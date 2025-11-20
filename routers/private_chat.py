from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, func
from datetime import datetime, timedelta
from typing import Optional

from db import get_db
from models import (
    User, PrivateChatConversation, PrivateChatMessage, Block,
    PrivateChatStatus, MessageStatus, UserPresence
)
from routers.dependencies import get_current_user
from config import (
    PRIVATE_CHAT_ENABLED,
    PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE,
    PRIVATE_CHAT_MAX_MESSAGE_LENGTH,
    PRIVATE_CHAT_MAX_MESSAGES_PER_BURST,
    PRIVATE_CHAT_BURST_WINDOW_SECONDS,
    TYPING_TIMEOUT_SECONDS
)
from utils.pusher_client import publish_chat_message_sync
from utils.onesignal_client import (
    send_push_notification_async,
    should_send_push,
    get_user_player_ids
)
from utils.message_sanitizer import sanitize_message
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/private-chat", tags=["Private Chat"])


class SendMessageRequest(BaseModel):
    recipient_id: int = Field(..., description="User ID of recipient")
    message: str = Field(..., min_length=1, max_length=PRIVATE_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(None, description="Client-provided ID for idempotency")


class AcceptRejectRequest(BaseModel):
    conversation_id: int
    action: str = Field(..., description="'accept' or 'reject'")


class BlockUserRequest(BaseModel):
    blocked_user_id: int = Field(..., description="User ID to block")


def check_blocked(db: Session, user1_id: int, user2_id: int) -> bool:
    """Check if user1 is blocked by user2 or vice versa"""
    block = db.query(Block).filter(
        or_(
            and_(Block.blocker_id == user1_id, Block.blocked_id == user2_id),
            and_(Block.blocker_id == user2_id, Block.blocked_id == user1_id)
        )
    ).first()
    return block is not None


def get_display_username(user: User) -> str:
    """Get display username with fallback logic"""
    if user.username and user.username.strip():
        return user.username
    if user.email:
        return user.email.split('@')[0]
    return f"User{user.account_id}"


def publish_to_pusher_private(conversation_id: int, message_id: int, sender_id: int, 
                               sender_username: str, message: str, created_at: datetime,
                               is_new_conversation: bool):
    """Background task to publish to Pusher"""
    try:
        channel = f"private-conversation-{conversation_id}"
        publish_chat_message_sync(
            channel,
            "new-message",
            {
                "conversation_id": conversation_id,
                "message_id": message_id,
                "sender_id": sender_id,
                "sender_username": sender_username,
                "message": message,
                "created_at": created_at.isoformat(),
                "is_new_conversation": is_new_conversation
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish private chat message to Pusher: {e}")


def send_push_if_needed_sync(recipient_id: int, conversation_id: int, sender_id: int,
                              sender_username: str, message: str, is_new_conversation: bool):
    """Background task wrapper to send push notification if user is not active"""
    import asyncio
    from db import get_db
    
    db = next(get_db())
    try:
        # Check if user is active (should not send push)
        if not should_send_push(recipient_id, db):
            logger.debug(f"User {recipient_id} is active, skipping push notification")
            return
        
        # Get player IDs
        player_ids = get_user_player_ids(recipient_id, db, valid_only=True)
        if not player_ids:
            logger.debug(f"No valid OneSignal players for user {recipient_id}")
            return
        
        if is_new_conversation:
            heading = "New Chat Request"
            content = f"{sender_username} wants to chat with you"
            data = {
                "type": "chat_request",
                "conversation_id": conversation_id,
                "sender_id": sender_id
            }
        else:
            heading = sender_username
            content = message[:100]  # Truncate for notification
            data = {
                "type": "private_message",
                "conversation_id": conversation_id,
                "sender_id": sender_id
            }
        
        # Run async function in event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        loop.run_until_complete(
            send_push_notification_async(
                player_ids=player_ids,
                heading=heading,
                content=content,
                data=data
            )
        )
    except Exception as e:
        logger.error(f"Failed to send push notification: {e}")
    finally:
        db.close()


@router.post("/send")
async def send_private_message(
    request: SendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send private message - creates conversation if needed"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    if request.recipient_id == current_user.account_id:
        raise HTTPException(status_code=400, detail="Cannot message yourself")
    
    # Check if blocked
    if check_blocked(db, current_user.account_id, request.recipient_id):
        raise HTTPException(status_code=403, detail="User is blocked")
    
    # Validate recipient exists
    recipient = db.query(User).filter(User.account_id == request.recipient_id).first()
    if not recipient:
        raise HTTPException(status_code=404, detail="Recipient not found")
    
    # Find or create conversation (sorted user IDs for consistency)
    user_ids = sorted([current_user.account_id, request.recipient_id])
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.user1_id == user_ids[0],
        PrivateChatConversation.user2_id == user_ids[1]
    ).first()
    
    is_new_conversation = False
    if not conversation:
        # New conversation - set status to pending
        conversation = PrivateChatConversation(
            user1_id=user_ids[0],
            user2_id=user_ids[1],
            requested_by=current_user.account_id,
            status='pending'
        )
        db.add(conversation)
        db.flush()
        is_new_conversation = True
    
    # Check if conversation is rejected
    if conversation.status == 'rejected':
        raise HTTPException(
            status_code=403,
            detail="User is not accepting private messages."
        )
    
    # Check if this is the first message in the conversation
    existing_message_count = db.query(PrivateChatMessage).filter(
        PrivateChatMessage.conversation_id == conversation.id
    ).count()
    
    is_first_message = existing_message_count == 0
    
    # Check if conversation is pending
    if conversation.status == 'pending':
        if conversation.requested_by != current_user.account_id:
            # Recipient trying to send message before accepting - must accept first
            raise HTTPException(
                status_code=403,
                detail="Chat request must be accepted before sending messages. Please accept the chat request first."
            )
        
        # If requester is sending and it's NOT the first message, block until accepted
        if conversation.requested_by == current_user.account_id and not is_first_message:
            raise HTTPException(
                status_code=403,
                detail="Chat request must be accepted before sending more messages. Please wait for the recipient to accept."
            )
        # First message from requester is always allowed (creates the request)
    
    # Sanitize message to prevent XSS
    sanitized_message = sanitize_message(request.message)
    if not sanitized_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # Check for duplicate message (idempotency)
    if request.client_message_id:
        existing_message = db.query(PrivateChatMessage).filter(
            PrivateChatMessage.conversation_id == conversation.id,
            PrivateChatMessage.sender_id == current_user.account_id,
            PrivateChatMessage.client_message_id == request.client_message_id
        ).first()
        
        if existing_message:
            logger.debug(f"Duplicate private chat message detected: {request.client_message_id}")
            return {
                "conversation_id": conversation.id,
                "message_id": existing_message.id,
                "status": conversation.status,
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True
            }
    
    # Burst rate limiting (3 messages per 3 seconds)
    burst_window_ago = datetime.utcnow() - timedelta(seconds=PRIVATE_CHAT_BURST_WINDOW_SECONDS)
    recent_burst = db.query(PrivateChatMessage).filter(
        PrivateChatMessage.sender_id == current_user.account_id,
        PrivateChatMessage.created_at >= burst_window_ago
    ).count()
    
    if recent_burst >= PRIVATE_CHAT_MAX_MESSAGES_PER_BURST:
        raise HTTPException(
            status_code=429,
            detail=f"Burst rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_BURST} messages per {PRIVATE_CHAT_BURST_WINDOW_SECONDS} seconds."
        )
    
    # Per-minute rate limiting
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
    recent_messages = db.query(PrivateChatMessage).filter(
        PrivateChatMessage.sender_id == current_user.account_id,
        PrivateChatMessage.created_at >= one_minute_ago
    ).count()
    
    if recent_messages >= PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
        )
    
    # Create message
    new_message = PrivateChatMessage(
        conversation_id=conversation.id,
        sender_id=current_user.account_id,
        message=sanitized_message,
        status='sent',
        client_message_id=request.client_message_id
    )
    
    db.add(new_message)
    conversation.last_message_at = datetime.utcnow()
    db.commit()
    db.refresh(new_message)
    
    # Publish to Pusher in background
    username = get_display_username(current_user)
    background_tasks.add_task(
        publish_to_pusher_private,
        conversation.id,
        new_message.id,
        current_user.account_id,
        username,
        new_message.message,
        new_message.created_at,
        is_new_conversation
    )
    
    # Send push notification in background (if user is not active)
    background_tasks.add_task(
        send_push_if_needed_sync,
        request.recipient_id,
        conversation.id,
        current_user.account_id,
        username,
        new_message.message,
        is_new_conversation
    )
    
    return {
        "conversation_id": conversation.id,
        "message_id": new_message.id,
        "status": conversation.status,
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False
    }


@router.post("/accept-reject")
async def accept_reject_chat(
    request: AcceptRejectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Accept or reject a chat request"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.id == request.conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Verify user is the recipient (not the requester)
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if current_user.account_id == conversation.requested_by:
        raise HTTPException(status_code=400, detail="Cannot accept/reject your own request")
    
    if conversation.status != 'pending':
        # Conversation already responded to - return current status
        return {
            "conversation_id": conversation.id,
            "status": conversation.status,
            "message": f"Conversation already {conversation.status}"
        }
    
    if request.action == "accept":
        conversation.status = 'accepted'
    elif request.action == "reject":
        conversation.status = 'rejected'
    else:
        raise HTTPException(status_code=400, detail="Invalid action. Use 'accept' or 'reject'")
    
    conversation.responded_at = datetime.utcnow()
    db.commit()
    
    # Notify requester via Pusher
    requester_id = conversation.requested_by
    background_tasks.add_task(
        publish_chat_message_sync,
        f"private-conversation-{conversation.id}",
        "conversation-updated",
        {
            "conversation_id": conversation.id,
            "status": conversation.status
        }
    )
    
    return {
        "conversation_id": conversation.id,
        "status": conversation.status
    }


@router.get("/conversations")
async def list_private_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all private chat conversations with unread counts"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    # Get conversations where user is participant
    # Include: accepted conversations OR pending conversations (requester can see their request, recipient can see requests to them)
    conversations = db.query(PrivateChatConversation).filter(
        or_(
            PrivateChatConversation.user1_id == current_user.account_id,
            PrivateChatConversation.user2_id == current_user.account_id
        ),
        or_(
            PrivateChatConversation.status == 'accepted',
            PrivateChatConversation.status == 'pending'
        )
    ).order_by(
        func.coalesce(PrivateChatConversation.last_message_at, PrivateChatConversation.created_at).desc()
    ).all()
    
    result = []
    for conv in conversations:
        # Determine peer user
        peer_id = conv.user2_id if conv.user1_id == current_user.account_id else conv.user1_id
        peer_user = db.query(User).filter(User.account_id == peer_id).first()
        
        if not peer_user:
            continue
        
        # Determine last_read_message_id for current user
        last_read_id = conv.last_read_message_id_user1 if conv.user1_id == current_user.account_id else conv.last_read_message_id_user2
        
        # Count unread messages (messages after last_read_id)
        if last_read_id:
            unread_count = db.query(PrivateChatMessage).filter(
                PrivateChatMessage.conversation_id == conv.id,
                PrivateChatMessage.sender_id != current_user.account_id,
                PrivateChatMessage.id > last_read_id
            ).count()
        else:
            # No read messages yet, count all messages from peer
            unread_count = db.query(PrivateChatMessage).filter(
                PrivateChatMessage.conversation_id == conv.id,
                PrivateChatMessage.sender_id != current_user.account_id
            ).count()
        
        # Get peer user's presence (last seen and online status)
        peer_presence = db.query(UserPresence).filter(
            UserPresence.user_id == peer_id
        ).first()
        
        # Check privacy settings (simplified - respect share_online and share_last_seen)
        peer_online = False
        peer_last_seen = None
        
        if peer_presence:
            privacy = peer_presence.privacy_settings or {}
            share_online = privacy.get("share_online", True)
            share_last_seen = privacy.get("share_last_seen", "contacts")
            
            # For now, assume if user is in conversation, they're a contact
            # TODO: Implement proper contact/friend checking
            if share_online:
                peer_online = peer_presence.device_online
            
            if share_last_seen in ["everyone", "contacts"]:
                peer_last_seen = peer_presence.last_seen_at.isoformat() if peer_presence.last_seen_at else None
        
        result.append({
            "conversation_id": conv.id,
            "peer_user_id": peer_id,
            "peer_username": get_display_username(peer_user),
            "peer_profile_pic": peer_user.profile_pic_url,
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            "unread_count": unread_count,
            "peer_online": peer_online,
            "peer_last_seen": peer_last_seen
        })
    
    return {"conversations": result}


@router.get("/conversations/{conversation_id}/messages")
async def get_private_messages(
    conversation_id: int,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get messages from a private conversation with read status"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.id == conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Check if conversation is accepted - recipient cannot view messages until accepted
    if conversation.status == 'pending':
        if conversation.requested_by != current_user.account_id:
            # Recipient trying to view messages before accepting
            raise HTTPException(
                status_code=403,
                detail="Chat request must be accepted before viewing messages. Please accept the chat request first."
            )
    
    # Get last_read_message_id for current user
    last_read_id = conversation.last_read_message_id_user1 if conversation.user1_id == current_user.account_id else conversation.last_read_message_id_user2
    
    messages = db.query(PrivateChatMessage).filter(
        PrivateChatMessage.conversation_id == conversation_id
    ).order_by(PrivateChatMessage.created_at.desc()).limit(limit).all()
    
    # Get peer user's presence (last seen and online status)
    peer_id = conversation.user2_id if conversation.user1_id == current_user.account_id else conversation.user1_id
    peer_presence = db.query(UserPresence).filter(
        UserPresence.user_id == peer_id
    ).first()
    
    # Check privacy settings (simplified - respect share_online and share_last_seen)
    peer_online = False
    peer_last_seen = None
    
    if peer_presence:
        privacy = peer_presence.privacy_settings or {}
        share_online = privacy.get("share_online", True)
        share_last_seen = privacy.get("share_last_seen", "contacts")
        
        # For now, assume if user is in conversation, they're a contact
        # TODO: Implement proper contact/friend checking
        if share_online:
            peer_online = peer_presence.device_online
        
        if share_last_seen in ["everyone", "contacts"]:
            peer_last_seen = peer_presence.last_seen_at.isoformat() if peer_presence.last_seen_at else None
    
    return {
        "messages": [
            {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "sender_username": get_display_username(msg.sender),
                "message": msg.message,
                "status": msg.status,
                "created_at": msg.created_at.isoformat(),
                "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
                "is_read": last_read_id is not None and msg.id <= last_read_id if msg.sender_id != current_user.account_id else None
            }
            for msg in reversed(messages)
        ],
        "peer_online": peer_online,
        "peer_last_seen": peer_last_seen
    }


@router.post("/conversations/{conversation_id}/mark-read")
async def mark_conversation_read(
    conversation_id: int,
    message_id: Optional[int] = Query(None, description="Message ID to mark as read up to (defaults to latest)"),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mark conversation as read up to a specific message ID"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.id == conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # If message_id not provided, use the latest message ID
    if message_id is None:
        latest_message = db.query(PrivateChatMessage).filter(
            PrivateChatMessage.conversation_id == conversation_id
        ).order_by(PrivateChatMessage.id.desc()).first()
        
        if latest_message:
            message_id = latest_message.id
        else:
            # No messages, nothing to mark as read
            return {"conversation_id": conversation_id, "last_read_message_id": None}
    
    # Update last_read_message_id for current user
    if conversation.user1_id == current_user.account_id:
        conversation.last_read_message_id_user1 = message_id
    else:
        conversation.last_read_message_id_user2 = message_id
    
    db.commit()
    
    # Notify other participant via Pusher
    peer_id = conversation.user2_id if conversation.user1_id == current_user.account_id else conversation.user1_id
    if background_tasks is not None:
        background_tasks.add_task(
            publish_chat_message_sync,
            f"private-conversation-{conversation_id}",
            "messages-read",
            {
                "conversation_id": conversation_id,
                "reader_id": current_user.account_id,
                "last_read_message_id": message_id
            }
        )
    
    return {
        "conversation_id": conversation_id,
        "last_read_message_id": message_id
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get conversation details"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.id == conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    peer_id = conversation.user2_id if conversation.user1_id == current_user.account_id else conversation.user1_id
    peer_user = db.query(User).filter(User.account_id == peer_id).first()
    
    # Get peer user's presence (last seen and online status)
    peer_presence = db.query(UserPresence).filter(
        UserPresence.user_id == peer_id
    ).first()
    
    # Check privacy settings (simplified - respect share_online and share_last_seen)
    peer_online = False
    peer_last_seen = None
    
    if peer_presence:
        privacy = peer_presence.privacy_settings or {}
        share_online = privacy.get("share_online", True)
        share_last_seen = privacy.get("share_last_seen", "contacts")
        
        # For now, assume if user is in conversation, they're a contact
        # TODO: Implement proper contact/friend checking
        if share_online:
            peer_online = peer_presence.device_online
        
        if share_last_seen in ["everyone", "contacts"]:
            peer_last_seen = peer_presence.last_seen_at.isoformat() if peer_presence.last_seen_at else None
    
    return {
        "conversation_id": conversation.id,
        "peer_user_id": peer_id,
        "peer_username": get_display_username(peer_user) if peer_user else None,
        "peer_profile_pic": peer_user.profile_pic_url if peer_user else None,
        "status": conversation.status,
        "created_at": conversation.created_at.isoformat(),
        "peer_online": peer_online,
        "peer_last_seen": peer_last_seen,
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None
    }


@router.post("/conversations/{conversation_id}/typing")
async def send_typing_indicator(
    conversation_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send typing indicator to conversation"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.id == conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    if conversation.status != 'accepted':
        raise HTTPException(status_code=403, detail="Conversation not accepted")
    
    # Publish typing event via Pusher
    username = get_display_username(current_user)
    background_tasks.add_task(
        publish_chat_message_sync,
        f"private-conversation-{conversation_id}",
        "typing",
        {
            "conversation_id": conversation_id,
            "user_id": current_user.account_id,
            "username": username
        }
    )
    
    return {"status": "typing"}


@router.post("/conversations/{conversation_id}/typing-stop")
async def send_typing_stop(
    conversation_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send typing stop indicator to conversation"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.id == conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Publish typing-stop event via Pusher
    background_tasks.add_task(
        publish_chat_message_sync,
        f"private-conversation-{conversation_id}",
        "typing-stop",
        {
            "conversation_id": conversation_id,
            "user_id": current_user.account_id
        }
    )
    
    return {"status": "stopped"}


@router.post("/messages/{message_id}/mark-delivered")
async def mark_message_delivered(
    message_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Mark a message as delivered"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    message = db.query(PrivateChatMessage).filter(
        PrivateChatMessage.id == message_id
    ).first()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Verify user is in the conversation
    conversation = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.id == message.conversation_id
    ).first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Only mark as delivered if message is not from current user
    if message.sender_id == current_user.account_id:
        raise HTTPException(status_code=400, detail="Cannot mark own message as delivered")
    
    # Update message status if not already delivered/seen
    if message.status == 'sent':
        message.status = 'delivered'
        message.delivered_at = datetime.utcnow()
        db.commit()
        
        # Notify sender via Pusher
        background_tasks.add_task(
            publish_chat_message_sync,
            f"private-conversation-{conversation.id}",
            "message-delivered",
            {
                "conversation_id": conversation.id,
                "message_id": message_id,
                "delivered_at": message.delivered_at.isoformat()
            }
        )
    
    return {
        "message_id": message_id,
        "status": message.status,
        "delivered_at": message.delivered_at.isoformat() if message.delivered_at else None
    }


@router.post("/block")
async def block_user(
    request: BlockUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Block a user from sending private messages"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    if request.blocked_user_id == current_user.account_id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    
    # Validate blocked user exists
    blocked_user = db.query(User).filter(User.account_id == request.blocked_user_id).first()
    if not blocked_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if already blocked
    existing_block = db.query(Block).filter(
        Block.blocker_id == current_user.account_id,
        Block.blocked_id == request.blocked_user_id
    ).first()
    
    if existing_block:
        return {
            "success": True,
            "message": "User already blocked"
        }
    
    # Create block
    new_block = Block(
        blocker_id=current_user.account_id,
        blocked_id=request.blocked_user_id,
        created_at=datetime.utcnow()
    )
    db.add(new_block)
    
    # Reject any pending conversations
    user_ids = sorted([current_user.account_id, request.blocked_user_id])
    pending_conversations = db.query(PrivateChatConversation).filter(
        PrivateChatConversation.user1_id == user_ids[0],
        PrivateChatConversation.user2_id == user_ids[1],
        PrivateChatConversation.status == 'pending'
    ).all()
    
    for conv in pending_conversations:
        conv.status = 'rejected'
        conv.responded_at = datetime.utcnow()
    
    db.commit()
    
    logger.info(f"User {current_user.account_id} blocked user {request.blocked_user_id}")
    
    return {
        "success": True,
        "message": "User blocked successfully"
    }


@router.delete("/block/{blocked_user_id}")
async def unblock_user(
    blocked_user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Unblock a user"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    block = db.query(Block).filter(
        Block.blocker_id == current_user.account_id,
        Block.blocked_id == blocked_user_id
    ).first()
    
    if not block:
        raise HTTPException(status_code=404, detail="User is not blocked")
    
    db.delete(block)
    db.commit()
    
    logger.info(f"User {current_user.account_id} unblocked user {blocked_user_id}")
    
    return {
        "success": True,
        "message": "User unblocked successfully"
    }


@router.get("/blocks")
async def list_blocks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all users blocked by the current user"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")
    
    blocks = db.query(Block).filter(
        Block.blocker_id == current_user.account_id
    ).order_by(Block.created_at.desc()).all()
    
    blocked_users = []
    for block in blocks:
        blocked_user = db.query(User).filter(User.account_id == block.blocked_id).first()
        if blocked_user:
            blocked_users.append({
                "user_id": blocked_user.account_id,
                "username": get_display_username(blocked_user),
                "blocked_at": block.created_at.isoformat()
            })
    
    return {
        "blocked_users": blocked_users
    }

