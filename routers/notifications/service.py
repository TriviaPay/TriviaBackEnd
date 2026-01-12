"""Notifications domain service layer."""

import logging
import os
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from typing import Deque, Dict, Optional, Tuple

from fastapi import HTTPException, status

from core.config import ONESIGNAL_ENABLED, ONESIGNAL_MAX_PLAYERS_PER_USER, PUSHER_ENABLED

from . import repository as notifications_repository
from .schemas import (
    CreateTestNotificationRequest,
    ListPlayersResponse,
    MarkReadRequest,
    NotificationListResponse,
    NotificationResponse,
)

logger = logging.getLogger(__name__)

_rate_limit_store: "OrderedDict[str, Deque[float]]" = OrderedDict()
_rate_limit_lock = threading.Lock()
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 20
RATE_LIMIT_MAX_KEYS = 10000

NOTIFICATIONS_DEBUG = os.getenv("NOTIFICATIONS_DEBUG", "false").lower() == "true"

_AUTH_CACHE_TTL_SECONDS = int(os.getenv("PUSHER_AUTH_CACHE_TTL_SECONDS", "5"))
_conversation_cache: Dict[int, Tuple[int, int, str, float]] = {}
_block_cache: Dict[str, Tuple[bool, float]] = {}


def _check_rate_limit(identifier: str) -> bool:
    now = time.time()
    with _rate_limit_lock:
        bucket = _rate_limit_store.get(identifier)
        if bucket is None:
            bucket = deque()
            _rate_limit_store[identifier] = bucket
        else:
            _rate_limit_store.move_to_end(identifier)

        while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
            return False

        bucket.append(now)
        if len(_rate_limit_store) > RATE_LIMIT_MAX_KEYS:
            _rate_limit_store.popitem(last=False)

    return True


def _cache_get_conversation(conversation_id: int) -> Optional[Tuple[int, int, str]]:
    cached = _conversation_cache.get(conversation_id)
    if not cached:
        return None
    user1_id, user2_id, status, expires_at = cached
    if expires_at > time.time():
        return user1_id, user2_id, status
    _conversation_cache.pop(conversation_id, None)
    return None


def _cache_set_conversation(
    conversation_id: int, user1_id: int, user2_id: int, status: str
) -> None:
    if _AUTH_CACHE_TTL_SECONDS <= 0:
        return
    _conversation_cache[conversation_id] = (
        user1_id,
        user2_id,
        status,
        time.time() + _AUTH_CACHE_TTL_SECONDS,
    )


def _block_cache_key(user1_id: int, user2_id: int) -> str:
    return f"{min(user1_id, user2_id)}:{max(user1_id, user2_id)}"


def _cache_get_blocked(user1_id: int, user2_id: int) -> Optional[bool]:
    cached = _block_cache.get(_block_cache_key(user1_id, user2_id))
    if not cached:
        return None
    blocked, expires_at = cached
    if expires_at > time.time():
        return blocked
    _block_cache.pop(_block_cache_key(user1_id, user2_id), None)
    return None


def _cache_set_blocked(user1_id: int, user2_id: int, blocked: bool) -> None:
    if _AUTH_CACHE_TTL_SECONDS <= 0:
        return
    _block_cache[_block_cache_key(user1_id, user2_id)] = (
        blocked,
        time.time() + _AUTH_CACHE_TTL_SECONDS,
    )


def _presence_channel_user_id(channel_name: str) -> Optional[int]:
    if channel_name.startswith("presence-user-"):
        suffix = channel_name[len("presence-user-") :]
    elif channel_name.startswith("presence-"):
        suffix = channel_name[len("presence-") :]
    else:
        return None
    if suffix.isdigit():
        return int(suffix)
    return None


def register_onesignal_player(
    db, *, current_user, ip: str, player_id: str, platform: str
):
    if not ONESIGNAL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="OneSignal is disabled"
        )

    rl_key = f"osreg:{ip}:{current_user.account_id}"
    if not _check_rate_limit(rl_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
        )

    if platform not in ["ios", "android", "web"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Platform must be 'ios', 'android', or 'web'",
        )

    now = datetime.utcnow()

    existing = notifications_repository.get_player_by_player_id(db, player_id)
    if existing:
        if existing.user_id != current_user.account_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Player ID is already registered to another user",
            )

        existing.last_active = now
        existing.is_valid = True
        existing.platform = platform
        try:
            db.commit()
            logger.info(
                f"Updated OneSignal player {player_id} for user {current_user.account_id}"
            )
        except Exception as exc:
            db.rollback()
            logger.error(f"Failed to update OneSignal player {player_id}: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update player",
            )

        return {
            "message": "Player updated",
            "player_id": player_id,
            "user_id": current_user.account_id,
        }

    player_count = notifications_repository.count_players_for_user(
        db, current_user.account_id
    )
    if player_count >= ONESIGNAL_MAX_PLAYERS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Player limit reached for this user",
        )

    notifications_repository.create_player(
        db,
        user_id=current_user.account_id,
        player_id=player_id,
        platform=platform,
        now=now,
    )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(f"Failed to register OneSignal player {player_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register player",
        )

    logger.info(
        f"Registered OneSignal player {player_id} for user {current_user.account_id}"
    )
    return {
        "message": "Player registered",
        "player_id": player_id,
        "user_id": current_user.account_id,
    }


def list_onesignal_players(
    db, *, current_user, limit: int, offset: int
) -> ListPlayersResponse:
    if not ONESIGNAL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="OneSignal is disabled"
        )

    players = notifications_repository.list_players_for_user(
        db, user_id=current_user.account_id, limit=limit, offset=offset
    )
    total = notifications_repository.count_players_for_user(db, current_user.account_id)

    return ListPlayersResponse(total=total, limit=limit, offset=offset, players=players)


def get_notifications(
    db,
    *,
    current_user,
    limit: int,
    offset: int,
    unread_only: bool,
    cursor,
) -> NotificationListResponse:
    if NOTIFICATIONS_DEBUG:
        logger.info(
            "Querying notifications for account_id=%s (descope_user_id=%s)",
            current_user.account_id,
            current_user.descope_user_id,
        )

    total, unread_count = notifications_repository.get_notification_counts(
        db, user_id=current_user.account_id
    )
    if unread_only:
        total = unread_count

    notifications = notifications_repository.list_notifications(
        db,
        user_id=current_user.account_id,
        limit=limit,
        offset=offset,
        unread_only=unread_only,
        cursor=cursor,
    )

    return NotificationListResponse(
        notifications=[
            NotificationResponse(
                id=n.id,
                title=n.title,
                body=n.body,
                type=n.type,
                data=n.data,
                read=n.read,
                read_at=n.read_at.isoformat() if n.read_at else None,
                created_at=n.created_at.isoformat(),
            )
            for n in notifications
        ],
        total=total,
        unread_count=unread_count,
    )


def get_unread_count(db, *, current_user):
    _, unread_count = notifications_repository.get_notification_counts(
        db, user_id=current_user.account_id
    )
    return {"unread_count": unread_count}


def mark_notifications_read(db, *, current_user, request: MarkReadRequest):
    if not request.notification_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="notification_ids cannot be empty",
        )

    notifications_count = notifications_repository.count_notifications_for_user_by_ids(
        db,
        user_id=current_user.account_id,
        notification_ids=request.notification_ids,
    )
    if notifications_count != len(request.notification_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more notifications not found or not owned by user",
        )

    now = datetime.utcnow()
    updated_count = notifications_repository.mark_notifications_read(
        db,
        user_id=current_user.account_id,
        notification_ids=request.notification_ids,
        now=now,
    )
    db.commit()

    return {
        "message": f"Marked {updated_count} notification(s) as read",
        "marked_count": updated_count,
    }


def mark_all_notifications_read(db, *, current_user):
    updated_count = notifications_repository.mark_all_notifications_read(
        db, user_id=current_user.account_id, now=datetime.utcnow()
    )
    db.commit()

    return {
        "message": f"Marked {updated_count} notification(s) as read",
        "marked_count": updated_count,
    }


def delete_notification(db, *, current_user, notification_id: int):
    notification = notifications_repository.get_notification_for_user(
        db, user_id=current_user.account_id, notification_id=notification_id
    )
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found"
        )

    db.delete(notification)
    db.commit()

    return {"message": "Notification deleted", "notification_id": notification_id}


def delete_all_notifications(db, *, current_user, read_only: bool):
    deleted_count = notifications_repository.delete_notifications_for_user(
        db, user_id=current_user.account_id, read_only=read_only
    )
    db.commit()

    return {
        "message": f"Deleted {deleted_count} notification(s)",
        "deleted_count": deleted_count,
    }


def create_test_notification(db, *, current_user, request: CreateTestNotificationRequest):
    from utils.notification_storage import create_notification

    notification = create_notification(
        db=db,
        user_id=current_user.account_id,
        title=request.title,
        body=request.body,
        notification_type=request.notification_type,
        data=request.data,
    )

    logger.info(
        "Created test notification %s for user %s",
        notification.id,
        current_user.account_id,
    )

    return NotificationResponse(
        id=notification.id,
        title=notification.title,
        body=notification.body,
        type=notification.type,
        data=notification.data,
        read=notification.read,
        read_at=notification.read_at.isoformat() if notification.read_at else None,
        created_at=notification.created_at.isoformat(),
    )


def pusher_authenticate(db, *, current_user, socket_id: str, channel_name: str):
    from utils.chat_blocking import check_blocked
    from utils.pusher_client import get_pusher_client

    if not PUSHER_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Pusher is not enabled"
        )

    if channel_name.startswith("private-conversation-"):
        pusher_client = get_pusher_client()
        if not pusher_client:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Pusher client not available",
            )
        try:
            conversation_id = int(channel_name.split("-")[-1])
        except (ValueError, IndexError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid channel name format",
            )

        cached_conversation = _cache_get_conversation(conversation_id)
        if cached_conversation:
            user1_id, user2_id, status_value = cached_conversation
        else:
            conversation = notifications_repository.get_private_chat_conversation_summary(
                db, conversation_id=conversation_id
            )
            if not conversation:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
                )
            user1_id, user2_id, status_value = conversation
            _cache_set_conversation(conversation_id, user1_id, user2_id, status_value)

        if current_user.account_id not in [user1_id, user2_id]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized for this conversation",
            )

        if status_value != "accepted":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Conversation not accepted",
            )

        blocked = _cache_get_blocked(user1_id, user2_id)
        if blocked is None:
            blocked = check_blocked(db, user1_id, user2_id)
            _cache_set_blocked(user1_id, user2_id, blocked)
        if blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Users are blocked"
            )

        return pusher_client.authenticate(channel=channel_name, socket_id=socket_id)

    if channel_name.startswith("presence-"):
        scoped_user_id = _presence_channel_user_id(channel_name)
        if scoped_user_id is not None and scoped_user_id != current_user.account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized for this presence channel",
            )

        pusher_client = get_pusher_client()
        if not pusher_client:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Pusher client not available",
            )

        user_info = {
            "user_id": current_user.account_id,
            "username": (
                current_user.username or current_user.email.split("@")[0]
                if current_user.email
                else f"User{current_user.account_id}"
            ),
        }
        return pusher_client.authenticate(
            channel=channel_name, socket_id=socket_id, custom_data=user_info
        )

    if channel_name in ["global-chat", "trivia-live-chat"]:
        return {"status": "authorized"}

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown channel type"
    )
