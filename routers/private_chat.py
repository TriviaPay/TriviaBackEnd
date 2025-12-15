from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func
from datetime import datetime, timedelta
from typing import Optional, Union, Tuple

from db import get_db
from models import (
    User, PrivateChatConversation, PrivateChatMessage, Block,
    PrivateChatStatus, MessageStatus, UserPresence, Avatar, Frame, TriviaModeConfig, UserSubscription, SubscriptionPlan
)
from routers.dependencies import get_current_user
from config import (
    PRIVATE_CHAT_ENABLED,
    PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE,
    PRIVATE_CHAT_MAX_MESSAGE_LENGTH,
    PRIVATE_CHAT_MAX_MESSAGES_PER_BURST,
    PRIVATE_CHAT_BURST_WINDOW_SECONDS,
    TYPING_TIMEOUT_SECONDS,
    PRESENCE_ENABLED
)
from utils.pusher_client import publish_chat_message_sync
from utils.onesignal_client import (
    send_push_notification_async,
    should_send_push,
    get_user_player_ids,
    is_user_active
)
from utils.chat_mute import is_user_muted_for_private_chat
from utils.message_sanitizer import sanitize_message
from utils.chat_helpers import get_user_chat_profile_data
from utils.storage import presign_get
from utils.user_level_service import get_level_progress
from utils.chat_redis import (
    check_burst_limit,
    check_rate_limit,
    enqueue_chat_event,
    should_emit_typing_event,
    clear_typing_event
)
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/private-chat", tags=["Private Chat"])


class SendMessageRequest(BaseModel):
    recipient_id: int = Field(..., description="User ID of recipient")
    message: str = Field(..., min_length=1, max_length=PRIVATE_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(None, description="Client-provided ID for idempotency")
    reply_to_message_id: Optional[int] = Field(None, description="ID of message being replied to")


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


def get_user_presence_info(db: Session, user_id: int, viewer_id: int, conversation_id: Optional[int] = None) -> Tuple[bool, Optional[str]]:
    """
    Get user's online status and last seen time, respecting privacy settings.
    Returns (is_online, last_seen_at_iso) tuple.
    Creates default presence if it doesn't exist.
    Falls back to last message time if last_seen_at is None.
    """
    if not PRESENCE_ENABLED:
        return False, None
    
    presence = db.query(UserPresence).filter(
        UserPresence.user_id == user_id
    ).first()
    
    # Create default presence if it doesn't exist
    if not presence:
        presence = UserPresence(
            user_id=user_id,
            privacy_settings={
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True
            },
            device_online=False,
            last_seen_at=None
        )
        db.add(presence)
        db.commit()
        db.refresh(presence)
    
    # Check privacy settings
    privacy = presence.privacy_settings or {}
    share_online = privacy.get("share_online", True)
    share_last_seen = privacy.get("share_last_seen", "contacts")
    
    # For now, assume if user is in conversation, they're a contact
    # TODO: Implement proper contact/friend checking
    is_online = False
    last_seen = None
    
    if share_online:
        is_online = presence.device_online
    
    if share_last_seen in ["everyone", "contacts"]:
        if presence.last_seen_at:
            last_seen = presence.last_seen_at.isoformat()
        else:
            # Fallback: use last message time if last_seen_at is None
            if conversation_id:
                last_message = db.query(PrivateChatMessage).filter(
                    PrivateChatMessage.conversation_id == conversation_id,
                    PrivateChatMessage.sender_id == user_id
                ).order_by(PrivateChatMessage.created_at.desc()).first()
                if last_message:
                    last_seen = last_message.created_at.isoformat()
            else:
                # Try to find any recent message from this user
                last_message = db.query(PrivateChatMessage).filter(
                    PrivateChatMessage.sender_id == user_id
                ).order_by(PrivateChatMessage.created_at.desc()).first()
                if last_message:
                    last_seen = last_message.created_at.isoformat()
    
    return is_online, last_seen


def _ensure_datetime(value: Union[datetime, str]) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.utcnow()


def publish_to_pusher_private(conversation_id: int, message_id: int, sender_id: int, 
                               sender_username: str, profile_pic_url: Optional[str],
                               avatar_url: Optional[str], frame_url: Optional[str], badge: Optional[dict],
                               message: str, created_at: Union[datetime, str],
                               is_new_conversation: bool, reply_to: Optional[dict] = None):
    """Background task to publish to Pusher"""
    try:
        created_at_dt = _ensure_datetime(created_at)
        channel = f"private-conversation-{conversation_id}"
        event_data = {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_username": sender_username,
            "profile_pic": profile_pic_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "badge": badge,
            "message": message,
            "created_at": created_at_dt.isoformat(),
            "is_new_conversation": is_new_conversation
        }
        if reply_to:
            event_data["reply_to"] = reply_to
        publish_chat_message_sync(channel, "new-message", event_data)
    except Exception as e:
        logger.error(f"Failed to publish private chat message to Pusher: {e}")


def send_push_if_needed_sync(recipient_id: int, conversation_id: int, sender_id: int,
                              sender_username: str, message: str, is_new_conversation: bool):
    """Background task wrapper to send push notification (in-app if active, system if inactive)"""
    import asyncio
    from db import get_db
    from utils.notification_storage import create_notification
    
    db = next(get_db())
    try:
        # Check if sender is muted by recipient
        if is_user_muted_for_private_chat(sender_id, recipient_id, db):
            logger.debug(f"User {sender_id} is muted by {recipient_id}, skipping push notification")
            return
        
        # Get player IDs
        player_ids = get_user_player_ids(recipient_id, db, valid_only=True)
        if not player_ids:
            logger.debug(f"No valid OneSignal players for user {recipient_id}")
            return
        
        # Check if user is active (determines in-app vs system notification)
        is_active = is_user_active(recipient_id, db)
        
        logger.info(
            f"Preparing push notification | recipient_id={recipient_id} | sender_id={sender_id} | "
            f"is_active={is_active} | is_new_conversation={is_new_conversation} | "
            f"player_count={len(player_ids)}"
        )
        
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
                data=data,
                is_in_app_notification=is_active
            )
        )
        
        # Store notification in database
        create_notification(
            db=db,
            user_id=recipient_id,
            title=heading,
            body=content,
            notification_type="chat_private" if not is_new_conversation else "chat_request",
            data=data
        )
        
        notification_type = "in-app" if is_active else "system"
        logger.info(
            f"Sent {notification_type} push notification | recipient_id={recipient_id} | "
            f"sender_id={sender_id} | is_in_app={is_active} | player_count={len(player_ids)}"
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
    
    # Burst rate limiting via Redis (fallback to DB)
    burst_allowed = await check_burst_limit(
        "private",
        current_user.account_id,
        PRIVATE_CHAT_MAX_MESSAGES_PER_BURST,
        PRIVATE_CHAT_BURST_WINDOW_SECONDS
    )
    if burst_allowed is False:
        raise HTTPException(
            status_code=429,
            detail=f"Burst rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_BURST} messages per {PRIVATE_CHAT_BURST_WINDOW_SECONDS} seconds."
        )
    if burst_allowed is None:
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
    minute_allowed = await check_rate_limit(
        "private",
        current_user.account_id,
        PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE,
        60
    )
    if minute_allowed is False:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
        )
    if minute_allowed is None:
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
    
    # Validate reply_to_message_id if provided
    reply_to_message = None
    if request.reply_to_message_id:
        reply_to_message = db.query(PrivateChatMessage).filter(
            PrivateChatMessage.id == request.reply_to_message_id,
            PrivateChatMessage.conversation_id == conversation.id
        ).first()
        if not reply_to_message:
            raise HTTPException(
                status_code=404,
                detail=f"Message {request.reply_to_message_id} not found in this conversation"
            )
    
    # Create message
    new_message = PrivateChatMessage(
        conversation_id=conversation.id,
        sender_id=current_user.account_id,
        message=sanitized_message,
        status='sent',
        client_message_id=request.client_message_id,
        reply_to_message_id=request.reply_to_message_id  # Use directly since validation ensures it exists if provided
    )
    
    db.add(new_message)
    conversation.last_message_at = datetime.utcnow()
    
    # Update sender's presence (last_seen_at) when they send a message
    if PRESENCE_ENABLED:
        sender_presence = db.query(UserPresence).filter(
            UserPresence.user_id == current_user.account_id
        ).first()
        if sender_presence:
            sender_presence.last_seen_at = datetime.utcnow()
        else:
            sender_presence = UserPresence(
                user_id=current_user.account_id,
                last_seen_at=datetime.utcnow(),
                device_online=False,
                privacy_settings={
                    "share_last_seen": "contacts",
                    "share_online": True,
                    "read_receipts": True
                }
            )
            db.add(sender_presence)
    
    db.commit()
    db.refresh(new_message)
    
    # Get user profile data (avatar, frame)
    profile_data = get_user_chat_profile_data(current_user, db)
    
    # Get reply information if this is a reply
    reply_info = None
    if reply_to_message:
        replied_sender_profile = get_user_chat_profile_data(reply_to_message.sender, db)
        reply_info = {
            "message_id": reply_to_message.id,
            "sender_id": reply_to_message.sender_id,
            "sender_username": get_display_username(reply_to_message.sender),
            "message": reply_to_message.message,
            "sender_profile_pic": replied_sender_profile["profile_pic_url"],
            "sender_avatar_url": replied_sender_profile["avatar_url"],
            "sender_frame_url": replied_sender_profile["frame_url"],
            "sender_badge": replied_sender_profile["badge"],
            "created_at": reply_to_message.created_at.isoformat()
        }
    
    # Publish to Pusher via Redis queue (fallback to inline background tasks)
    username = get_display_username(current_user)
    event_enqueued = await enqueue_chat_event(
        "private_message",
        {
            "pusher_args": {
                "conversation_id": conversation.id,
                "message_id": new_message.id,
                "sender_id": current_user.account_id,
                "sender_username": username,
                "profile_pic_url": profile_data["profile_pic_url"],
                "avatar_url": profile_data["avatar_url"],
                "frame_url": profile_data["frame_url"],
                "badge": profile_data["badge"],
                "message": new_message.message,
                "created_at": new_message.created_at.isoformat(),
                "is_new_conversation": is_new_conversation,
                "reply_to": reply_info
            },
            "push_args": {
                "recipient_id": request.recipient_id,
                "conversation_id": conversation.id,
                "sender_id": current_user.account_id,
                "sender_username": username,
                "message": new_message.message,
                "is_new_conversation": is_new_conversation
            }
        }
    )
    
    if not event_enqueued:
        background_tasks.add_task(
            publish_to_pusher_private,
            conversation.id,
            new_message.id,
            current_user.account_id,
            username,
            profile_data["profile_pic_url"],
            profile_data["avatar_url"],
            profile_data["frame_url"],
            profile_data["badge"],
            new_message.message,
            new_message.created_at,
            is_new_conversation,
            reply_info
        )
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
        peer_online, peer_last_seen = get_user_presence_info(db, peer_id, current_user.account_id, conv.id)
        
        # Get peer user's profile data (avatar, frame)
        peer_profile_data = get_user_chat_profile_data(peer_user, db)
        
        result.append({
            "conversation_id": conv.id,
            "peer_user_id": peer_id,
            "peer_username": get_display_username(peer_user),
            "peer_profile_pic": peer_profile_data["profile_pic_url"],
            "peer_avatar_url": peer_profile_data["avatar_url"],
            "peer_frame_url": peer_profile_data["frame_url"],
            "peer_badge": peer_profile_data["badge"],
            "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
            "unread_count": unread_count,
            "peer_online": peer_online,
            "peer_last_seen": peer_last_seen
        })
    
    return {"conversations": result}


def _batch_get_user_profile_data(users: list[User], db: Session) -> dict[int, dict]:
    """
    Batch load profile data for multiple users to avoid N+1 queries.
    Returns a dict mapping user_id -> profile_data.
    """
    if not users:
        return {}
    
    user_ids = [u.account_id for u in users]
    profile_cache = {}
    
    # Batch load all avatars
    avatar_ids = {u.selected_avatar_id for u in users if u.selected_avatar_id}
    avatars = {}
    if avatar_ids:
        avatars = {a.id: a for a in db.query(Avatar).filter(Avatar.id.in_(list(avatar_ids))).all()}
    
    # Batch load all frames
    frame_ids = {u.selected_frame_id for u in users if u.selected_frame_id}
    frames = {}
    if frame_ids:
        frames = {f.id: f for f in db.query(Frame).filter(Frame.id.in_(list(frame_ids))).all()}
    
    # Batch load all badges (now from TriviaModeConfig)
    badge_ids = {u.badge_id for u in users if u.badge_id}
    badges = {}
    if badge_ids:
        mode_configs = db.query(TriviaModeConfig).filter(
            TriviaModeConfig.mode_id.in_(list(badge_ids)),
            TriviaModeConfig.badge_image_url.isnot(None)
        ).all()
        badges = {mc.mode_id: mc for mc in mode_configs}
    
    # Batch load all active subscriptions with plans eagerly loaded
    active_subscriptions = {}
    if user_ids:
        subs = db.query(UserSubscription).options(joinedload(UserSubscription.plan)).join(SubscriptionPlan).filter(
            and_(
                UserSubscription.user_id.in_(list(user_ids)),
                UserSubscription.status == 'active',
                UserSubscription.current_period_end > datetime.utcnow()
            )
        ).all()
        for sub in subs:
            if sub.user_id not in active_subscriptions:
                active_subscriptions[sub.user_id] = []
            active_subscriptions[sub.user_id].append(sub)
    
    # Batch load subscription badges (bronze and silver) from TriviaModeConfig
    subscription_badge_ids = ['bronze', 'bronze_badge', 'brone_badge', 'brone', 'silver', 'silver_badge']
    subscription_badges_dict = {mc.mode_id: mc for mc in db.query(TriviaModeConfig).filter(
        TriviaModeConfig.mode_id.in_(list(subscription_badge_ids)),
        TriviaModeConfig.badge_image_url.isnot(None)
    ).all()}
    # Also try name-based matching
    name_based_badges = {mc.mode_id: mc for mc in db.query(TriviaModeConfig).filter(
        (TriviaModeConfig.mode_name.ilike('%bronze%') | TriviaModeConfig.mode_name.ilike('%silver%')),
        TriviaModeConfig.badge_image_url.isnot(None)
    ).all()}
    subscription_badges_dict.update(name_based_badges)
    
    # Generate presigned URLs in batch
    presigned_avatars = {}
    presigned_frames = {}
    for avatar_id, avatar in avatars.items():
        bucket = getattr(avatar, "bucket", None)
        object_key = getattr(avatar, "object_key", None)
        if bucket and object_key:
            try:
                presigned_avatars[avatar_id] = presign_get(bucket, object_key, expires=900)
            except Exception as e:
                logger.warning(f"Failed to presign avatar {avatar_id}: {e}")
    
    for frame_id, frame in frames.items():
        bucket = getattr(frame, "bucket", None)
        object_key = getattr(frame, "object_key", None)
        if bucket and object_key:
            try:
                presigned_frames[frame_id] = presign_get(bucket, object_key, expires=900)
            except Exception as e:
                logger.warning(f"Failed to presign frame {frame_id}: {e}")
    
    # Build profile data for each user
    for user in users:
        # Avatar URL
        avatar_url = None
        if user.selected_avatar_id and user.selected_avatar_id in presigned_avatars:
            avatar_url = presigned_avatars[user.selected_avatar_id]
        
        # Frame URL
        frame_url = None
        if user.selected_frame_id and user.selected_frame_id in presigned_frames:
            frame_url = presigned_frames[user.selected_frame_id]
        
        # Badge info
        badge_info = None
        if user.badge_id and user.badge_id in badges:
            mode_config = badges[user.badge_id]
            badge_info = {
                "id": mode_config.mode_id,
                "name": mode_config.mode_name,
                "image_url": mode_config.badge_image_url
            }
        
        # Subscription badges
        subscription_badges = []
        user_subs = active_subscriptions.get(user.account_id, [])
        for sub in user_subs:
            plan = sub.plan  # Use 'plan' relationship, not 'subscription_plan'
            if not plan:
                continue
            
            # Check for bronze ($5)
            if (getattr(plan, 'unit_amount_minor', None) == 500 or 
                getattr(plan, 'price_usd', None) == 5.0):
                bronze_badge = (subscription_badges_dict.get('bronze') or 
                              subscription_badges_dict.get('bronze_badge') or
                              subscription_badges_dict.get('brone_badge') or
                              subscription_badges_dict.get('brone'))
                if not bronze_badge:
                    # Try name-based match
                    for bid, mc in subscription_badges_dict.items():
                        if 'bronze' in mc.mode_name.lower():
                            bronze_badge = mc
                            break
                if bronze_badge and bronze_badge.badge_image_url:
                    subscription_badges.append({
                        "id": bronze_badge.mode_id,
                        "name": bronze_badge.mode_name,
                        "image_url": bronze_badge.badge_image_url,
                        "subscription_type": "bronze",
                        "price": 5.0
                    })
            
            # Check for silver ($10)
            if (getattr(plan, 'unit_amount_minor', None) == 1000 or 
                getattr(plan, 'price_usd', None) == 10.0):
                silver_badge = (subscription_badges_dict.get('silver') or 
                              subscription_badges_dict.get('silver_badge'))
                if not silver_badge:
                    # Try name-based match
                    for bid, mc in subscription_badges_dict.items():
                        if 'silver' in mc.mode_name.lower():
                            silver_badge = mc
                            break
                if silver_badge and silver_badge.badge_image_url:
                    subscription_badges.append({
                        "id": silver_badge.mode_id,
                        "name": silver_badge.mode_name,
                        "image_url": silver_badge.badge_image_url,
                        "subscription_type": "silver",
                        "price": 10.0
                    })
        
        # Level progress
        level_progress = get_level_progress(user, db)
        
        profile_cache[user.account_id] = {
            "profile_pic_url": user.profile_pic_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "badge": badge_info,
            "subscription_badges": subscription_badges,
            "level": level_progress['level'],
            "level_progress": level_progress['progress']
        }
    
    return profile_cache


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
    
    # Eagerly load senders to avoid N+1 queries
    messages = db.query(PrivateChatMessage).options(joinedload(PrivateChatMessage.sender)).filter(
        PrivateChatMessage.conversation_id == conversation_id
    ).order_by(PrivateChatMessage.created_at.desc()).limit(limit).all()
    
    # Get peer user's presence (last seen and online status)
    peer_id = conversation.user2_id if conversation.user1_id == current_user.account_id else conversation.user1_id
    peer_online, peer_last_seen = get_user_presence_info(db, peer_id, current_user.account_id, conversation_id)
    
    # Collect all unique users and reply message IDs
    unique_users = {msg.sender for msg in messages if msg.sender}
    reply_message_ids = {msg.reply_to_message_id for msg in messages if msg.reply_to_message_id}
    
    # Batch load all replied messages with their senders
    replied_messages = {}
    if reply_message_ids:
        replied_msgs = db.query(PrivateChatMessage).options(joinedload(PrivateChatMessage.sender)).filter(
            PrivateChatMessage.id.in_(list(reply_message_ids)),
            PrivateChatMessage.conversation_id == conversation_id
        ).all()
        replied_messages = {msg.id: msg for msg in replied_msgs}
        # Add replied message senders to unique_users set
        unique_users.update({msg.sender for msg in replied_msgs if msg.sender})
    
    # Batch load profile data for all unique users
    profile_cache = _batch_get_user_profile_data(list(unique_users), db)
    
    # Build response
    result_messages = []
    for msg in reversed(messages):
        sender_profile_data = profile_cache.get(msg.sender_id, {
            "profile_pic_url": None,
            "avatar_url": None,
            "frame_url": None,
            "badge": None,
            "subscription_badges": [],
            "level": 1,
            "level_progress": "0/100"
        })
        is_read = last_read_id is not None and msg.id <= last_read_id if msg.sender_id != current_user.account_id else None
        
        # Get reply information if this message is a reply
        reply_info = None
        if msg.reply_to_message_id and msg.reply_to_message_id in replied_messages:
            replied_msg = replied_messages[msg.reply_to_message_id]
            replied_profile = profile_cache.get(replied_msg.sender_id, {
                "profile_pic_url": None,
                "avatar_url": None,
                "frame_url": None,
                "badge": None,
                "subscription_badges": [],
                "level": 1,
                "level_progress": "0/100"
            })
            reply_info = {
                "message_id": replied_msg.id,
                "sender_id": replied_msg.sender_id,
                "sender_username": get_display_username(replied_msg.sender),
                "message": replied_msg.message,
                "sender_profile_pic": replied_profile["profile_pic_url"],
                "sender_avatar_url": replied_profile["avatar_url"],
                "sender_frame_url": replied_profile["frame_url"],
                "sender_badge": replied_profile["badge"],
                "created_at": replied_msg.created_at.isoformat(),
                "sender_level": replied_profile.get("level", 1),
                "sender_level_progress": replied_profile.get("level_progress", "0/100")
            }
        
        result_messages.append({
            "id": msg.id,
            "sender_id": msg.sender_id,
            "sender_username": get_display_username(msg.sender),
            "sender_profile_pic": sender_profile_data["profile_pic_url"],
            "sender_avatar_url": sender_profile_data["avatar_url"],
            "sender_frame_url": sender_profile_data["frame_url"],
            "sender_badge": sender_profile_data["badge"],
            "message": msg.message,
            "status": msg.status,
            "created_at": msg.created_at.isoformat(),
            "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
            "is_read": is_read,
            "reply_to": reply_info,
            "sender_level": sender_profile_data.get("level", 1),
            "sender_level_progress": sender_profile_data.get("level_progress", "0/100")
        })
    
    return {
        "messages": result_messages,
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
    
    # Update current user's presence when they view a conversation
    if PRESENCE_ENABLED:
        current_user_presence = db.query(UserPresence).filter(
            UserPresence.user_id == current_user.account_id
        ).first()
        if current_user_presence:
            current_user_presence.last_seen_at = datetime.utcnow()
        else:
            current_user_presence = UserPresence(
                user_id=current_user.account_id,
                last_seen_at=datetime.utcnow(),
                device_online=False,
                privacy_settings={
                    "share_last_seen": "contacts",
                    "share_online": True,
                    "read_receipts": True
                }
            )
            db.add(current_user_presence)
        db.commit()
    
    # Get peer user's presence (last seen and online status)
    peer_online, peer_last_seen = get_user_presence_info(db, peer_id, current_user.account_id, conversation_id)
    
    # Get peer user's profile data (avatar, frame)
    peer_profile_data = get_user_chat_profile_data(peer_user, db) if peer_user else {
        "profile_pic_url": None,
        "avatar_url": None,
        "frame_url": None
    }
    
    return {
        "conversation_id": conversation.id,
        "peer_user_id": peer_id,
        "peer_username": get_display_username(peer_user) if peer_user else None,
        "peer_profile_pic": peer_profile_data["profile_pic_url"],
        "peer_avatar_url": peer_profile_data["avatar_url"],
        "peer_frame_url": peer_profile_data["frame_url"],
        "peer_badge": peer_profile_data["badge"],
        "status": conversation.status,
        "created_at": conversation.created_at.isoformat(),
        "peer_online": peer_online,
        "peer_last_seen": peer_last_seen,
        "last_message_at": conversation.last_message_at.isoformat() if conversation.last_message_at else None,
        "peer_level": peer_profile_data.get("level", 1) if peer_user else None,
        "peer_level_progress": peer_profile_data.get("level_progress", "0/100") if peer_user else None
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
    
    channel_key = f"conversation:{conversation_id}"
    should_emit = await should_emit_typing_event(channel_key, current_user.account_id)
    if not should_emit:
        return {"status": "typing"}
    
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
    
    channel_key = f"conversation:{conversation_id}"
    await clear_typing_event(channel_key, current_user.account_id)
    
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
