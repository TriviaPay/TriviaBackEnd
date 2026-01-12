import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from config import (
    PRESENCE_ENABLED,
    PRIVATE_CHAT_BURST_WINDOW_SECONDS,
    PRIVATE_CHAT_ENABLED,
    PRIVATE_CHAT_MAX_MESSAGE_LENGTH,
    PRIVATE_CHAT_MAX_MESSAGES_PER_BURST,
    PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE,
    TYPING_TIMEOUT_SECONDS,
)
from db import get_db
from models import (
    Avatar,
    AdminUser,
    Block,
    Frame,
    MessageStatus,
    PrivateChatConversation,
    PrivateChatMessage,
    PrivateChatStatus,
    SubscriptionPlan,
    TriviaModeConfig,
    User,
    UserPresence,
    UserSubscription,
)
from routers.dependencies import get_current_user
from utils.chat_blocking import check_blocked
from utils.chat_helpers import (
    get_user_chat_profile_data,
    get_user_chat_profile_data_bulk,
)
from utils.chat_mute import is_user_muted_for_private_chat
from utils.chat_redis import (
    check_burst_limit,
    check_rate_limit,
    clear_typing_event,
    enqueue_chat_event,
    should_emit_typing_event,
)
from utils.message_sanitizer import sanitize_message
from utils.onesignal_client import (
    get_user_player_ids,
    is_user_active,
    send_push_notification_async,
    should_send_push,
)
from utils.pusher_client import publish_chat_message_sync
from utils.storage import presign_get

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/private-chat", tags=["Private Chat"])


def _get_admin_user_id(db: Session) -> Optional[int]:
    admin_entry = db.query(AdminUser).first()
    return admin_entry.user_id if admin_entry else None


class SendMessageRequest(BaseModel):
    recipient_id: int = Field(..., description="User ID of recipient")
    message: str = Field(..., min_length=1, max_length=PRIVATE_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(
        None, description="Client-provided ID for idempotency"
    )
    reply_to_message_id: Optional[int] = Field(
        None, description="ID of message being replied to"
    )


class AcceptRejectRequest(BaseModel):
    conversation_id: int
    action: str = Field(..., description="'accept' or 'reject'")


from .schemas import PrivateChatBlockUserRequest
from .service import (
    block_private_chat_user as service_block_private_chat_user,
    list_private_chat_blocks as service_list_private_chat_blocks,
    unblock_private_chat_user as service_unblock_private_chat_user,
)


def get_display_username(user: User) -> str:
    """Get display username with fallback logic"""
    if user.username and user.username.strip():
        return user.username
    if user.email:
        return user.email.split("@")[0]
    return f"User{user.account_id}"


def get_user_presence_info(
    db: Session, user_id: int, viewer_id: int, conversation_id: Optional[int] = None
) -> Tuple[bool, Optional[str]]:
    """
    Get user's online status and last seen time, respecting privacy settings.
    Returns (is_online, last_seen_at_iso) tuple.
    Creates default presence if it doesn't exist.
    Falls back to last message time if last_seen_at is None.
    """
    if not PRESENCE_ENABLED:
        return False, None

    presence = db.query(UserPresence).filter(UserPresence.user_id == user_id).first()

    # Create default presence if it doesn't exist
    if not presence:
        presence = UserPresence(
            user_id=user_id,
            privacy_settings={
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True,
            },
            device_online=False,
            last_seen_at=None,
        )
        db.add(presence)
        try:
            db.commit()
            db.refresh(presence)
        except IntegrityError:
            db.rollback()
            presence = (
                db.query(UserPresence).filter(UserPresence.user_id == user_id).first()
            )

    # Check privacy settings
    privacy = presence.privacy_settings or {}
    share_online = privacy.get("share_online", True)
    share_last_seen = privacy.get("share_last_seen", "contacts")
    if share_last_seen == "all":
        share_last_seen = "everyone"

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
                last_message = (
                    db.query(PrivateChatMessage)
                    .filter(
                        PrivateChatMessage.conversation_id == conversation_id,
                        PrivateChatMessage.sender_id == user_id,
                    )
                    .order_by(PrivateChatMessage.created_at.desc())
                    .first()
                )
                if last_message:
                    last_seen = last_message.created_at.isoformat()
            else:
                # Try to find any recent message from this user
                last_message = (
                    db.query(PrivateChatMessage)
                    .filter(PrivateChatMessage.sender_id == user_id)
                    .order_by(PrivateChatMessage.created_at.desc())
                    .first()
                )
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


def publish_to_pusher_private(
    conversation_id: int,
    message_id: int,
    sender_id: int,
    sender_username: str,
    profile_pic_url: Optional[str],
    avatar_url: Optional[str],
    frame_url: Optional[str],
    badge: Optional[dict],
    message: str,
    created_at: Union[datetime, str],
    is_new_conversation: bool,
    reply_to: Optional[dict] = None,
):
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
            "is_new_conversation": is_new_conversation,
        }
        if reply_to:
            event_data["reply_to"] = reply_to
        publish_chat_message_sync(channel, "new-message", event_data)
    except Exception as e:
        logger.error(f"Failed to publish private chat message to Pusher: {e}")


def send_push_if_needed_sync(
    recipient_id: int,
    conversation_id: int,
    sender_id: int,
    sender_username: str,
    message: str,
    is_new_conversation: bool,
):
    """Background task wrapper to send push notification (in-app if active, system if inactive)"""
    import asyncio

    from db import get_db
    from utils.notification_storage import create_notification

    db = next(get_db())
    try:
        # Check if sender is muted by recipient
        if is_user_muted_for_private_chat(sender_id, recipient_id, db):
            logger.debug(
                f"User {sender_id} is muted by {recipient_id}, skipping push notification"
            )
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
                "sender_id": sender_id,
            }
        else:
            heading = sender_username
            content = message[:100]  # Truncate for notification
            data = {
                "type": "private_message",
                "conversation_id": conversation_id,
                "sender_id": sender_id,
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
                is_in_app_notification=is_active,
            )
        )

        # Store notification in database
        create_notification(
            db=db,
            user_id=recipient_id,
            title=heading,
            body=content,
            notification_type=(
                "chat_private" if not is_new_conversation else "chat_request"
            ),
            data=data,
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
    current_user: User = Depends(get_current_user),
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

    admin_user_id = _get_admin_user_id(db)
    is_admin_conversation = admin_user_id in [
        current_user.account_id,
        request.recipient_id,
    ]

    # Find or create conversation (sorted user IDs for consistency)
    user_ids = sorted([current_user.account_id, request.recipient_id])
    conversation = (
        db.query(PrivateChatConversation)
        .filter(
            PrivateChatConversation.user1_id == user_ids[0],
            PrivateChatConversation.user2_id == user_ids[1],
        )
        .first()
    )

    is_new_conversation = False
    if not conversation:
        # New conversation - set status to pending
        conversation = PrivateChatConversation(
            user1_id=user_ids[0],
            user2_id=user_ids[1],
            requested_by=current_user.account_id,
            status="accepted" if is_admin_conversation else "pending",
        )
        if is_admin_conversation:
            conversation.responded_at = datetime.utcnow()
        db.add(conversation)
        try:
            db.flush()
            is_new_conversation = True
        except IntegrityError:
            db.rollback()
            conversation = (
                db.query(PrivateChatConversation)
                .filter(
                    PrivateChatConversation.user1_id == user_ids[0],
                    PrivateChatConversation.user2_id == user_ids[1],
                )
                .first()
            )
            if not conversation:
                raise HTTPException(
                    status_code=500, detail="Failed to create conversation"
                )

    if is_admin_conversation and conversation.status == "pending":
        conversation.status = "accepted"
        conversation.responded_at = datetime.utcnow()

    # Check if conversation is rejected
    if conversation.status == "rejected":
        raise HTTPException(
            status_code=403, detail="User is not accepting private messages."
        )

    # Check if this is the first message in the conversation
    existing_message_count = (
        db.query(PrivateChatMessage)
        .filter(PrivateChatMessage.conversation_id == conversation.id)
        .count()
    )

    is_first_message = existing_message_count == 0

    # Check if conversation is pending
    if conversation.status == "pending":
        if conversation.requested_by != current_user.account_id:
            # Recipient trying to send message before accepting - must accept first
            raise HTTPException(
                status_code=403,
                detail="Chat request must be accepted before sending messages. Please accept the chat request first.",
            )

        # If requester is sending and it's NOT the first message, block until accepted
        if (
            conversation.requested_by == current_user.account_id
            and not is_first_message
        ):
            raise HTTPException(
                status_code=403,
                detail="Chat request must be accepted before sending more messages. Please wait for the recipient to accept.",
            )
        # First message from requester is always allowed (creates the request)

    # Sanitize message to prevent XSS
    sanitized_message = sanitize_message(request.message)
    if not sanitized_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Check for duplicate message (idempotency)
    if request.client_message_id:
        existing_message = (
            db.query(PrivateChatMessage)
            .filter(
                PrivateChatMessage.conversation_id == conversation.id,
                PrivateChatMessage.sender_id == current_user.account_id,
                PrivateChatMessage.client_message_id == request.client_message_id,
            )
            .first()
        )

        if existing_message:
            logger.debug(
                f"Duplicate private chat message detected: {request.client_message_id}"
            )
            return {
                "conversation_id": conversation.id,
                "message_id": existing_message.id,
                "status": conversation.status,
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True,
            }

    # Burst rate limiting via Redis (fallback to DB)
    burst_allowed = await check_burst_limit(
        "private",
        current_user.account_id,
        PRIVATE_CHAT_MAX_MESSAGES_PER_BURST,
        PRIVATE_CHAT_BURST_WINDOW_SECONDS,
    )
    if burst_allowed is False:
        raise HTTPException(
            status_code=429,
            detail=f"Burst rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_BURST} messages per {PRIVATE_CHAT_BURST_WINDOW_SECONDS} seconds.",
        )
    if burst_allowed is None:
        burst_window_ago = datetime.utcnow() - timedelta(
            seconds=PRIVATE_CHAT_BURST_WINDOW_SECONDS
        )
        recent_burst = (
            db.query(PrivateChatMessage)
            .filter(
                PrivateChatMessage.sender_id == current_user.account_id,
                PrivateChatMessage.created_at >= burst_window_ago,
            )
            .count()
        )

        if recent_burst >= PRIVATE_CHAT_MAX_MESSAGES_PER_BURST:
            raise HTTPException(
                status_code=429,
                detail=f"Burst rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_BURST} messages per {PRIVATE_CHAT_BURST_WINDOW_SECONDS} seconds.",
            )

    # Per-minute rate limiting
    minute_allowed = await check_rate_limit(
        "private", current_user.account_id, PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE, 60
    )
    if minute_allowed is False:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute.",
        )
    if minute_allowed is None:
        one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
        recent_messages = (
            db.query(PrivateChatMessage)
            .filter(
                PrivateChatMessage.sender_id == current_user.account_id,
                PrivateChatMessage.created_at >= one_minute_ago,
            )
            .count()
        )

        if recent_messages >= PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Maximum {PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute.",
            )

    # Validate reply_to_message_id if provided
    reply_to_message = None
    if request.reply_to_message_id:
        reply_to_message = (
            db.query(PrivateChatMessage)
            .filter(
                PrivateChatMessage.id == request.reply_to_message_id,
                PrivateChatMessage.conversation_id == conversation.id,
            )
            .first()
        )
        if not reply_to_message:
            raise HTTPException(
                status_code=404,
                detail=f"Message {request.reply_to_message_id} not found in this conversation",
            )

    # Create message
    new_message = PrivateChatMessage(
        conversation_id=conversation.id,
        sender_id=current_user.account_id,
        message=sanitized_message,
        status="sent",
        client_message_id=request.client_message_id,
        reply_to_message_id=request.reply_to_message_id,  # Use directly since validation ensures it exists if provided
    )

    db.add(new_message)
    conversation.last_message_at = datetime.utcnow()

    # Update sender's presence (last_seen_at) when they send a message
    if PRESENCE_ENABLED:
        sender_presence = (
            db.query(UserPresence)
            .filter(UserPresence.user_id == current_user.account_id)
            .first()
        )
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
                    "read_receipts": True,
                },
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
            "created_at": reply_to_message.created_at.isoformat(),
        }

    # Publish to Pusher via Redis queue (fallback to inline background tasks)
    username = get_display_username(current_user)
    is_admin_sender = admin_user_id == current_user.account_id
    push_args = None if is_admin_sender else {
        "recipient_id": request.recipient_id,
        "conversation_id": conversation.id,
        "sender_id": current_user.account_id,
        "sender_username": username,
        "message": new_message.message,
        "is_new_conversation": is_new_conversation,
    }
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
                "reply_to": reply_info,
            },
            "push_args": push_args,
        },
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
            reply_info,
        )
        if push_args:
            background_tasks.add_task(
                send_push_if_needed_sync,
                request.recipient_id,
                conversation.id,
                current_user.account_id,
                username,
                new_message.message,
                is_new_conversation,
            )

    return {
        "conversation_id": conversation.id,
        "message_id": new_message.id,
        "status": conversation.status,
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False,
    }


@router.post("/accept-reject")
async def accept_reject_chat(
    request: AcceptRejectRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Accept or reject a chat request"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    conversation = (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == request.conversation_id)
        .first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    admin_user_id = _get_admin_user_id(db)
    if admin_user_id in [conversation.user1_id, conversation.user2_id]:
        if conversation.status == "pending":
            conversation.status = "accepted"
            conversation.responded_at = datetime.utcnow()
            db.commit()
        return {"conversation_id": conversation.id, "status": conversation.status}

    # Verify user is the recipient (not the requester)
    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if current_user.account_id == conversation.requested_by:
        raise HTTPException(
            status_code=400, detail="Cannot accept/reject your own request"
        )

    if conversation.status != "pending":
        # Conversation already responded to - return current status
        return {
            "conversation_id": conversation.id,
            "status": conversation.status,
            "message": f"Conversation already {conversation.status}",
        }

    if request.action == "accept":
        conversation.status = "accepted"
    elif request.action == "reject":
        conversation.status = "rejected"
    else:
        raise HTTPException(
            status_code=400, detail="Invalid action. Use 'accept' or 'reject'"
        )

    conversation.responded_at = datetime.utcnow()
    db.commit()

    # Notify requester via Pusher
    requester_id = conversation.requested_by
    background_tasks.add_task(
        publish_chat_message_sync,
        f"private-conversation-{conversation.id}",
        "conversation-updated",
        {"conversation_id": conversation.id, "status": conversation.status},
    )

    return {"conversation_id": conversation.id, "status": conversation.status}


@router.get("/conversations")
async def list_private_conversations(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """List all private chat conversations with unread counts"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    # Get conversations where user is participant
    # Include: accepted conversations OR pending conversations (requester can see their request, recipient can see requests to them)
    conversations = (
        db.query(PrivateChatConversation)
        .filter(
            or_(
                PrivateChatConversation.user1_id == current_user.account_id,
                PrivateChatConversation.user2_id == current_user.account_id,
            ),
            or_(
                PrivateChatConversation.status == "accepted",
                PrivateChatConversation.status == "pending",
            ),
        )
        .order_by(
            func.coalesce(
                PrivateChatConversation.last_message_at,
                PrivateChatConversation.created_at,
            ).desc()
        )
        .all()
    )

    if not conversations:
        return {"conversations": []}

    admin_user_id = _get_admin_user_id(db)

    peer_map = {}
    conv_ids_user1 = []
    conv_ids_user2 = []
    peer_ids = set()
    for conv in conversations:
        if conv.user1_id == current_user.account_id:
            peer_id = conv.user2_id
            conv_ids_user1.append(conv.id)
        else:
            peer_id = conv.user1_id
            conv_ids_user2.append(conv.id)
        peer_map[conv.id] = peer_id
        peer_ids.add(peer_id)

    peer_users = db.query(User).filter(User.account_id.in_(list(peer_ids))).all()
    peer_user_map = {user.account_id: user for user in peer_users}

    unread_counts = {}
    if conv_ids_user1:
        unread_user1 = (
            db.query(
                PrivateChatMessage.conversation_id, func.count(PrivateChatMessage.id)
            )
            .join(
                PrivateChatConversation,
                PrivateChatConversation.id == PrivateChatMessage.conversation_id,
            )
            .filter(
                PrivateChatMessage.conversation_id.in_(conv_ids_user1),
                PrivateChatMessage.sender_id != current_user.account_id,
                or_(
                    PrivateChatConversation.last_read_message_id_user1.is_(None),
                    PrivateChatMessage.id
                    > PrivateChatConversation.last_read_message_id_user1,
                ),
            )
            .group_by(PrivateChatMessage.conversation_id)
            .all()
        )
        unread_counts.update({cid: count for cid, count in unread_user1})
    if conv_ids_user2:
        unread_user2 = (
            db.query(
                PrivateChatMessage.conversation_id, func.count(PrivateChatMessage.id)
            )
            .join(
                PrivateChatConversation,
                PrivateChatConversation.id == PrivateChatMessage.conversation_id,
            )
            .filter(
                PrivateChatMessage.conversation_id.in_(conv_ids_user2),
                PrivateChatMessage.sender_id != current_user.account_id,
                or_(
                    PrivateChatConversation.last_read_message_id_user2.is_(None),
                    PrivateChatMessage.id
                    > PrivateChatConversation.last_read_message_id_user2,
                ),
            )
            .group_by(PrivateChatMessage.conversation_id)
            .all()
        )
        unread_counts.update({cid: count for cid, count in unread_user2})

    presence_rows = (
        db.query(UserPresence).filter(UserPresence.user_id.in_(list(peer_ids))).all()
    )
    presence_map = {p.user_id: p for p in presence_rows}
    missing_ids = peer_ids - set(presence_map.keys())
    if missing_ids:
        for user_id in missing_ids:
            presence = UserPresence(
                user_id=user_id,
                privacy_settings={
                    "share_last_seen": "contacts",
                    "share_online": True,
                    "read_receipts": True,
                },
                device_online=False,
                last_seen_at=None,
            )
            db.add(presence)
            presence_map[user_id] = presence
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        presence_rows = (
            db.query(UserPresence)
            .filter(UserPresence.user_id.in_(list(peer_ids)))
            .all()
        )
        presence_map = {p.user_id: p for p in presence_rows}

    last_message_map = {}
    if conversations and peer_ids:
        last_messages = (
            db.query(
                PrivateChatMessage.conversation_id,
                PrivateChatMessage.sender_id,
                func.max(PrivateChatMessage.created_at),
            )
            .filter(
                PrivateChatMessage.conversation_id.in_(
                    [conv.id for conv in conversations]
                ),
                PrivateChatMessage.sender_id.in_(list(peer_ids)),
            )
            .group_by(PrivateChatMessage.conversation_id, PrivateChatMessage.sender_id)
            .all()
        )
        last_message_map = {
            (cid, sender_id): created_at for cid, sender_id, created_at in last_messages
        }

    profile_map = get_user_chat_profile_data_bulk(peer_users, db)

    result = []
    for conv in conversations:
        peer_id = peer_map.get(conv.id)
        peer_user = peer_user_map.get(peer_id)
        if not peer_user:
            continue

        presence = presence_map.get(peer_id)
        privacy = (
            presence.privacy_settings if presence and presence.privacy_settings else {}
        )
        share_online = privacy.get("share_online", True)
        share_last_seen = privacy.get("share_last_seen", "contacts")
        if share_last_seen == "all":
            share_last_seen = "everyone"

        peer_online = presence.device_online if presence and share_online else False
        peer_last_seen = None
        if share_last_seen in ["everyone", "contacts"]:
            if presence and presence.last_seen_at:
                peer_last_seen = presence.last_seen_at.isoformat()
            else:
                last_msg_time = last_message_map.get((conv.id, peer_id))
                if last_msg_time:
                    peer_last_seen = last_msg_time.isoformat()

        profile_data = profile_map.get(peer_id, {})

        result.append(
            {
                "conversation_id": conv.id,
                "peer_user_id": peer_id,
                "peer_username": get_display_username(peer_user),
                "peer_profile_pic": profile_data.get("profile_pic_url"),
                "peer_avatar_url": profile_data.get("avatar_url"),
                "peer_frame_url": profile_data.get("frame_url"),
                "peer_badge": profile_data.get("badge"),
                "last_message_at": (
                    conv.last_message_at.isoformat() if conv.last_message_at else None
                ),
                "unread_count": unread_counts.get(conv.id, 0),
                "peer_online": peer_online,
                "peer_last_seen": peer_last_seen,
            }
        )

    if admin_user_id:
        admin_index = next(
            (
                idx
                for idx, item in enumerate(result)
                if item.get("peer_user_id") == admin_user_id
            ),
            None,
        )
        if admin_index is not None and admin_index != 0:
            result.insert(0, result.pop(admin_index))

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
        avatars = {
            a.id: a
            for a in db.query(Avatar).filter(Avatar.id.in_(list(avatar_ids))).all()
        }

    # Batch load all frames
    frame_ids = {u.selected_frame_id for u in users if u.selected_frame_id}
    frames = {}
    if frame_ids:
        frames = {
            f.id: f for f in db.query(Frame).filter(Frame.id.in_(list(frame_ids))).all()
        }

    # Batch load all badges (now from TriviaModeConfig)
    badge_ids = {u.badge_id for u in users if u.badge_id}
    badges = {}
    if badge_ids:
        mode_configs = (
            db.query(TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_id.in_(list(badge_ids)),
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .all()
        )
        badges = {mc.mode_id: mc for mc in mode_configs}

    # Batch load all active subscriptions with plans eagerly loaded
    active_subscriptions: Dict[int, List[UserSubscription]] = {}
    if user_ids:
        subs = (
            db.query(UserSubscription)
            .options(joinedload(UserSubscription.plan))
            .join(SubscriptionPlan)
            .filter(
                and_(
                    UserSubscription.user_id.in_(list(user_ids)),
                    UserSubscription.status == "active",
                    UserSubscription.current_period_end > datetime.utcnow(),
                )
            )
            .all()
        )
        for sub in subs:
            if sub.user_id not in active_subscriptions:
                active_subscriptions[sub.user_id] = []
            active_subscriptions[sub.user_id].append(sub)

    # Batch load subscription badges (bronze and silver) from TriviaModeConfig
    subscription_badge_ids = [
        "bronze",
        "bronze_badge",
        "brone_badge",
        "brone",
        "silver",
        "silver_badge",
    ]
    subscription_badges_dict = {
        mc.mode_id: mc
        for mc in db.query(TriviaModeConfig)
        .filter(
            TriviaModeConfig.mode_id.in_(list(subscription_badge_ids)),
            TriviaModeConfig.badge_image_url.isnot(None),
        )
        .all()
    }
    # Also try name-based matching
    name_based_badges = {
        mc.mode_id: mc
        for mc in db.query(TriviaModeConfig)
        .filter(
            (
                TriviaModeConfig.mode_name.ilike("%bronze%")
                | TriviaModeConfig.mode_name.ilike("%silver%")
            ),
            TriviaModeConfig.badge_image_url.isnot(None),
        )
        .all()
    }
    subscription_badges_dict.update(name_based_badges)

    # Generate presigned URLs in batch
    presigned_avatars = {}
    presigned_frames = {}
    for avatar_id, avatar in avatars.items():
        bucket = getattr(avatar, "bucket", None)
        object_key = getattr(avatar, "object_key", None)
        if bucket and object_key:
            try:
                presigned_avatars[avatar_id] = presign_get(
                    bucket, object_key, expires=900
                )
            except Exception as e:
                logger.warning(f"Failed to presign avatar {avatar_id}: {e}")

    for frame_id, frame in frames.items():
        bucket = getattr(frame, "bucket", None)
        object_key = getattr(frame, "object_key", None)
        if bucket and object_key:
            try:
                presigned_frames[frame_id] = presign_get(
                    bucket, object_key, expires=900
                )
            except Exception as e:
                logger.warning(f"Failed to presign frame {frame_id}: {e}")

    from utils.user_level_service import get_level_progress_for_users

    level_progress_map = get_level_progress_for_users(users, db)

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
                "image_url": mode_config.badge_image_url,
            }

        # Subscription badges
        subscription_badges = []
        user_subs = active_subscriptions.get(user.account_id, [])
        for sub in user_subs:
            plan = sub.plan  # Use 'plan' relationship, not 'subscription_plan'
            if not plan:
                continue

            # Check for bronze ($5)
            if (
                getattr(plan, "unit_amount_minor", None) == 500
                or getattr(plan, "price_usd", None) == 5.0
            ):
                bronze_badge = (
                    subscription_badges_dict.get("bronze")
                    or subscription_badges_dict.get("bronze_badge")
                    or subscription_badges_dict.get("brone_badge")
                    or subscription_badges_dict.get("brone")
                )
                if not bronze_badge:
                    # Try name-based match
                    for bid, mc in subscription_badges_dict.items():
                        if "bronze" in mc.mode_name.lower():
                            bronze_badge = mc
                            break
                if bronze_badge and bronze_badge.badge_image_url:
                    subscription_badges.append(
                        {
                            "id": bronze_badge.mode_id,
                            "name": bronze_badge.mode_name,
                            "image_url": bronze_badge.badge_image_url,
                            "subscription_type": "bronze",
                            "price": 5.0,
                        }
                    )

            # Check for silver ($10)
            if (
                getattr(plan, "unit_amount_minor", None) == 1000
                or getattr(plan, "price_usd", None) == 10.0
            ):
                silver_badge = subscription_badges_dict.get(
                    "silver"
                ) or subscription_badges_dict.get("silver_badge")
                if not silver_badge:
                    # Try name-based match
                    for bid, mc in subscription_badges_dict.items():
                        if "silver" in mc.mode_name.lower():
                            silver_badge = mc
                            break
                if silver_badge and silver_badge.badge_image_url:
                    subscription_badges.append(
                        {
                            "id": silver_badge.mode_id,
                            "name": silver_badge.mode_name,
                            "image_url": silver_badge.badge_image_url,
                            "subscription_type": "silver",
                            "price": 10.0,
                        }
                    )

        # Level progress
        level_progress = level_progress_map.get(
            user.account_id,
            {"level": user.level if user.level else 1, "level_progress": "0/100"},
        )

        profile_cache[user.account_id] = {
            "profile_pic_url": user.profile_pic_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "badge": badge_info,
            "subscription_badges": subscription_badges,
            "level": level_progress["level"],
            "level_progress": level_progress["level_progress"],
        }

    return profile_cache


@router.get("/conversations/{conversation_id}/messages")
async def get_private_messages(
    conversation_id: int,
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get messages from a private conversation with read status"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    conversation = (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == conversation_id)
        .first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Check if conversation is accepted - recipient cannot view messages until accepted
    if conversation.status == "pending":
        admin_user_id = _get_admin_user_id(db)
        if admin_user_id not in [conversation.user1_id, conversation.user2_id]:
            if conversation.requested_by != current_user.account_id:
                # Recipient trying to view messages before accepting
                raise HTTPException(
                    status_code=403,
                    detail="Chat request must be accepted before viewing messages. Please accept the chat request first.",
                )

    # Get last_read_message_id for current user
    last_read_id = (
        conversation.last_read_message_id_user1
        if conversation.user1_id == current_user.account_id
        else conversation.last_read_message_id_user2
    )

    # Eagerly load senders to avoid N+1 queries
    messages = (
        db.query(PrivateChatMessage)
        .options(joinedload(PrivateChatMessage.sender))
        .filter(PrivateChatMessage.conversation_id == conversation_id)
        .order_by(PrivateChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )

    # Get peer user's presence (last seen and online status)
    peer_id = (
        conversation.user2_id
        if conversation.user1_id == current_user.account_id
        else conversation.user1_id
    )
    peer_online, peer_last_seen = get_user_presence_info(
        db, peer_id, current_user.account_id, conversation_id
    )

    # Collect all unique users and reply message IDs
    unique_users = {msg.sender for msg in messages if msg.sender}
    reply_message_ids = {
        msg.reply_to_message_id for msg in messages if msg.reply_to_message_id
    }

    # Batch load all replied messages with their senders
    replied_messages = {}
    if reply_message_ids:
        replied_msgs = (
            db.query(PrivateChatMessage)
            .options(joinedload(PrivateChatMessage.sender))
            .filter(
                PrivateChatMessage.id.in_(list(reply_message_ids)),
                PrivateChatMessage.conversation_id == conversation_id,
            )
            .all()
        )
        replied_messages = {msg.id: msg for msg in replied_msgs}
        # Add replied message senders to unique_users set
        unique_users.update({msg.sender for msg in replied_msgs if msg.sender})

    # Batch load profile data for all unique users
    profile_cache = _batch_get_user_profile_data(list(unique_users), db)

    # Build response
    result_messages = []
    for msg in reversed(messages):
        sender_profile_data = profile_cache.get(
            msg.sender_id,
            {
                "profile_pic_url": None,
                "avatar_url": None,
                "frame_url": None,
                "badge": None,
                "subscription_badges": [],
                "level": 1,
                "level_progress": "0/100",
            },
        )
        is_read = (
            last_read_id is not None and msg.id <= last_read_id
            if msg.sender_id != current_user.account_id
            else None
        )

        # Get reply information if this message is a reply
        reply_info = None
        if msg.reply_to_message_id and msg.reply_to_message_id in replied_messages:
            replied_msg = replied_messages[msg.reply_to_message_id]
            replied_profile = profile_cache.get(
                replied_msg.sender_id,
                {
                    "profile_pic_url": None,
                    "avatar_url": None,
                    "frame_url": None,
                    "badge": None,
                    "subscription_badges": [],
                    "level": 1,
                    "level_progress": "0/100",
                },
            )
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
                "sender_level_progress": replied_profile.get("level_progress", "0/100"),
            }

        result_messages.append(
            {
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
                "delivered_at": (
                    msg.delivered_at.isoformat() if msg.delivered_at else None
                ),
                "is_read": is_read,
                "reply_to": reply_info,
                "sender_level": sender_profile_data.get("level", 1),
                "sender_level_progress": sender_profile_data.get(
                    "level_progress", "0/100"
                ),
            }
        )

    return {
        "messages": result_messages,
        "peer_online": peer_online,
        "peer_last_seen": peer_last_seen,
    }


@router.post("/conversations/{conversation_id}/mark-read")
async def mark_conversation_read(
    conversation_id: int,
    message_id: Optional[int] = Query(
        None, description="Message ID to mark as read up to (defaults to latest)"
    ),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark conversation as read up to a specific message ID"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    conversation = (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == conversation_id)
        .first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # If message_id not provided, use the latest message ID
    if message_id is None:
        latest_message = (
            db.query(PrivateChatMessage)
            .filter(PrivateChatMessage.conversation_id == conversation_id)
            .order_by(PrivateChatMessage.id.desc())
            .first()
        )

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
    peer_id = (
        conversation.user2_id
        if conversation.user1_id == current_user.account_id
        else conversation.user1_id
    )
    if background_tasks is not None:
        background_tasks.add_task(
            publish_chat_message_sync,
            f"private-conversation-{conversation_id}",
            "messages-read",
            {
                "conversation_id": conversation_id,
                "reader_id": current_user.account_id,
                "last_read_message_id": message_id,
            },
        )

    return {"conversation_id": conversation_id, "last_read_message_id": message_id}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get conversation details"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    conversation = (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == conversation_id)
        .first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")

    peer_id = (
        conversation.user2_id
        if conversation.user1_id == current_user.account_id
        else conversation.user1_id
    )
    peer_user = db.query(User).filter(User.account_id == peer_id).first()

    # Update current user's presence when they view a conversation
    if PRESENCE_ENABLED:
        current_user_presence = (
            db.query(UserPresence)
            .filter(UserPresence.user_id == current_user.account_id)
            .first()
        )
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
                    "read_receipts": True,
                },
            )
            db.add(current_user_presence)
        db.commit()

    # Get peer user's presence (last seen and online status)
    peer_online, peer_last_seen = get_user_presence_info(
        db, peer_id, current_user.account_id, conversation_id
    )

    # Get peer user's profile data (avatar, frame)
    peer_profile_data = (
        get_user_chat_profile_data(peer_user, db)
        if peer_user
        else {"profile_pic_url": None, "avatar_url": None, "frame_url": None}
    )

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
        "last_message_at": (
            conversation.last_message_at.isoformat()
            if conversation.last_message_at
            else None
        ),
        "peer_level": peer_profile_data.get("level", 1) if peer_user else None,
        "peer_level_progress": (
            peer_profile_data.get("level_progress", "0/100") if peer_user else None
        ),
    }


@router.post("/conversations/{conversation_id}/typing")
async def send_typing_indicator(
    conversation_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send typing indicator to conversation"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    conversation = (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == conversation_id)
        .first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")

    if conversation.status != "accepted":
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
            "username": username,
        },
    )

    return {"status": "typing"}


@router.post("/conversations/{conversation_id}/typing-stop")
async def send_typing_stop(
    conversation_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send typing stop indicator to conversation"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    conversation = (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == conversation_id)
        .first()
    )

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
        {"conversation_id": conversation_id, "user_id": current_user.account_id},
    )

    return {"status": "stopped"}


@router.post("/messages/{message_id}/mark-delivered")
async def mark_message_delivered(
    message_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark a message as delivered"""
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Private chat is disabled")

    message = (
        db.query(PrivateChatMessage).filter(PrivateChatMessage.id == message_id).first()
    )

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    # Verify user is in the conversation
    conversation = (
        db.query(PrivateChatConversation)
        .filter(PrivateChatConversation.id == message.conversation_id)
        .first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Only mark as delivered if message is not from current user
    if message.sender_id == current_user.account_id:
        raise HTTPException(
            status_code=400, detail="Cannot mark own message as delivered"
        )

    # Update message status if not already delivered/seen
    if message.status == "sent":
        message.status = "delivered"
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
                "delivered_at": message.delivered_at.isoformat(),
            },
        )

    return {
        "message_id": message_id,
        "status": message.status,
        "delivered_at": (
            message.delivered_at.isoformat() if message.delivered_at else None
        ),
    }


@router.post("/block")
async def block_user(
    request: PrivateChatBlockUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Block a user from sending private messages"""
    return service_block_private_chat_user(
        db, current_user=current_user, blocked_user_id=request.blocked_user_id
    )


@router.delete("/block/{blocked_user_id}")
async def unblock_user(
    blocked_user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Unblock a user"""
    return service_unblock_private_chat_user(
        db, current_user=current_user, blocked_user_id=blocked_user_id
    )


@router.get("/blocks")
async def list_blocks(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """List all users blocked by the current user"""
    return service_list_private_chat_blocks(db, current_user=current_user)
