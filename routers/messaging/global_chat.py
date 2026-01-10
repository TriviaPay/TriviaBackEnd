import logging
from datetime import datetime, timedelta
from typing import Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from config import (
    GLOBAL_CHAT_BURST_WINDOW_SECONDS,
    GLOBAL_CHAT_ENABLED,
    GLOBAL_CHAT_MAX_MESSAGE_LENGTH,
    GLOBAL_CHAT_MAX_MESSAGES_PER_BURST,
    GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE,
    GLOBAL_CHAT_RETENTION_DAYS,
)
from db import get_db
from models import OneSignalPlayer, User
from routers.dependencies import get_current_user
from utils.chat_helpers import (
    get_user_chat_profile_data,
    get_user_chat_profile_data_bulk,
)
from utils.chat_mute import get_muted_user_ids
from utils.chat_redis import get_chat_redis
from utils.onesignal_client import (
    ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS,
    send_push_notification_async,
)
from utils.pusher_client import publish_chat_message_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/global-chat", tags=["Global Chat"])

from .schemas import (
    GlobalChatCleanupResponse,
    GlobalChatMessagesResponse,
    GlobalChatSendMessageRequest,
    GlobalChatSendResponse,
)
from .service import (
    cleanup_global_chat_messages as service_cleanup_global_chat_messages,
    get_global_chat_messages as service_get_global_chat_messages,
    send_global_chat_message as service_send_global_chat_message,
)


def get_display_username(user: User) -> str:
    """Get display username with fallback logic"""
    if user.username and user.username.strip():
        return user.username
    if user.email:
        return user.email.split("@")[0]
    return f"User{user.account_id}"


def _ensure_datetime(value: Union[datetime, str]) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.utcnow()


def publish_to_pusher_global(
    message_id: int,
    user_id: int,
    username: str,
    profile_pic: Optional[str],
    avatar_url: Optional[str],
    frame_url: Optional[str],
    badge: Optional[dict],
    message: str,
    created_at: Union[datetime, str],
    reply_to: Optional[dict] = None,
):
    """Background task to publish to Pusher"""
    try:
        created_at_dt = _ensure_datetime(created_at)
        event_data = {
            "id": message_id,
            "user_id": user_id,
            "username": username,
            "profile_pic": profile_pic,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "badge": badge,
            "message": message,
            "created_at": created_at_dt.isoformat(),
        }
        if reply_to:
            event_data["reply_to"] = reply_to
        publish_chat_message_sync("global-chat", "new-message", event_data)
    except Exception as e:
        logger.error(f"Failed to publish global chat message to Pusher: {e}")


def send_push_for_global_chat_sync(
    message_id: int,
    sender_id: int,
    sender_username: str,
    message: str,
    created_at: Union[datetime, str],
):
    """Background task to send push notifications for global chat to all users (except sender)"""
    import asyncio

    from db import get_db
    from utils.notification_storage import create_notifications_batch

    db = next(get_db())
    try:
        created_at_dt = _ensure_datetime(created_at)
        # Get all users with OneSignal players (except sender)
        all_players = (
            db.query(OneSignalPlayer)
            .filter(
                OneSignalPlayer.user_id != sender_id, OneSignalPlayer.is_valid == True
            )
            .all()
        )

        if not all_players:
            logger.debug("No OneSignal players found for global chat push")
            return

        # Precompute muted users and active users to avoid per-user queries
        player_user_ids = {player.user_id for player in all_players}
        muted_user_ids = get_muted_user_ids(list(player_user_ids), "global", db)
        threshold_time = datetime.utcnow() - timedelta(
            seconds=ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS
        )
        active_user_ids = {
            player.user_id
            for player in all_players
            if player.last_active and player.last_active >= threshold_time
        }

        # Batch player IDs separately for active (in-app) and inactive (system) users
        BATCH_SIZE = 2000
        active_player_batches = []  # In-app notifications
        inactive_player_batches = []  # System push notifications
        active_current_batch = []
        inactive_current_batch = []

        for player in all_players:
            user_id = player.user_id

            # Check if user has muted global chat
            if user_id in muted_user_ids:
                continue

            if user_id in active_user_ids:
                # Active user: in-app notification
                active_current_batch.append(player.player_id)
                if len(active_current_batch) >= BATCH_SIZE:
                    active_player_batches.append(active_current_batch)
                    active_current_batch = []
            else:
                # Inactive user: system push notification
                inactive_current_batch.append(player.player_id)
                if len(inactive_current_batch) >= BATCH_SIZE:
                    inactive_player_batches.append(inactive_current_batch)
                    inactive_current_batch = []

        # Add remaining batches
        if active_current_batch:
            active_player_batches.append(active_current_batch)
        if inactive_current_batch:
            inactive_player_batches.append(inactive_current_batch)

        # Prepare notification data
        heading = "Global Chat"
        content = f"{sender_username}: {message[:100]}"  # Truncate for notification
        data = {
            "type": "global_chat",
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_username": sender_username,
            "message": message,
            "created_at": created_at_dt.isoformat(),
        }

        # Run async function in event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Send in-app notifications to active users
        for batch in active_player_batches:
            logger.debug(f"Sending in-app notification batch: {len(batch)} players")
            loop.run_until_complete(
                send_push_notification_async(
                    player_ids=batch,
                    heading=heading,
                    content=content,
                    data=data,
                    is_in_app_notification=True,
                )
            )

        # Send system push notifications to inactive users
        for batch in inactive_player_batches:
            logger.debug(
                f"Sending system push notification batch: {len(batch)} players"
            )
            loop.run_until_complete(
                send_push_notification_async(
                    player_ids=batch,
                    heading=heading,
                    content=content,
                    data=data,
                    is_in_app_notification=False,
                )
            )

        total_active = sum(len(b) for b in active_player_batches)
        total_inactive = sum(len(b) for b in inactive_player_batches)

        # Store notifications in database for all recipients
        all_recipient_ids = list(player_user_ids - muted_user_ids)
        if all_recipient_ids:
            create_notifications_batch(
                db=db,
                user_ids=all_recipient_ids,
                title=heading,
                body=content,
                notification_type="chat_global",
                data=data,
            )

        logger.info(
            f"Sent global chat push notifications | in-app={total_active} | system={total_inactive} | "
            f"sender_id={sender_id} | message_id={message_id}"
        )
    except Exception as e:
        logger.error(f"Failed to send push notifications for global chat: {e}")
    finally:
        db.close()


@router.post("/send", response_model=GlobalChatSendResponse)
async def send_global_message(
    request: GlobalChatSendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send message to global chat"""
    result = await service_send_global_chat_message(
        db, current_user=current_user, request=request
    )

    if not result["event_enqueued"] and not result["response"]["duplicate"]:
        background_tasks.add_task(
            publish_to_pusher_global,
            result["pusher_args"]["message_id"],
            result["pusher_args"]["user_id"],
            result["pusher_args"]["username"],
            result["pusher_args"]["profile_pic"],
            result["pusher_args"]["avatar_url"],
            result["pusher_args"]["frame_url"],
            result["pusher_args"]["badge"],
            result["pusher_args"]["message"],
            result["pusher_args"]["created_at"],
            result["pusher_args"]["reply_to"],
        )
        background_tasks.add_task(
            send_push_for_global_chat_sync,
            result["push_args"]["message_id"],
            result["push_args"]["sender_id"],
            result["push_args"]["sender_username"],
            result["push_args"]["message"],
            result["push_args"]["created_at"],
        )

    return result["response"]


def _batch_get_user_profile_data(users: list[User], db: Session) -> dict[int, dict]:
    """Delegate to shared bulk helper to avoid N+1 queries."""
    return get_user_chat_profile_data_bulk(users, db)


@router.get("/messages", response_model=GlobalChatMessagesResponse)
async def get_global_messages(
    limit: int = Query(50, ge=1, le=100),
    before: Optional[int] = Query(
        None, description="Message ID to fetch messages before"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get global chat messages with pagination"""
    return await service_get_global_chat_messages(
        db, current_user=current_user, limit=limit, before=before
    )


@router.post("/cleanup", response_model=GlobalChatCleanupResponse)
async def cleanup_old_messages(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    """Cleanup old messages based on retention policy (admin only)"""
    return service_cleanup_global_chat_messages(db, current_user=current_user)
