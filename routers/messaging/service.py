"""Messaging/Realtime service layer."""

import base64
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import BackgroundTasks, HTTPException, status

from core.cache import default_cache
from core.config import (
    E2EE_DM_BURST_WINDOW_SECONDS,
    E2EE_DM_ENABLED,
    E2EE_DM_MAX_MESSAGE_SIZE,
    E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST,
    E2EE_DM_MAX_MESSAGES_PER_MINUTE,
    GLOBAL_CHAT_ENABLED,
    GLOBAL_CHAT_RETENTION_DAYS,
    PRESENCE_ENABLED,
    PRIVATE_CHAT_BURST_WINDOW_SECONDS,
    PRIVATE_CHAT_ENABLED,
    PRIVATE_CHAT_MAX_MESSAGES_PER_BURST,
    PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE,
    PRIVATE_CHAT_PROFILE_CACHE_SECONDS,
)
from core.rate_limit import default_rate_limiter
from utils.chat_blocking import check_blocked
from utils.redis_pubsub import publish_dm_message

from . import repository as messaging_repository

logger = logging.getLogger(__name__)

# Active SSE connections for DM streams (user_id -> set of connection IDs).
from collections import defaultdict

ACTIVE_DM_SSE_CONNECTIONS: dict[int, set] = defaultdict(set)

# --- Chat mute ---


def get_chat_mute_preferences(db, *, current_user):
    from utils.chat_mute import get_mute_preferences, get_muted_users_from_preferences

    preferences = get_mute_preferences(
        current_user.account_id, db, create_if_missing=False
    )
    return {
        "global_chat_muted": preferences.global_chat_muted,
        "trivia_live_chat_muted": preferences.trivia_live_chat_muted,
        "private_chat_muted_users": get_muted_users_from_preferences(preferences),
    }


def set_global_chat_mute(db, *, current_user, muted: bool):
    from utils.chat_mute import get_mute_preferences

    preferences = get_mute_preferences(current_user.account_id, db)
    preferences.global_chat_muted = muted
    db.commit()
    return {
        "message": "Global chat muted" if muted else "Global chat unmuted",
        "global_chat_muted": preferences.global_chat_muted,
    }


def set_trivia_live_chat_mute(db, *, current_user, muted: bool):
    from utils.chat_mute import get_mute_preferences

    preferences = get_mute_preferences(current_user.account_id, db)
    preferences.trivia_live_chat_muted = muted
    db.commit()
    return {
        "message": "Trivia live chat muted" if muted else "Trivia live chat unmuted",
        "trivia_live_chat_muted": preferences.trivia_live_chat_muted,
    }


def set_private_chat_mute(db, *, current_user, user_id: int, muted: bool):
    from fastapi import HTTPException
    from core.users import get_user_by_id
    from utils.chat_mute import add_muted_user, remove_muted_user

    if user_id == current_user.account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot mute yourself")

    target_user = get_user_by_id(db, account_id=user_id)
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if muted:
        add_muted_user(current_user.account_id, user_id, db)
        return {"message": f"User {user_id} muted for private chat", "muted": True}
    remove_muted_user(current_user.account_id, user_id, db)
    return {"message": f"User {user_id} unmuted for private chat", "muted": False}


def list_private_chat_muted_users(db, *, current_user):
    from core.users import get_users_by_ids
    from utils.chat_mute import get_muted_users

    muted_user_ids = get_muted_users(current_user.account_id, db)
    muted_users = []
    if muted_user_ids:
        users = get_users_by_ids(db, account_ids=list(muted_user_ids))
        user_map = {user.account_id: user for user in users}
        for user_id in muted_user_ids:
            user = user_map.get(user_id)
            if user:
                muted_users.append(
                    {
                        "user_id": user.account_id,
                        "username": user.username or f"User{user.account_id}",
                        "profile_pic_url": user.profile_pic_url,
                    }
                )
    return {"muted_users": muted_users, "count": len(muted_users)}


# --- Presence ---


def get_my_presence(db, *, current_user):
    from config import PRESENCE_ENABLED

    if not PRESENCE_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Presence feature is not enabled")

    from models import UserPresence

    presence = (
        messaging_repository.query(db, UserPresence)
        .filter(UserPresence.user_id == current_user.account_id)
        .first()
    )

    if not presence:
        return {
            "user_id": current_user.account_id,
            "last_seen_at": None,
            "device_online": False,
            "privacy_settings": {
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True,
            },
        }

    privacy = presence.privacy_settings or {}
    share_last_seen = privacy.get("share_last_seen", "contacts")
    if share_last_seen == "all":
        share_last_seen = "everyone"

    return {
        "user_id": current_user.account_id,
        "last_seen_at": (
            presence.last_seen_at.isoformat() if presence.last_seen_at else None
        ),
        "device_online": presence.device_online,
        "privacy_settings": {
            "share_last_seen": share_last_seen,
            "share_online": privacy.get("share_online", True),
            "read_receipts": privacy.get("read_receipts", True),
        },
    }


def update_my_presence(db, *, current_user, request):
    from config import PRESENCE_ENABLED
    from sqlalchemy.exc import IntegrityError

    if not PRESENCE_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Presence feature is not enabled")

    from models import UserPresence

    presence = (
        messaging_repository.query(db, UserPresence)
        .filter(UserPresence.user_id == current_user.account_id)
        .first()
    )

    if not presence:
        presence = UserPresence(
            user_id=current_user.account_id,
            privacy_settings={
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True,
            },
        )
        db.add(presence)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            presence = (
                messaging_repository.query(db, UserPresence)
                .filter(UserPresence.user_id == current_user.account_id)
                .first()
            )
            if not presence:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update presence",
                )

    privacy = dict(
        presence.privacy_settings
        or {"share_last_seen": "contacts", "share_online": True, "read_receipts": True}
    )

    if request.share_last_seen is not None:
        share_last_seen = (
            "everyone" if request.share_last_seen == "all" else request.share_last_seen
        )
        privacy["share_last_seen"] = share_last_seen

    if request.share_online is not None:
        privacy["share_online"] = request.share_online

    if request.read_receipts is not None:
        privacy["read_receipts"] = request.read_receipts

    presence.privacy_settings = privacy

    try:
        db.commit()
        db.refresh(presence)
        return {"privacy_settings": presence.privacy_settings}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error updating presence: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update presence",
        )


# --- Status ---


async def create_status_post(db, *, current_user, request):
    import uuid
    from datetime import datetime, timedelta

    from config import STATUS_ENABLED, STATUS_MAX_POSTS_PER_DAY, STATUS_TTL_HOURS
    from utils.redis_pubsub import publish_dm_message

    if not STATUS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Status feature is not enabled")

    messaging_repository.lock_user(db, user_id=current_user.account_id)

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_posts = messaging_repository.count_status_posts_since(
        db, owner_user_id=current_user.account_id, since_dt=today_start
    )
    if today_posts >= STATUS_MAX_POSTS_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Maximum {STATUS_MAX_POSTS_PER_DAY} posts per day",
        )

    if request.audience_mode == "contacts":
        audience_user_ids = messaging_repository.list_user_contacts(
            db, user_id=current_user.account_id
        )
    elif request.audience_mode == "custom":
        if not request.custom_audience:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="custom_audience required for custom mode",
            )
        audience_user_ids = request.custom_audience
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid audience_mode")

    post_id = uuid.uuid4()
    expires_at = datetime.utcnow() + timedelta(hours=STATUS_TTL_HOURS)
    new_post = messaging_repository.create_status_post(
        db,
        post_id=post_id,
        owner_user_id=current_user.account_id,
        media_meta=request.media_meta,
        audience_mode=request.audience_mode,
        expires_at=expires_at,
        post_epoch=0,
    )
    messaging_repository.create_status_audience_rows(
        db, post_id=new_post.id, viewer_user_ids=audience_user_ids
    )

    db.commit()
    db.refresh(new_post)

    for viewer_id in audience_user_ids:
        event = {
            "type": "status_post",
            "post_id": str(new_post.id),
            "owner_user_id": current_user.account_id,
            "created_at": new_post.created_at.isoformat() if new_post.created_at else None,
            "expires_at": new_post.expires_at.isoformat() if new_post.expires_at else None,
        }
        # DM user channel for status notifications
        await publish_dm_message("", viewer_id, event)

    return {
        "id": str(new_post.id),
        "created_at": new_post.created_at.isoformat() if new_post.created_at else None,
        "expires_at": new_post.expires_at.isoformat() if new_post.expires_at else None,
        "audience_count": len(audience_user_ids),
    }


def get_status_feed(db, *, current_user, limit: int, cursor):
    import uuid
    from datetime import datetime

    from config import STATUS_ENABLED

    if not STATUS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Status feature is not enabled")

    now = datetime.utcnow()

    if cursor:
        try:
            cursor_uuid = uuid.UUID(cursor)
        except ValueError:
            cursor_uuid = None
        if cursor_uuid:
            cursor_post = messaging_repository.get_status_post(db, post_id=cursor_uuid)
            if cursor_post:
                posts = messaging_repository.list_status_feed_posts_before(
                    db,
                    viewer_user_id=current_user.account_id,
                    now_dt=now,
                    limit=limit,
                    cursor_created_at=cursor_post.created_at,
                    cursor_id=cursor_uuid,
                )
            else:
                posts = messaging_repository.list_status_feed_posts(
                    db, viewer_user_id=current_user.account_id, now_dt=now, limit=limit
                )
        else:
            posts = messaging_repository.list_status_feed_posts(
                db, viewer_user_id=current_user.account_id, now_dt=now, limit=limit
            )
    else:
        posts = messaging_repository.list_status_feed_posts(
            db, viewer_user_id=current_user.account_id, now_dt=now, limit=limit
        )

    result = []
    for post in posts:
        result.append(
            {
                "id": str(post.id),
                "owner_user_id": post.owner_user_id,
                "media_meta": post.media_meta,
                "created_at": post.created_at.isoformat() if post.created_at else None,
                "expires_at": post.expires_at.isoformat() if post.expires_at else None,
                "post_epoch": post.post_epoch,
            }
        )
    return {"posts": result}


def mark_status_viewed(db, *, current_user, request):
    import uuid
    from datetime import datetime

    from config import STATUS_ENABLED

    if not STATUS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Status feature is not enabled")

    parsed_ids = []
    for post_id_str in request.post_ids:
        try:
            parsed_ids.append(uuid.UUID(post_id_str))
        except ValueError:
            continue

    if not parsed_ids:
        return {"viewed_post_ids": []}

    allowed_ids = messaging_repository.list_allowed_audience_post_ids(
        db, viewer_user_id=current_user.account_id, post_ids=parsed_ids
    )
    if not allowed_ids:
        return {"viewed_post_ids": []}

    existing_ids = messaging_repository.list_existing_status_views(
        db, viewer_user_id=current_user.account_id, post_ids=list(allowed_ids)
    )

    new_ids = [
        post_id
        for post_id in parsed_ids
        if post_id in allowed_ids and post_id not in existing_ids
    ]
    if not new_ids:
        return {"viewed_post_ids": []}

    now = datetime.utcnow()
    rows = [
        {"post_id": post_id, "viewer_user_id": current_user.account_id, "viewed_at": now}
        for post_id in new_ids
    ]
    messaging_repository.insert_status_views(db, rows=rows)

    db.commit()
    return {"viewed_post_ids": [str(post_id) for post_id in new_ids]}


def delete_status_post(db, *, current_user, post_id: str):
    import uuid

    from config import STATUS_ENABLED

    if not STATUS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Status feature is not enabled")

    try:
        post_uuid = uuid.UUID(post_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid post ID format")

    post = messaging_repository.get_status_post(db, post_id=post_uuid)
    if not post or post.owner_user_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    messaging_repository.delete_status_post_cascade(db, post_id=post_uuid)
    db.delete(post)
    db.commit()
    return {"message": "Post deleted"}


def get_status_presence(db, *, current_user, user_ids):
    from config import STATUS_ENABLED

    if not STATUS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Status feature is not enabled")

    query_user_ids = list(dict.fromkeys(user_ids))
    presences = messaging_repository.list_user_presences(db, user_ids=query_user_ids)
    presence_map = {p.user_id: p for p in presences}

    contact_ids = set(
        messaging_repository.list_user_contacts(db, user_id=current_user.account_id)
    )

    blocked_ids = set()
    blocks = messaging_repository.list_blocks_involving_user(
        db, current_user_id=current_user.account_id, user_ids=query_user_ids
    )
    for blocker_id, blocked_id in blocks:
        if blocker_id == current_user.account_id:
            blocked_ids.add(blocked_id)
        else:
            blocked_ids.add(blocker_id)

    result = []
    for user_id in user_ids:
        if user_id in blocked_ids and user_id != current_user.account_id:
            result.append({"user_id": user_id, "last_seen_at": None, "device_online": False})
            continue

        presence = presence_map.get(user_id)
        if user_id == current_user.account_id:
            last_seen = presence.last_seen_at.isoformat() if presence and presence.last_seen_at else None
            device_online = presence.device_online if presence else False
        else:
            privacy = presence.privacy_settings if presence and presence.privacy_settings else {}
            share_online = privacy.get("share_online", True)
            share_last_seen = privacy.get("share_last_seen", "contacts")
            if share_last_seen == "all":
                share_last_seen = "everyone"
            is_contact = user_id in contact_ids
            device_online = presence.device_online if presence and share_online else False
            if (
                presence
                and presence.last_seen_at
                and (
                    share_last_seen == "everyone"
                    or (share_last_seen == "contacts" and is_contact)
                )
            ):
                last_seen = presence.last_seen_at.isoformat()
            else:
                last_seen = None

        result.append({"user_id": user_id, "last_seen_at": last_seen, "device_online": device_online})

    return {"presence": result}


# --- Global chat ---


async def get_global_chat_messages(db, *, current_user, limit: int, before):
    from datetime import datetime, timedelta

    from fastapi import HTTPException

    from config import GLOBAL_CHAT_ENABLED
    from utils.chat_helpers import get_user_chat_profile_data_bulk
    from utils.chat_redis import get_chat_redis

    if not GLOBAL_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Global chat is disabled")

    messages = messaging_repository.list_global_chat_messages(db, limit=limit, before=before)

    # Update viewer tracking (user is viewing global chat)
    now = datetime.utcnow()
    messaging_repository.upsert_global_chat_viewer_last_seen(
        db, user_id=current_user.account_id, now_dt=now
    )
    db.commit()

    # Online count cached in Redis for 5s
    cutoff_time = now - timedelta(minutes=5)
    online_count = None
    redis_client = await get_chat_redis()
    cache_key = "global_chat:online_count"
    if redis_client:
        try:
            cached_value = await redis_client.get(cache_key)
            if cached_value is not None:
                online_count = int(cached_value)
        except Exception:
            online_count = None

    if online_count is None:
        online_count = messaging_repository.count_global_chat_viewers_since(
            db, cutoff_dt=cutoff_time
        )
        if redis_client:
            try:
                await redis_client.set(cache_key, str(online_count), ex=5)
            except Exception:
                pass

    # Reply message hydration
    reply_message_ids = {msg.reply_to_message_id for msg in messages if msg.reply_to_message_id}
    replied_messages = {}
    if reply_message_ids:
        replied_msgs = messaging_repository.list_global_chat_messages_by_ids(
            db, ids=reply_message_ids
        )
        replied_messages = {msg.id: msg for msg in replied_msgs}

    unique_users = {msg.user for msg in messages if getattr(msg, "user", None)}
    unique_users.update({msg.user for msg in replied_messages.values() if getattr(msg, "user", None)})

    profile_cache = get_user_chat_profile_data_bulk(list(unique_users), db)

    def _display_username(user) -> str:
        if user and user.username and user.username.strip():
            return user.username
        if user and user.email:
            return user.email.split("@")[0]
        return f"User{user.account_id}" if user else "User"

    result_messages = []
    for msg in reversed(messages):
        profile_data = profile_cache.get(
            msg.user_id,
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

        reply_info = None
        if msg.reply_to_message_id and msg.reply_to_message_id in replied_messages:
            replied_msg = replied_messages[msg.reply_to_message_id]
            replied_profile = profile_cache.get(
                replied_msg.user_id,
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
                "sender_id": replied_msg.user_id,
                "sender_username": _display_username(getattr(replied_msg, "user", None)),
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
                "user_id": msg.user_id,
                "username": _display_username(getattr(msg, "user", None)),
                "profile_pic": profile_data["profile_pic_url"],
                "avatar_url": profile_data["avatar_url"],
                "frame_url": profile_data["frame_url"],
                "badge": profile_data["badge"],
                "message": msg.message,
                "created_at": msg.created_at.isoformat(),
                "reply_to": reply_info,
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
            }
        )

    unread_messages_count = messaging_repository.count_total_unread_private_messages(
        db, user_id=current_user.account_id
    )
    friend_requests_count = messaging_repository.count_pending_private_chat_requests(
        db, user_id=current_user.account_id
    )

    return {
        "messages": result_messages,
        "online_count": online_count,
        "unread_messages_count": unread_messages_count,
        "friend_requests_count": friend_requests_count,
    }


def cleanup_global_chat_messages(db, *, current_user):
    from datetime import datetime, timedelta

    from fastapi import HTTPException
    from routers.dependencies import verify_admin

    from config import GLOBAL_CHAT_ENABLED, GLOBAL_CHAT_RETENTION_DAYS

    verify_admin(db, current_user)

    if not GLOBAL_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Global chat is disabled")

    cutoff_date = datetime.utcnow() - timedelta(days=GLOBAL_CHAT_RETENTION_DAYS)
    deleted_count = messaging_repository.delete_global_chat_messages_before(
        db, cutoff_dt=cutoff_date
    )
    db.commit()
    logger.info(
        f"Cleaned up {deleted_count} old global chat messages (older than {GLOBAL_CHAT_RETENTION_DAYS} days)"
    )
    return {"deleted_count": deleted_count, "cutoff_date": cutoff_date.isoformat()}


async def send_global_chat_message(db, *, current_user, request):
    from datetime import datetime, timedelta

    from fastapi import HTTPException

    from core.config import (
        GLOBAL_CHAT_BURST_WINDOW_SECONDS,
        GLOBAL_CHAT_ENABLED,
        GLOBAL_CHAT_MAX_MESSAGES_PER_BURST,
        GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE,
    )
    from utils.chat_helpers import get_user_chat_profile_data
    from utils.chat_redis import enqueue_chat_event
    from utils.message_sanitizer import sanitize_message

    if not GLOBAL_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Global chat is disabled")

    message_text = sanitize_message(request.message)
    if not message_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message cannot be empty")

    if request.client_message_id:
        existing_message = messaging_repository.get_global_chat_message_by_client_id(
            db,
            user_id=current_user.account_id,
            client_message_id=request.client_message_id,
        )
        if existing_message:
            return {
                "response": {
                    "message_id": existing_message.id,
                    "created_at": existing_message.created_at.isoformat(),
                    "duplicate": True,
                },
                "event_enqueued": True,
                "pusher_args": None,
                "push_args": None,
            }

    minute_rl = default_rate_limiter.allow(
        key=f"rl:global_chat:minute:{current_user.account_id}",
        limit=GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE,
        window_seconds=60,
    )
    if not minute_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
            ),
            headers={"X-Retry-After": str(minute_rl.retry_after_seconds)},
        )
    burst_rl = default_rate_limiter.allow(
        key=f"rl:global_chat:burst:{current_user.account_id}",
        limit=GLOBAL_CHAT_MAX_MESSAGES_PER_BURST,
        window_seconds=GLOBAL_CHAT_BURST_WINDOW_SECONDS,
    )
    if not burst_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Burst rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_BURST} messages "
                f"per {GLOBAL_CHAT_BURST_WINDOW_SECONDS} seconds."
            ),
            headers={"X-Retry-After": str(burst_rl.retry_after_seconds)},
        )

    reply_to_message = None
    if request.reply_to_message_id:
        reply_to_message = messaging_repository.get_global_chat_message_with_user(
            db, message_id=request.reply_to_message_id
        )
        if not reply_to_message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {request.reply_to_message_id} not found",
            )

    new_message = messaging_repository.create_global_chat_message(
        db,
        user_id=current_user.account_id,
        message=message_text,
        client_message_id=request.client_message_id,
        reply_to_message_id=request.reply_to_message_id,
    )

    messaging_repository.upsert_global_chat_viewer_last_seen(
        db, user_id=current_user.account_id, now_dt=datetime.utcnow()
    )

    db.commit()
    db.refresh(new_message)

    profile_data = get_user_chat_profile_data(current_user, db)

    def _display_username(user) -> str:
        if user and user.username and user.username.strip():
            return user.username
        if user and user.email:
            return user.email.split("@")[0]
        return f"User{user.account_id}" if user else "User"

    reply_info = None
    if reply_to_message and getattr(reply_to_message, "user", None):
        replied_sender_profile = get_user_chat_profile_data(reply_to_message.user, db)
        reply_info = {
            "message_id": reply_to_message.id,
            "sender_id": reply_to_message.user_id,
            "sender_username": _display_username(reply_to_message.user),
            "message": reply_to_message.message,
            "sender_profile_pic": replied_sender_profile["profile_pic_url"],
            "sender_avatar_url": replied_sender_profile["avatar_url"],
            "sender_frame_url": replied_sender_profile["frame_url"],
            "sender_badge": replied_sender_profile["badge"],
            "created_at": reply_to_message.created_at.isoformat(),
        }

    username = _display_username(current_user)

    pusher_args = {
        "message_id": new_message.id,
        "user_id": current_user.account_id,
        "username": username,
        "profile_pic": profile_data["profile_pic_url"],
        "avatar_url": profile_data["avatar_url"],
        "frame_url": profile_data["frame_url"],
        "badge": profile_data["badge"],
        "message": new_message.message,
        "created_at": new_message.created_at.isoformat(),
        "reply_to": reply_info,
    }
    push_args = {
        "message_id": new_message.id,
        "sender_id": current_user.account_id,
        "sender_username": username,
        "message": new_message.message,
        "created_at": new_message.created_at.isoformat(),
    }

    event_enqueued = await enqueue_chat_event(
        "global_message",
        {"pusher_args": pusher_args, "push_args": push_args},
    )

    return {
        "response": {
            "message_id": new_message.id,
            "created_at": new_message.created_at.isoformat(),
            "duplicate": False,
        },
        "event_enqueued": bool(event_enqueued),
        "pusher_args": pusher_args,
        "push_args": push_args,
    }


# --- Private chat ---


def _display_username(user) -> str:
    if user and user.username and user.username.strip():
        return user.username
    if user and user.email:
        return user.email.split("@")[0]
    return f"User{user.account_id}" if user else "User"


def _ensure_datetime(value):
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
    profile_pic_url,
    avatar_url,
    frame_url,
    badge,
    message: str,
    created_at,
    is_new_conversation: bool,
    reply_to=None,
):
    from utils.pusher_client import publish_chat_message_sync

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
    except Exception as exc:
        logger.error(f"Failed to publish private chat message to Pusher: {exc}")


def publish_to_pusher_global(
    message_id: int,
    user_id: int,
    username: str,
    profile_pic,
    avatar_url,
    frame_url,
    badge,
    message: str,
    created_at,
    reply_to=None,
):
    from utils.pusher_client import publish_chat_message_sync

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
    except Exception as exc:
        logger.error(f"Failed to publish global chat message to Pusher: {exc}")


def send_push_for_global_chat_sync(
    message_id: int,
    sender_id: int,
    sender_username: str,
    message: str,
    created_at,
):
    import asyncio
    from datetime import timedelta

    from db import get_db
    from models import OneSignalPlayer
    from utils.chat_mute import get_muted_user_ids
    from utils.notification_storage import create_notifications_batch
    from utils.onesignal_client import (
        ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS,
        send_push_notification_async,
    )

    db = next(get_db())
    try:
        created_at_dt = _ensure_datetime(created_at)
        all_players = (
            messaging_repository.query(db, OneSignalPlayer)
            .filter(OneSignalPlayer.user_id != sender_id, OneSignalPlayer.is_valid == True)
            .all()
        )
        if not all_players:
            return

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

        batch_size = 2000
        active_batches = []
        inactive_batches = []
        active_current = []
        inactive_current = []

        for player in all_players:
            user_id = player.user_id
            if user_id in muted_user_ids:
                continue

            if user_id in active_user_ids:
                active_current.append(player.player_id)
                if len(active_current) >= batch_size:
                    active_batches.append(active_current)
                    active_current = []
            else:
                inactive_current.append(player.player_id)
                if len(inactive_current) >= batch_size:
                    inactive_batches.append(inactive_current)
                    inactive_current = []

        if active_current:
            active_batches.append(active_current)
        if inactive_current:
            inactive_batches.append(inactive_current)

        heading = "Global Chat"
        content = f"{sender_username}: {message[:100]}"
        data = {
            "type": "global_chat",
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_username": sender_username,
            "message": message,
            "created_at": created_at_dt.isoformat(),
        }

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        for batch in active_batches:
            loop.run_until_complete(
                send_push_notification_async(
                    player_ids=batch,
                    heading=heading,
                    content=content,
                    data=data,
                    is_in_app_notification=True,
                )
            )

        for batch in inactive_batches:
            loop.run_until_complete(
                send_push_notification_async(
                    player_ids=batch,
                    heading=heading,
                    content=content,
                    data=data,
                    is_in_app_notification=False,
                )
            )

        notifications = []
        for player in all_players:
            if player.user_id == sender_id:
                continue
            if player.user_id in muted_user_ids:
                continue
            notifications.append(
                {
                    "user_id": player.user_id,
                    "title": heading,
                    "body": content,
                    "type": "chat_global",
                    "data": data,
                }
            )
        if notifications:
            create_notifications_batch(db, notifications)
            db.commit()
    except Exception as exc:
        logger.error(f"Failed to send global chat push notifications: {exc}")
    finally:
        db.close()


def send_push_if_needed_sync(
    recipient_id: int,
    conversation_id: int,
    sender_id: int,
    sender_username: str,
    message: str,
    is_new_conversation: bool,
):
    import asyncio

    from db import get_db
    from utils.chat_mute import is_user_muted_for_private_chat
    from utils.notification_storage import create_notification
    from utils.onesignal_client import (
        get_user_player_ids,
        is_user_active,
        send_push_notification_async,
    )

    db = next(get_db())
    try:
        if is_user_muted_for_private_chat(sender_id, recipient_id, db):
            logger.debug(
                "User %s is muted by %s, skipping push notification",
                sender_id,
                recipient_id,
            )
            return

        player_ids = get_user_player_ids(recipient_id, db, valid_only=True)
        if not player_ids:
            logger.debug("No valid OneSignal players for user %s", recipient_id)
            return

        is_active = is_user_active(recipient_id, db)

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
            content = message[:100]
            data = {
                "type": "private_message",
                "conversation_id": conversation_id,
                "sender_id": sender_id,
            }

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
    except Exception as exc:
        logger.error(f"Failed to send push notification: {exc}")
    finally:
        db.close()


def _get_user_presence_info(db, *, user_id: int, conversation_id: Optional[int] = None):
    from sqlalchemy.exc import IntegrityError

    if not PRESENCE_ENABLED:
        return False, None

    presence = messaging_repository.get_user_presence(db, user_id=user_id)
    if not presence:
        presence = messaging_repository.create_user_presence(
            db,
            user_id=user_id,
            last_seen_at=None,
            device_online=False,
            privacy_settings={
                "share_last_seen": "contacts",
                "share_online": True,
                "read_receipts": True,
            },
        )
        try:
            db.commit()
            db.refresh(presence)
        except IntegrityError:
            db.rollback()
            presence = messaging_repository.get_user_presence(db, user_id=user_id)

    privacy = presence.privacy_settings or {}
    share_online = privacy.get("share_online", True)
    share_last_seen = privacy.get("share_last_seen", "contacts")
    if share_last_seen == "all":
        share_last_seen = "everyone"

    is_online = presence.device_online if share_online else False
    last_seen = None

    if share_last_seen in ["everyone", "contacts"]:
        if presence.last_seen_at:
            last_seen = presence.last_seen_at.isoformat()
        else:
            if conversation_id:
                last_message = (
                    messaging_repository.get_latest_private_chat_message_for_sender_in_conversation(
                        db, conversation_id=conversation_id, sender_id=user_id
                    )
                )
                if last_message:
                    last_seen = last_message.created_at.isoformat()
            else:
                last_message = messaging_repository.get_latest_private_chat_message_for_sender(
                    db, sender_id=user_id
                )
                if last_message:
                    last_seen = last_message.created_at.isoformat()

    return is_online, last_seen


def _batch_get_user_profile_data(users, db):
    from sqlalchemy import and_
    from sqlalchemy.orm import joinedload

    from models import (
        Avatar,
        Frame,
        SubscriptionPlan,
        TriviaModeConfig,
        UserSubscription,
    )
    from utils.storage import presign_get
    from utils.user_level_service import get_level_progress_for_users

    if not users:
        return {}

    profile_cache = {}
    missing_users = []

    for user in users:
        cache_key = f"private_chat_profile:{user.account_id}"
        cached = default_cache.get(cache_key)
        if cached is not None:
            profile_cache[user.account_id] = cached
        else:
            missing_users.append(user)

    if not missing_users:
        return profile_cache

    users = missing_users
    user_ids = [u.account_id for u in users]

    avatar_ids = {u.selected_avatar_id for u in users if u.selected_avatar_id}
    avatars = {}
    if avatar_ids:
        avatars = {
            a.id: a
            for a in messaging_repository.query(db, Avatar).filter(Avatar.id.in_(list(avatar_ids))).all()
        }

    frame_ids = {u.selected_frame_id for u in users if u.selected_frame_id}
    frames = {}
    if frame_ids:
        frames = {
            f.id: f
            for f in messaging_repository.query(db, Frame).filter(Frame.id.in_(list(frame_ids))).all()
        }

    badge_ids = {u.badge_id for u in users if u.badge_id}
    badges = {}
    if badge_ids:
        mode_configs = (
            messaging_repository.query(db, TriviaModeConfig)
            .filter(
                TriviaModeConfig.mode_id.in_(list(badge_ids)),
                TriviaModeConfig.badge_image_url.isnot(None),
            )
            .all()
        )
        badges = {mc.mode_id: mc for mc in mode_configs}

    active_subscriptions = {}
    if user_ids:
        subs = (
            messaging_repository.query(db, UserSubscription)
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
            active_subscriptions.setdefault(sub.user_id, []).append(sub)

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
        for mc in messaging_repository.query(db, TriviaModeConfig)
        .filter(
            TriviaModeConfig.mode_id.in_(list(subscription_badge_ids)),
            TriviaModeConfig.badge_image_url.isnot(None),
        )
        .all()
    }
    name_based_badges = {
        mc.mode_id: mc
        for mc in messaging_repository.query(db, TriviaModeConfig)
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
            except Exception as exc:
                logger.warning(f"Failed to presign avatar {avatar_id}: {exc}")

    for frame_id, frame in frames.items():
        bucket = getattr(frame, "bucket", None)
        object_key = getattr(frame, "object_key", None)
        if bucket and object_key:
            try:
                presigned_frames[frame_id] = presign_get(
                    bucket, object_key, expires=900
                )
            except Exception as exc:
                logger.warning(f"Failed to presign frame {frame_id}: {exc}")

    level_progress_map = get_level_progress_for_users(users, db)

    for user in users:
        avatar_url = None
        if user.selected_avatar_id and user.selected_avatar_id in presigned_avatars:
            avatar_url = presigned_avatars[user.selected_avatar_id]

        frame_url = None
        if user.selected_frame_id and user.selected_frame_id in presigned_frames:
            frame_url = presigned_frames[user.selected_frame_id]

        badge_info = None
        if user.badge_id and user.badge_id in badges:
            mode_config = badges[user.badge_id]
            badge_info = {
                "id": mode_config.mode_id,
                "name": mode_config.mode_name,
                "image_url": mode_config.badge_image_url,
            }

        subscription_badges = []
        user_subs = active_subscriptions.get(user.account_id, [])
        for sub in user_subs:
            plan = sub.plan
            if not plan:
                continue

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
                    for _, mc in subscription_badges_dict.items():
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

            if (
                getattr(plan, "unit_amount_minor", None) == 1000
                or getattr(plan, "price_usd", None) == 10.0
            ):
                silver_badge = subscription_badges_dict.get(
                    "silver"
                ) or subscription_badges_dict.get("silver_badge")
                if not silver_badge:
                    for _, mc in subscription_badges_dict.items():
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

        level_progress = level_progress_map.get(
            user.account_id,
            {"level": user.level if user.level else 1, "level_progress": "0/100"},
        )

        profile = {
            "profile_pic_url": user.profile_pic_url,
            "avatar_url": avatar_url,
            "frame_url": frame_url,
            "badge": badge_info,
            "subscription_badges": subscription_badges,
            "level": level_progress["level"],
            "level_progress": level_progress["level_progress"],
        }
        profile_cache[user.account_id] = profile
        default_cache.set(
            f"private_chat_profile:{user.account_id}",
            profile,
            ttl_seconds=PRIVATE_CHAT_PROFILE_CACHE_SECONDS,
        )

    return profile_cache


async def send_private_message(db, *, current_user, request, background_tasks: BackgroundTasks):
    from sqlalchemy.exc import IntegrityError

    from utils.chat_helpers import get_user_chat_profile_data
    from utils.chat_redis import enqueue_chat_event
    from utils.message_sanitizer import sanitize_message

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    if request.recipient_id == current_user.account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot message yourself")

    if check_blocked(db, current_user.account_id, request.recipient_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is blocked")

    recipient = messaging_repository.get_user_by_account_id(
        db, user_id=request.recipient_id
    )
    if not recipient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipient not found")

    admin_user_id = messaging_repository.get_admin_user_id(db)
    is_admin_conversation = admin_user_id in [
        current_user.account_id,
        request.recipient_id,
    ] if admin_user_id else False

    user_ids = sorted([current_user.account_id, request.recipient_id])
    conversation = messaging_repository.get_private_chat_conversation_by_users(
        db, user1_id=user_ids[0], user2_id=user_ids[1]
    )

    is_new_conversation = False
    if not conversation:
        conversation = messaging_repository.create_private_chat_conversation(
            db,
            user1_id=user_ids[0],
            user2_id=user_ids[1],
            requested_by=current_user.account_id,
            status="accepted" if is_admin_conversation else "pending",
            responded_at=datetime.utcnow() if is_admin_conversation else None,
        )
        try:
            db.flush()
            is_new_conversation = True
        except IntegrityError:
            db.rollback()
            conversation = messaging_repository.get_private_chat_conversation_by_users(
                db, user1_id=user_ids[0], user2_id=user_ids[1]
            )
            if not conversation:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create conversation",
                )

    if is_admin_conversation and conversation.status == "pending":
        conversation.status = "accepted"
        conversation.responded_at = datetime.utcnow()

    if conversation.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not accepting private messages.",
        )

    existing_message_count = messaging_repository.count_private_chat_messages(
        db, conversation_id=conversation.id
    )
    is_first_message = existing_message_count == 0

    if conversation.status == "pending":
        if conversation.requested_by != current_user.account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Chat request must be accepted before sending messages. Please accept the chat request first.",
            )

        if conversation.requested_by == current_user.account_id and not is_first_message:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Chat request must be accepted before sending more messages. Please wait for the recipient to accept.",
            )

    sanitized_message = sanitize_message(request.message)
    if not sanitized_message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message cannot be empty")

    if request.client_message_id:
        existing_message = messaging_repository.get_private_chat_message_by_client_id(
            db,
            conversation_id=conversation.id,
            sender_id=current_user.account_id,
            client_message_id=request.client_message_id,
        )
        if existing_message:
            return {
                "conversation_id": conversation.id,
                "message_id": existing_message.id,
                "status": conversation.status,
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True,
            }

    burst_rl = default_rate_limiter.allow(
        key=f"rl:private_chat:burst:{current_user.account_id}",
        limit=PRIVATE_CHAT_MAX_MESSAGES_PER_BURST,
        window_seconds=PRIVATE_CHAT_BURST_WINDOW_SECONDS,
    )
    if not burst_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Burst rate limit exceeded. Maximum "
                f"{PRIVATE_CHAT_MAX_MESSAGES_PER_BURST} messages per "
                f"{PRIVATE_CHAT_BURST_WINDOW_SECONDS} seconds."
            ),
            headers={"X-Retry-After": str(burst_rl.retry_after_seconds)},
        )

    minute_rl = default_rate_limiter.allow(
        key=f"rl:private_chat:minute:{current_user.account_id}",
        limit=PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE,
        window_seconds=60,
    )
    if not minute_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Rate limit exceeded. Maximum "
                f"{PRIVATE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
            ),
            headers={"X-Retry-After": str(minute_rl.retry_after_seconds)},
        )

    reply_to_message = None
    if request.reply_to_message_id:
        reply_to_message = messaging_repository.get_private_chat_message_in_conversation(
            db,
            message_id=request.reply_to_message_id,
            conversation_id=conversation.id,
        )
        if not reply_to_message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {request.reply_to_message_id} not found in this conversation",
            )

    new_message = messaging_repository.create_private_chat_message(
        db,
        conversation_id=conversation.id,
        sender_id=current_user.account_id,
        message=sanitized_message,
        status="sent",
        client_message_id=request.client_message_id,
        reply_to_message_id=request.reply_to_message_id,
    )
    conversation.last_message_at = datetime.utcnow()

    if PRESENCE_ENABLED:
        sender_presence = messaging_repository.get_user_presence(
            db, user_id=current_user.account_id
        )
        if sender_presence:
            sender_presence.last_seen_at = datetime.utcnow()
        else:
            messaging_repository.create_user_presence(
                db,
                user_id=current_user.account_id,
                last_seen_at=datetime.utcnow(),
                device_online=False,
                privacy_settings={
                    "share_last_seen": "contacts",
                    "share_online": True,
                    "read_receipts": True,
                },
            )

    db.commit()
    db.refresh(new_message)

    profile_data = get_user_chat_profile_data(current_user, db)

    reply_info = None
    if reply_to_message:
        replied_sender_profile = get_user_chat_profile_data(reply_to_message.sender, db)
        reply_info = {
            "message_id": reply_to_message.id,
            "sender_id": reply_to_message.sender_id,
            "sender_username": _display_username(reply_to_message.sender),
            "message": reply_to_message.message,
            "sender_profile_pic": replied_sender_profile["profile_pic_url"],
            "sender_avatar_url": replied_sender_profile["avatar_url"],
            "sender_frame_url": replied_sender_profile["frame_url"],
            "sender_badge": replied_sender_profile["badge"],
            "created_at": reply_to_message.created_at.isoformat(),
        }

    username = _display_username(current_user)
    is_admin_sender = admin_user_id == current_user.account_id if admin_user_id else False
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


async def accept_reject_private_chat(db, *, current_user, request, background_tasks: BackgroundTasks):
    from utils.pusher_client import publish_chat_message_sync

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    conversation = messaging_repository.get_private_chat_conversation(
        db, conversation_id=request.conversation_id
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    admin_user_id = messaging_repository.get_admin_user_id(db)
    if admin_user_id in [conversation.user1_id, conversation.user2_id]:
        if conversation.status == "pending":
            conversation.status = "accepted"
            conversation.responded_at = datetime.utcnow()
            db.commit()
        return {"conversation_id": conversation.id, "status": conversation.status}

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if current_user.account_id == conversation.requested_by:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot accept/reject your own request",
        )

    if conversation.status != "pending":
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid action. Use 'accept' or 'reject'",
        )

    conversation.responded_at = datetime.utcnow()
    db.commit()

    background_tasks.add_task(
        publish_chat_message_sync,
        f"private-conversation-{conversation.id}",
        "conversation-updated",
        {"conversation_id": conversation.id, "status": conversation.status},
    )

    return {"conversation_id": conversation.id, "status": conversation.status}


async def list_private_conversations(db, *, current_user):
    from sqlalchemy.exc import IntegrityError

    from utils.chat_helpers import get_user_chat_profile_data_bulk

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    conversations = messaging_repository.list_private_chat_conversations_for_user(
        db, user_id=current_user.account_id
    )
    if not conversations:
        return {"conversations": []}

    admin_user_id = messaging_repository.get_admin_user_id(db)

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

    peer_users = messaging_repository.list_users_by_account_ids(db, user_ids=peer_ids)
    peer_user_map = {user.account_id: user for user in peer_users}

    unread_counts = {}
    if conv_ids_user1:
        unread_user1 = messaging_repository.list_unread_counts_for_user_as_user1(
            db, conversation_ids=conv_ids_user1, user_id=current_user.account_id
        )
        unread_counts.update({cid: count for cid, count in unread_user1})
    if conv_ids_user2:
        unread_user2 = messaging_repository.list_unread_counts_for_user_as_user2(
            db, conversation_ids=conv_ids_user2, user_id=current_user.account_id
        )
        unread_counts.update({cid: count for cid, count in unread_user2})

    presence_rows = messaging_repository.list_user_presence_rows(
        db, user_ids=peer_ids
    )
    presence_map = {p.user_id: p for p in presence_rows}
    missing_ids = peer_ids - set(presence_map.keys())
    if missing_ids:
        for user_id in missing_ids:
            presence = messaging_repository.create_user_presence(
                db,
                user_id=user_id,
                last_seen_at=None,
                device_online=False,
                privacy_settings={
                    "share_last_seen": "contacts",
                    "share_online": True,
                    "read_receipts": True,
                },
            )
            presence_map[user_id] = presence
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        presence_rows = messaging_repository.list_user_presence_rows(
            db, user_ids=peer_ids
        )
        presence_map = {p.user_id: p for p in presence_rows}

    last_message_map = {}
    if conversations and peer_ids:
        last_messages = messaging_repository.list_private_chat_last_messages(
            db,
            conversation_ids=[conv.id for conv in conversations],
            peer_ids=peer_ids,
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
        privacy = presence.privacy_settings if presence and presence.privacy_settings else {}
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
                "peer_username": _display_username(peer_user),
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


async def get_private_messages(db, *, current_user, conversation_id: int, limit: int):
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    conversation = messaging_repository.get_private_chat_conversation(
        db, conversation_id=conversation_id
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if conversation.status == "pending":
        admin_user_id = messaging_repository.get_admin_user_id(db)
        if admin_user_id not in [conversation.user1_id, conversation.user2_id]:
            if conversation.requested_by != current_user.account_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "Chat request must be accepted before viewing messages. "
                        "Please accept the chat request first."
                    ),
                )

    last_read_id = (
        conversation.last_read_message_id_user1
        if conversation.user1_id == current_user.account_id
        else conversation.last_read_message_id_user2
    )

    messages = messaging_repository.list_private_chat_messages_with_sender(
        db, conversation_id=conversation_id, limit=limit
    )

    peer_id = (
        conversation.user2_id
        if conversation.user1_id == current_user.account_id
        else conversation.user1_id
    )
    peer_online, peer_last_seen = _get_user_presence_info(
        db, user_id=peer_id, conversation_id=conversation_id
    )

    unique_users = {msg.sender for msg in messages if msg.sender}
    reply_message_ids = {
        msg.reply_to_message_id for msg in messages if msg.reply_to_message_id
    }

    replied_messages = {}
    if reply_message_ids:
        replied_msgs = messaging_repository.list_private_chat_messages_with_sender_by_ids(
            db, conversation_id=conversation_id, message_ids=reply_message_ids
        )
        replied_messages = {msg.id: msg for msg in replied_msgs}
        unique_users.update({msg.sender for msg in replied_msgs if msg.sender})

    profile_cache = _batch_get_user_profile_data(list(unique_users), db)

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
                "sender_username": _display_username(replied_msg.sender),
                "message": replied_msg.message,
                "sender_profile_pic": replied_profile["profile_pic_url"],
                "sender_avatar_url": replied_profile["avatar_url"],
                "sender_frame_url": replied_profile["frame_url"],
                "sender_badge": replied_profile["badge"],
                "created_at": replied_msg.created_at.isoformat(),
                "sender_level": replied_profile.get("level", 1),
                "sender_level_progress": replied_profile.get(
                    "level_progress", "0/100"
                ),
            }

        result_messages.append(
            {
                "id": msg.id,
                "sender_id": msg.sender_id,
                "sender_username": _display_username(msg.sender),
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


async def mark_conversation_read(
    db,
    *,
    current_user,
    conversation_id: int,
    message_id: Optional[int],
    background_tasks: BackgroundTasks,
):
    from utils.pusher_client import publish_chat_message_sync

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    conversation = messaging_repository.get_private_chat_conversation(
        db, conversation_id=conversation_id
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if message_id is None:
        latest_message = messaging_repository.get_latest_private_chat_message(
            db, conversation_id=conversation_id
        )
        if latest_message:
            message_id = latest_message.id
        else:
            return {"conversation_id": conversation_id, "last_read_message_id": None}

    if conversation.user1_id == current_user.account_id:
        conversation.last_read_message_id_user1 = message_id
    else:
        conversation.last_read_message_id_user2 = message_id

    db.commit()

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


async def get_private_conversation(db, *, current_user, conversation_id: int):
    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    conversation = messaging_repository.get_private_chat_conversation(
        db, conversation_id=conversation_id
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    peer_id = (
        conversation.user2_id
        if conversation.user1_id == current_user.account_id
        else conversation.user1_id
    )
    peer_user = messaging_repository.get_user_by_account_id(db, user_id=peer_id)

    if PRESENCE_ENABLED:
        current_user_presence = messaging_repository.get_user_presence(
            db, user_id=current_user.account_id
        )
        if current_user_presence:
            current_user_presence.last_seen_at = datetime.utcnow()
        else:
            messaging_repository.create_user_presence(
                db,
                user_id=current_user.account_id,
                last_seen_at=datetime.utcnow(),
                device_online=False,
                privacy_settings={
                    "share_last_seen": "contacts",
                    "share_online": True,
                    "read_receipts": True,
                },
            )
        db.commit()

    peer_online, peer_last_seen = _get_user_presence_info(
        db, user_id=peer_id, conversation_id=conversation_id
    )

    from utils.chat_helpers import get_user_chat_profile_data

    peer_profile_data = (
        get_user_chat_profile_data(peer_user, db)
        if peer_user
        else {"profile_pic_url": None, "avatar_url": None, "frame_url": None}
    )

    return {
        "conversation_id": conversation.id,
        "peer_user_id": peer_id,
        "peer_username": _display_username(peer_user) if peer_user else None,
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


async def send_private_typing_indicator(
    db, *, current_user, conversation_id: int, background_tasks: BackgroundTasks
):
    from utils.chat_redis import should_emit_typing_event
    from utils.pusher_client import publish_chat_message_sync

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    conversation = messaging_repository.get_private_chat_conversation(
        db, conversation_id=conversation_id
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if conversation.status != "accepted":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Conversation not accepted")

    channel_key = f"conversation:{conversation_id}"
    should_emit = await should_emit_typing_event(channel_key, current_user.account_id)
    if not should_emit:
        return {"status": "typing"}

    background_tasks.add_task(
        publish_chat_message_sync,
        f"private-conversation-{conversation_id}",
        "typing",
        {
            "conversation_id": conversation_id,
            "user_id": current_user.account_id,
            "username": _display_username(current_user),
        },
    )

    return {"status": "typing"}


async def send_private_typing_stop(
    db, *, current_user, conversation_id: int, background_tasks: BackgroundTasks
):
    from utils.chat_redis import clear_typing_event
    from utils.pusher_client import publish_chat_message_sync

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    conversation = messaging_repository.get_private_chat_conversation(
        db, conversation_id=conversation_id
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    channel_key = f"conversation:{conversation_id}"
    await clear_typing_event(channel_key, current_user.account_id)

    background_tasks.add_task(
        publish_chat_message_sync,
        f"private-conversation-{conversation_id}",
        "typing-stop",
        {"conversation_id": conversation_id, "user_id": current_user.account_id},
    )

    return {"status": "stopped"}


async def mark_private_message_delivered(
    db, *, current_user, message_id: int, background_tasks: BackgroundTasks
):
    from utils.pusher_client import publish_chat_message_sync

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    message = messaging_repository.get_private_chat_message(db, message_id=message_id)
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    conversation = messaging_repository.get_private_chat_conversation(
        db, conversation_id=message.conversation_id
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if current_user.account_id not in [conversation.user1_id, conversation.user2_id]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

    if message.sender_id == current_user.account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot mark own message as delivered",
        )

    if message.status == "sent":
        message.status = "delivered"
        message.delivered_at = datetime.utcnow()
        db.commit()

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


def block_private_chat_user(db, *, current_user, blocked_user_id: int):
    from datetime import datetime

    from fastapi import HTTPException

    from config import PRIVATE_CHAT_ENABLED

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    if blocked_user_id == current_user.account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot block yourself")

    blocked_user = messaging_repository.get_user_by_account_id(db, user_id=blocked_user_id)
    if not blocked_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing_block = messaging_repository.get_private_chat_block(
        db, blocker_id=current_user.account_id, blocked_id=blocked_user_id
    )
    if existing_block:
        return {"success": True, "message": "User already blocked"}

    messaging_repository.create_private_chat_block(
        db,
        blocker_id=current_user.account_id,
        blocked_id=blocked_user_id,
        created_at=datetime.utcnow(),
    )

    pending_conversations = messaging_repository.list_pending_private_chat_conversations_between(
        db, user_a=current_user.account_id, user_b=blocked_user_id
    )
    for conv in pending_conversations:
        conv.status = "rejected"
        conv.responded_at = datetime.utcnow()

    db.commit()
    logger.info(f"User {current_user.account_id} blocked user {blocked_user_id}")
    return {"success": True, "message": "User blocked successfully"}


def unblock_private_chat_user(db, *, current_user, blocked_user_id: int):
    from fastapi import HTTPException

    from config import PRIVATE_CHAT_ENABLED

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    block = messaging_repository.get_private_chat_block(
        db, blocker_id=current_user.account_id, blocked_id=blocked_user_id
    )
    if not block:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not blocked")

    db.delete(block)
    db.commit()
    logger.info(f"User {current_user.account_id} unblocked user {blocked_user_id}")
    return {"success": True, "message": "User unblocked successfully"}


def list_private_chat_blocks(db, *, current_user):
    from fastapi import HTTPException

    from config import PRIVATE_CHAT_ENABLED

    if not PRIVATE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Private chat is disabled")

    blocks = messaging_repository.list_blocks_for_user(db, blocker_id=current_user.account_id)
    blocked_ids = [block.blocked_id for block in blocks]

    users = messaging_repository.list_users_by_account_ids(db, user_ids=blocked_ids)
    user_map = {user.account_id: user for user in users}

    def _display_username(user) -> str:
        if user.username and user.username.strip():
            return user.username
        if user.email:
            return user.email.split("@")[0]
        return f"User{user.account_id}"

    blocked_users = []
    for block in blocks:
        blocked_user = user_map.get(block.blocked_id)
        if not blocked_user:
            continue
        blocked_users.append(
            {
                "user_id": blocked_user.account_id,
                "username": _display_username(blocked_user),
                "blocked_at": block.created_at.isoformat(),
            }
        )

    return {"blocked_users": blocked_users}


# --- DM privacy blocks ---


def dm_block_user(db, *, current_user, blocked_user_id: int):
    from datetime import datetime

    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    if blocked_user_id == current_user.account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot block yourself")

    blocked_user = messaging_repository.get_user_by_account_id(db, user_id=blocked_user_id)
    if not blocked_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing = messaging_repository.get_block(
        db, blocker_id=current_user.account_id, blocked_id=blocked_user_id
    )
    if existing:
        return {"success": True, "message": "User already blocked"}

    try:
        messaging_repository.create_block(
            db,
            blocker_id=current_user.account_id,
            blocked_id=blocked_user_id,
            created_at=datetime.utcnow(),
        )
        db.commit()
    except Exception:
        db.rollback()
        # likely unique constraint / race; treat idempotently
        return {"success": True, "message": "User already blocked"}

    logger.info(f"User {current_user.account_id} blocked user {blocked_user_id}")
    return {"success": True, "message": "User blocked successfully"}


def dm_unblock_user(db, *, current_user, blocked_user_id: int):
    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    block = messaging_repository.get_block(
        db, blocker_id=current_user.account_id, blocked_id=blocked_user_id
    )
    if not block:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not blocked")

    db.delete(block)
    db.commit()
    logger.info(f"User {current_user.account_id} unblocked user {blocked_user_id}")
    return {"success": True, "message": "User unblocked successfully"}


def dm_list_blocks(db, *, current_user, limit: int, offset: int):
    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED
    from core.users import get_users_by_ids

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    blocks = messaging_repository.list_blocks(
        db, blocker_id=current_user.account_id, limit=limit, offset=offset
    )
    blocked_ids = [b.blocked_id for b in blocks]
    users = get_users_by_ids(db, account_ids=blocked_ids) if blocked_ids else []
    users_by_id = {u.account_id: u for u in users}
    blocked_users = []
    for block in blocks:
        blocked_user = users_by_id.get(block.blocked_id)
        if not blocked_user:
            continue
        blocked_users.append(
            {
                "user_id": blocked_user.account_id,
                "username": blocked_user.username,
                "blocked_at": block.created_at.isoformat(),
            }
        )
    return {"blocked_users": blocked_users}


# --- DM conversations ---


def create_or_find_dm_conversation(db, *, current_user, peer_user_id: int):
    import uuid
    from datetime import datetime

    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from config import E2EE_DM_ENABLED
    from utils.chat_blocking import check_blocked

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    if peer_user_id == current_user.account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create conversation with yourself",
        )

    peer_user = messaging_repository.get_user_by_account_id(db, user_id=peer_user_id)
    if not peer_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Peer user not found")

    if check_blocked(db, current_user.account_id, peer_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot create conversation with blocked user",
        )

    user_ids = sorted([current_user.account_id, peer_user_id])
    pair_key = f"{user_ids[0]}:{user_ids[1]}"

    existing = messaging_repository.get_dm_conversation_by_pair_key(db, pair_key=pair_key)
    if not existing:
        existing = messaging_repository.find_dm_conversation_between_users(
            db, user_ids=user_ids
        )

    if existing:
        participants = messaging_repository.list_dm_participants(
            db, conversation_id=existing.id
        )
        return {
            "conversation_id": str(existing.id),
            "created_at": existing.created_at.isoformat(),
            "participants": [
                {
                    "user_id": p.user_id,
                    "device_ids": p.device_ids if p.device_ids else [],
                }
                for p in participants
            ],
        }

    new_conversation = messaging_repository.create_dm_conversation(
        db,
        conversation_id=uuid.uuid4(),
        created_at=datetime.utcnow(),
        pair_key=pair_key,
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = messaging_repository.get_dm_conversation_by_pair_key(
            db, pair_key=pair_key
        ) or messaging_repository.find_dm_conversation_between_users(db, user_ids=user_ids)
        if not existing:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create conversation")
        participants = messaging_repository.list_dm_participants(
            db, conversation_id=existing.id
        )
        return {
            "conversation_id": str(existing.id),
            "created_at": existing.created_at.isoformat(),
            "participants": [
                {
                    "user_id": p.user_id,
                    "device_ids": p.device_ids if p.device_ids else [],
                }
                for p in participants
            ],
        }

    device_map = messaging_repository.list_active_e2ee_device_ids_for_users(
        db, user_ids=user_ids
    )
    current_device_ids = device_map.get(current_user.account_id, [])
    peer_device_ids = device_map.get(peer_user_id, [])

    messaging_repository.create_dm_participant(
        db,
        conversation_id=new_conversation.id,
        user_id=current_user.account_id,
        device_ids=current_device_ids,
    )
    messaging_repository.create_dm_participant(
        db,
        conversation_id=new_conversation.id,
        user_id=peer_user_id,
        device_ids=peer_device_ids,
    )

    try:
        db.commit()
        db.refresh(new_conversation)
    except IntegrityError:
        db.rollback()
        existing = messaging_repository.get_dm_conversation_by_pair_key(
            db, pair_key=pair_key
        ) or messaging_repository.find_dm_conversation_between_users(db, user_ids=user_ids)
        if not existing:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create conversation")
        participants = messaging_repository.list_dm_participants(
            db, conversation_id=existing.id
        )
        return {
            "conversation_id": str(existing.id),
            "created_at": existing.created_at.isoformat(),
            "participants": [
                {
                    "user_id": p.user_id,
                    "device_ids": p.device_ids if p.device_ids else [],
                }
                for p in participants
            ],
        }

    logger.info(
        f"Created conversation {new_conversation.id} between users {current_user.account_id} and {peer_user_id}"
    )
    return {
        "conversation_id": str(new_conversation.id),
        "created_at": new_conversation.created_at.isoformat(),
        "participants": [
            {"user_id": current_user.account_id, "device_ids": current_device_ids},
            {"user_id": peer_user_id, "device_ids": peer_device_ids},
        ],
    }


def list_dm_conversations(db, *, current_user, limit: int, offset: int):
    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    conversations = messaging_repository.list_user_dm_conversations(
        db, user_id=current_user.account_id, limit=limit, offset=offset
    )
    if not conversations:
        return {"conversations": []}

    conv_ids = [conv.id for conv in conversations]
    participants = messaging_repository.list_dm_participants_for_conversations(
        db, conversation_ids=conv_ids, exclude_user_id=current_user.account_id
    )
    participant_map = {p.conversation_id: p for p in participants}

    peer_ids = [p.user_id for p in participants]
    peer_users = messaging_repository.list_users_by_account_ids(db, user_ids=peer_ids)
    peer_user_map = {user.account_id: user for user in peer_users}

    unread_map = messaging_repository.count_unread_dm_messages_for_conversations(
        db,
        conversation_ids=conv_ids,
        recipient_user_id=current_user.account_id,
        exclude_sender_id=current_user.account_id,
    )

    result = []
    for conv in conversations:
        participant = participant_map.get(conv.id)
        if not participant:
            continue
        peer_user = peer_user_map.get(participant.user_id)
        if not peer_user:
            continue
        result.append(
            {
                "conversation_id": str(conv.id),
                "peer_user_id": participant.user_id,
                "peer_username": peer_user.username if peer_user.username else None,
                "last_message_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
                "unread_count": unread_map.get(conv.id, 0),
            }
        )
    return {"conversations": result}


def get_dm_conversation_details(db, *, current_user, conversation_id: str):
    import uuid

    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid conversation ID")

    participant = messaging_repository.get_dm_participant(
        db, conversation_id=conv_uuid, user_id=current_user.account_id
    )
    if not participant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    conversation = messaging_repository.get_dm_conversation_by_id(
        db, conversation_id=conv_uuid
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    participants = messaging_repository.list_dm_participants(
        db, conversation_id=conv_uuid
    )
    return {
        "conversation_id": str(conversation.id),
        "created_at": conversation.created_at.isoformat(),
        "last_message_at": (
            conversation.last_message_at.isoformat() if conversation.last_message_at else None
        ),
        "sealed_sender_enabled": conversation.sealed_sender_enabled,
        "participants": [
            {"user_id": p.user_id, "device_ids": p.device_ids if p.device_ids else []}
            for p in participants
        ],
    }


def get_dm_metrics(db, *, current_user, active_sse_connections, now_ts, cache):
    import time
    from datetime import datetime, timedelta

    from fastapi import HTTPException

    from config import (
        E2EE_DM_ENABLED,
        E2EE_DM_METRICS_CACHE_SECONDS,
        E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS,
    )
    from routers.dependencies import verify_admin
    from utils.redis_pubsub import get_redis

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    verify_admin(db, current_user)

    if E2EE_DM_METRICS_CACHE_SECONDS > 0:
        payload = cache.get("payload")
        ts = cache.get("ts") or 0.0
        if payload and (now_ts - ts) < E2EE_DM_METRICS_CACHE_SECONDS:
            return payload

    now = datetime.utcnow()

    total_sse_connections = sum(len(sessions) for sessions in active_sse_connections.values())
    sse_connections_per_user = {
        str(user_id): len(sessions) for user_id, sessions in active_sse_connections.items()
    }

    redis_client = get_redis()
    redis_status = "available" if redis_client else "unavailable"
    redis_lag_ms = 0

    otpk_stats = messaging_repository.list_otpk_stats(db)
    devices_low_otpk = []
    devices_critical_otpk = []
    total_available_otpks = 0
    total_claimed_otpks = 0

    for device_id, available, claimed in otpk_stats:
        total_available_otpks += available or 0
        total_claimed_otpks += claimed or 0

        if available is not None:
            if available < 2:
                devices_critical_otpk.append(str(device_id))
            elif available < 5:
                devices_low_otpk.append(str(device_id))

    old_prekey_cutoff = now - timedelta(days=E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS)
    old_prekeys = messaging_repository.count_old_key_bundles(db, cutoff_dt=old_prekey_cutoff)

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    message_counts = messaging_repository.get_dm_message_counts(
        db, today_start=today_start, last_hour_start=now - timedelta(hours=1)
    )

    delivery_counts = messaging_repository.get_dm_delivery_counts(db)

    recent_deliveries = messaging_repository.get_avg_dm_delivery_ms_since(
        db, since_dt=now - timedelta(hours=1)
    )
    avg_delivery_ms = float(recent_deliveries) if recent_deliveries else 0

    device_counts = messaging_repository.get_device_counts(db)

    payload = {
        "status": "success",
        "timestamp": now.isoformat(),
        "metrics": {
            "sse_connections": {
                "total": total_sse_connections,
                "per_user": sse_connections_per_user,
                "max_per_user": 3,
            },
            "redis": {
                "status": redis_status,
                "lag_ms": redis_lag_ms,
                "available": redis_status == "available",
            },
            "otpk_pools": {
                "total_available": total_available_otpks,
                "total_claimed": total_claimed_otpks,
                "devices_low_watermark": len(devices_low_otpk),
                "devices_critical_watermark": len(devices_critical_otpk),
                "device_ids_low": devices_low_otpk[:10],
                "device_ids_critical": devices_critical_otpk[:10],
            },
            "signed_prekeys": {
                "old_prekeys_count": old_prekeys,
                "max_age_days": E2EE_DM_SIGNED_PREKEY_MAX_AGE_DAYS,
            },
            "messages": {
                "today": message_counts.today,
                "last_hour": message_counts.last_hour,
            },
            "delivery": {
                "undelivered": delivery_counts.undelivered,
                "unread": delivery_counts.unread,
                "avg_delivery_ms": round(avg_delivery_ms, 2),
            },
            "devices": {
                "total": device_counts.total,
                "active": device_counts.active,
                "revoked": device_counts.revoked,
            },
        },
    }

    cache["ts"] = now_ts
    cache["payload"] = payload
    return payload


def get_status_metrics(db, *, current_user):
    from datetime import datetime

    from fastapi import HTTPException

    from config import STATUS_ENABLED
    from routers.dependencies import verify_admin

    if not STATUS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Status feature is not enabled")

    verify_admin(db, current_user)

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.utcnow()
    post_counts = messaging_repository.get_status_post_counts(
        db, today_start=today_start, now_dt=now
    )
    posts_today = post_counts[0] if post_counts and post_counts[0] is not None else 0
    active_posts = post_counts[1] if post_counts and post_counts[1] is not None else 0
    expired_posts = post_counts[2] if post_counts and post_counts[2] is not None else 0

    views_today = messaging_repository.count_status_views_since(db, since_dt=today_start)
    avg_audience = messaging_repository.get_avg_status_audience_size(db)

    return {
        "status": "success",
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": {
            "posts": {
                "today": posts_today,
                "active": active_posts,
                "expired": expired_posts,
            },
            "views": {"today": views_today},
            "audience": {"average_size": round(float(avg_audience), 2)},
        },
    }


def get_group_metrics(db, *, current_user, now_ts, cache):
    import time
    from datetime import datetime, timedelta

    from fastapi import HTTPException

    from config import GROUP_METRICS_CACHE_SECONDS, GROUPS_ENABLED
    from routers.dependencies import verify_admin
    from utils.redis_pubsub import get_redis

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    verify_admin(db, current_user)

    if GROUP_METRICS_CACHE_SECONDS > 0:
        payload = cache.get("payload")
        ts = cache.get("ts") or 0.0
        if payload and (now_ts - ts) < GROUP_METRICS_CACHE_SECONDS:
            return payload

    now = datetime.utcnow()

    group_counts = messaging_repository.get_group_counts(db)
    total_groups = group_counts.total or 0
    active_groups = group_counts.active or 0

    avg_size = messaging_repository.get_avg_group_size(db)

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    message_counts = messaging_repository.get_group_message_counts(
        db, today_start=today_start, last_hour_start=now - timedelta(hours=1)
    )
    messages_today = message_counts.today or 0
    messages_last_hour = message_counts.last_hour or 0

    sender_key_count = messaging_repository.count_group_sender_keys(db)
    groups_with_epoch_changes = messaging_repository.count_groups_with_recent_epoch_changes(
        db, since_dt=now - timedelta(days=1)
    )

    redis_client = get_redis()
    redis_status = "available" if redis_client else "unavailable"

    payload = {
        "status": "success",
        "timestamp": now.isoformat(),
        "metrics": {
            "groups": {
                "total": total_groups,
                "active": active_groups,
                "closed": total_groups - active_groups,
            },
            "participants": {"average_per_group": round(float(avg_size), 2)},
            "messages": {"today": messages_today, "last_hour": messages_last_hour},
            "sender_keys": {"total_distributions": sender_key_count},
            "rekey": {"groups_with_epoch_changes_24h": groups_with_epoch_changes},
            "redis": {"status": redis_status},
        },
    }
    cache["ts"] = now_ts
    cache["payload"] = payload
    return payload


# --- DM SSE ---


def _sse_format(data: dict, event=None, id_=None) -> bytes:
    chunks = []
    if event:
        chunks.append(f"event: {event}\n")
    if id_:
        chunks.append(f"id: {id_}\n")
    import json

    payload = json.dumps(data, separators=(",", ":"))
    chunks.append(f"data: {payload}\n\n")
    return "".join(chunks).encode("utf-8")


def _sse_retry(ms: int = 5000) -> bytes:
    return f"retry: {ms}\n\n".encode("utf-8")


def _hash_user_id(user_id: int) -> str:
    import hashlib

    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]


def _get_user_from_token(token: str, db):
    from fastapi import HTTPException, status

    from auth import validate_descope_jwt
    from core.users import get_user_by_descope_id, get_user_by_email

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token"
        )

    try:
        user_info = validate_descope_jwt(token)
        user = get_user_by_descope_id(db, descope_user_id=user_info["userId"])
        if not user:
            email = user_info.get("loginIds", [None])[0] or user_info.get("email")
            if email:
                user = get_user_by_email(db, email=email)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Authentication failed: {str(exc)}")
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(exc)}")


def _get_token_expiry(token: str):
    try:
        from auth import decode_jwt_payload

        payload = decode_jwt_payload(token)
        exp = payload.get("exp")
        return float(exp) if exp else None
    except Exception as exc:
        logger.debug(f"Token expiry decode failed: {exc}")
        return None


def _load_user_context(token: str):
    from db import get_db_context
    from config import GROUPS_ENABLED

    with get_db_context() as db:
        user = _get_user_from_token(token, db)
        user_id = user.account_id
        group_ids: list[str] = []
        if GROUPS_ENABLED:
            try:
                from models import GroupParticipant

                group_ids = [
                    str(participant.group_id)
                    for participant in messaging_repository.query(db, GroupParticipant)
                    .filter(
                        GroupParticipant.user_id == user_id,
                        GroupParticipant.is_banned == False,
                    )
                    .all()
                ]
            except Exception as exc:
                logger.warning(
                    f"Group models unavailable, skipping group subscriptions: {exc}"
                )
        return user_id, group_ids


def _update_presence(user_id: int, last_seen_at, device_online, create_if_missing: bool):
    from config import PRESENCE_ENABLED

    if not PRESENCE_ENABLED:
        return
    from db import get_db_context
    from models import UserPresence

    with get_db_context() as db:
        presence = (
            messaging_repository.query(db, UserPresence).filter(UserPresence.user_id == user_id).first()
        )
        if presence:
            if last_seen_at is not None:
                presence.last_seen_at = last_seen_at
            if device_online is not None:
                presence.device_online = device_online
        elif create_if_missing:
            presence = UserPresence(
                user_id=user_id,
                last_seen_at=last_seen_at,
                device_online=device_online if device_online is not None else False,
            )
            db.add(presence)
        else:
            return
        db.commit()


async def dm_sse_stream(request, token=None):
    import asyncio
    import time
    from typing import AsyncGenerator

    from fastapi import HTTPException, Query, status
    from fastapi.concurrency import run_in_threadpool
    from fastapi.responses import StreamingResponse

    from datetime import datetime
    from config import (
        E2EE_DM_ENABLED,
        E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER,
        E2EE_DM_SSE_ALLOW_QUERY_TOKEN,
        GROUPS_ENABLED,
        PRESENCE_ENABLED,
        PRESENCE_UPDATE_INTERVAL_SECONDS,
        REDIS_RETRY_INTERVAL_SECONDS,
        SSE_HEARTBEAT_SECONDS,
        SSE_MAX_MISSED_HEARTBEATS,
    )
    from utils.redis_pubsub import get_redis, subscribe_dm_user, subscribe_group

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")

    token_param = token
    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        if token_param and E2EE_DM_SSE_ALLOW_QUERY_TOKEN:
            token = token_param
        elif token_param:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Use Authorization header for SSE",
            )

    user_id, group_ids = await run_in_threadpool(_load_user_context, token)
    user_id_hash = _hash_user_id(user_id)

    connection_id = id(request)
    active_connections = ACTIVE_DM_SSE_CONNECTIONS[user_id]
    if len(active_connections) >= E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER:
        logger.warning(f"Connection limit exceeded for user {user_id_hash}")
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER} concurrent streams allowed per user",
        )

    ACTIVE_DM_SSE_CONNECTIONS[user_id].add(connection_id)
    logger.info(f"DM SSE connection opened: user={user_id_hash}")

    async def event_stream() -> AsyncGenerator[bytes, None]:
        yield _sse_retry(5000)
        current_token = token
        token_expiry = _get_token_expiry(current_token) if current_token else None

        redis_available = get_redis() is not None
        last_redis_check = time.time()

        redis_iter = None
        if redis_available:
            try:
                redis_msgs = subscribe_dm_user(user_id)
                redis_iter = redis_msgs.__aiter__()
            except Exception as exc:
                logger.warning(
                    f"Failed to subscribe to DM channel for user {user_id_hash}: {exc}"
                )
                redis_available = False
                redis_iter = None
        else:
            logger.info(
                f"Redis unavailable, skipping real-time subscriptions for user {user_id_hash}"
            )

        group_subscriptions = {}
        if GROUPS_ENABLED and redis_available and group_ids:
            for group_id in group_ids:
                try:
                    group_msgs = subscribe_group(group_id)
                    group_subscriptions[group_id] = group_msgs.__aiter__()
                except Exception as exc:
                    logger.warning(f"Failed to subscribe to group {group_id}: {exc}")

        missed_heartbeats = 0
        last_presence_update = time.time()

        while True:
            if token_expiry and time.time() > token_expiry:
                logger.info(f"Token expired for user {user_id_hash}")
                break

            if await request.is_disconnected():
                logger.info(f"Client disconnected: user={user_id_hash}")
                break

            now = time.time()
            if redis_available and now - last_redis_check > REDIS_RETRY_INTERVAL_SECONDS:
                last_redis_check = now
                if get_redis() is None:
                    redis_available = False
                    logger.warning(f"Redis became unavailable for user {user_id_hash}")

            if PRESENCE_ENABLED and now - last_presence_update > PRESENCE_UPDATE_INTERVAL_SECONDS:
                last_presence_update = now
                await run_in_threadpool(
                    _update_presence, user_id, datetime.utcnow(), True, True
                )

            try:
                tasks = []
                if redis_iter:
                    tasks.append(redis_iter.__anext__())
                for group_iter in group_subscriptions.values():
                    tasks.append(group_iter.__anext__())

                if tasks:
                    done, pending = await asyncio.wait(
                        [asyncio.create_task(t) for t in tasks],
                        timeout=SSE_HEARTBEAT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    done, pending = await asyncio.wait(
                        [], timeout=SSE_HEARTBEAT_SECONDS
                    )

                if not done:
                    missed_heartbeats += 1
                    if missed_heartbeats >= SSE_MAX_MISSED_HEARTBEATS:
                        logger.warning(
                            f"Missed heartbeats for user {user_id_hash}, closing connection"
                        )
                        break
                    yield _sse_format({"type": "heartbeat"})
                    continue

                missed_heartbeats = 0

                for task in done:
                    try:
                        msg = task.result()
                        if msg:
                            yield _sse_format(msg)
                    except StopAsyncIteration:
                        pass
            except Exception as exc:
                logger.error(f"Error in SSE stream: {exc}")
                await asyncio.sleep(1)

        if PRESENCE_ENABLED:
            await run_in_threadpool(
                _update_presence, user_id, datetime.utcnow(), False, False
            )

    try:
        return StreamingResponse(event_stream(), media_type="text/event-stream")
    finally:
        ACTIVE_DM_SSE_CONNECTIONS[user_id].discard(connection_id)
        if not ACTIVE_DM_SSE_CONNECTIONS[user_id]:
            ACTIVE_DM_SSE_CONNECTIONS.pop(user_id, None)
        logger.info(f"DM SSE connection closed: user={user_id_hash}")


# --- E2EE keys ---


def _has_dm_relationship(db, user_a: int, user_b: int) -> bool:
    import models as models_module
    from sqlalchemy import func

    dm_participant = getattr(models_module, "DMParticipant", None)
    dm_conversation = getattr(models_module, "DMConversation", None)
    if not dm_participant:
        logger.warning("DMParticipant model not available; skipping relationship check")
        return True

    query = (
        messaging_repository.query(db, dm_participant.conversation_id)
        .filter(dm_participant.user_id.in_([user_a, user_b]))
        .group_by(dm_participant.conversation_id)
        .having(func.count() == 2)
    )

    if dm_conversation:
        query = query.join(
            dm_conversation, dm_conversation.id == dm_participant.conversation_id
        )

    return query.first() is not None


def upload_e2ee_key_bundle(db, *, current_user, request):
    import hashlib
    import uuid
    from datetime import datetime

    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from config import (
        E2EE_DM_ENABLED,
        E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD,
        E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD,
        E2EE_DM_PREKEY_POOL_SIZE,
    )
    from models import E2EEKeyBundle

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    if not request.one_time_prekeys or len(request.one_time_prekeys) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one one-time prekey is required",
        )
    if len(request.one_time_prekeys) > E2EE_DM_PREKEY_POOL_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many one-time prekeys (max {E2EE_DM_PREKEY_POOL_SIZE})",
        )

    try:
        if request.device_id:
            try:
                device_uuid = uuid.UUID(request.device_id)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid device_id format: {request.device_id}",
                )
        else:
            device_uuid = uuid.uuid4()

        device = messaging_repository.get_e2ee_device(db, device_id=device_uuid)

        if not device:
            device = messaging_repository.create_e2ee_device(
                db,
                device_id=device_uuid,
                user_id=current_user.account_id,
                device_name=request.device_name,
            )
            db.flush()
        else:
            if device.user_id != current_user.account_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Device belongs to another user. Cannot upload keys for this device.",
                )
            device.device_name = request.device_name
            device.last_seen_at = datetime.utcnow()
            if device.status == "revoked":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Device has been revoked")

        key_bundle = messaging_repository.get_e2ee_key_bundle(db, device_id=device_uuid)

        if key_bundle:
            old_identity_key = key_bundle.identity_key_pub
            identity_changed = old_identity_key != request.identity_key_pub
            if identity_changed:
                logger.warning(
                    f"Identity key change detected: device={device_uuid}, "
                    f"user={current_user.account_id}, "
                    f"old_fingerprint={hashlib.sha256(old_identity_key.encode()).hexdigest()[:16]}, "
                    f"new_fingerprint={hashlib.sha256(request.identity_key_pub.encode()).hexdigest()[:16]}"
                )

                identity_change_count = (
                    messaging_repository.count_identity_change_revocations(
                        db, device_id=device_uuid
                    )
                    + 1
                )

                if (
                    E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD > 0
                    and identity_change_count >= E2EE_DM_IDENTITY_CHANGE_BLOCK_THRESHOLD
                ):
                    device.status = "revoked"
                    messaging_repository.create_device_revocation(
                        db,
                        user_id=current_user.account_id,
                        device_id=device_uuid,
                        reason="identity_change_block",
                    )
                    db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="IDENTITY_CHANGE_BLOCKED",
                        headers={"X-Error-Code": "IDENTITY_CHANGE_BLOCKED"},
                    )

                if (
                    E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD > 0
                    and identity_change_count >= E2EE_DM_IDENTITY_CHANGE_ALERT_THRESHOLD
                ):
                    logger.warning(
                        "Identity key change alert threshold reached",
                        extra={
                            "user_id": current_user.account_id,
                            "device_id": str(device_uuid),
                            "count": identity_change_count,
                        },
                    )
                messaging_repository.create_device_revocation(
                    db,
                    user_id=current_user.account_id,
                    device_id=device_uuid,
                    reason="identity_change",
                )

            key_bundle.identity_key_pub = request.identity_key_pub
            key_bundle.signed_prekey_pub = request.signed_prekey_pub
            key_bundle.signed_prekey_sig = request.signed_prekey_sig
            key_bundle.bundle_version += 1
            key_bundle.updated_at = datetime.utcnow()
        else:
            key_bundle = E2EEKeyBundle(
                device_id=device_uuid,
                identity_key_pub=request.identity_key_pub,
                signed_prekey_pub=request.signed_prekey_pub,
                signed_prekey_sig=request.signed_prekey_sig,
                bundle_version=1,
                prekeys_remaining=0,
            )
            db.add(key_bundle)

        messaging_repository.delete_unclaimed_prekeys(db, device_id=device_uuid)
        prekeys = [p.prekey_pub for p in request.one_time_prekeys]
        prekey_objects = messaging_repository.bulk_insert_prekeys(
            db, device_id=device_uuid, prekeys=prekeys
        )
        prekeys_stored = len(prekey_objects)
        key_bundle.prekeys_remaining = prekeys_stored

        db.commit()
        db.refresh(key_bundle)

        logger.info(
            f"Key bundle uploaded for device {device_uuid} (user {current_user.account_id})"
        )

        return {
            "device_id": str(device_uuid),
            "bundle_version": key_bundle.bundle_version,
            "prekeys_stored": prekeys_stored,
        }

    except ValueError as exc:
        db.rollback()
        logger.error(f"Invalid UUID in upload_key_bundle: {str(exc)}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid UUID: {str(exc)}")
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.error(f"Error uploading key bundle: {str(exc)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload key bundle: {str(exc)}",
        )


def get_e2ee_key_bundle(db, *, current_user, user_id: int, bundle_version):
    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    # block check
    from models import Block
    from sqlalchemy import and_, or_

    is_blocked = (
        messaging_repository.query(db, Block)
        .filter(
            or_(
                and_(Block.blocker_id == user_id, Block.blocked_id == current_user.account_id),
                and_(Block.blocker_id == current_user.account_id, Block.blocked_id == user_id),
            )
        )
        .first()
    )
    if is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="BLOCKED", headers={"X-Error-Code": "BLOCKED"}
        )

    if user_id != current_user.account_id and not _has_dm_relationship(
        db, user_id, current_user.account_id
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="RELATIONSHIP_REQUIRED")

    rows = messaging_repository.list_active_devices_with_bundles(db, user_id=user_id)
    result_devices = []
    for row in rows:
        if bundle_version is not None and row.bundle_version > bundle_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="BUNDLE_STALE",
                headers={
                    "X-Error-Code": "BUNDLE_STALE",
                    "X-Bundle-Version": str(row.bundle_version),
                },
            )
        result_devices.append(
            {
                "device_id": str(row.device_id),
                "device_name": row.device_name,
                "identity_key_pub": row.identity_key_pub,
                "signed_prekey_pub": row.signed_prekey_pub,
                "signed_prekey_sig": row.signed_prekey_sig,
                "bundle_version": row.bundle_version,
                "prekeys_available": int(row.available or 0),
            }
        )

    return {"devices": result_devices}


def list_e2ee_devices(db, *, current_user):
    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    devices = messaging_repository.list_user_devices(db, user_id=current_user.account_id)
    return {
        "devices": [
            {
                "device_id": str(device.device_id),
                "device_name": device.device_name,
                "created_at": device.created_at.isoformat(),
                "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
                "status": device.status,
            }
            for device in devices
        ]
    }


def revoke_e2ee_device(db, *, current_user, request):
    import uuid
    from fastapi import HTTPException

    from config import E2EE_DM_ENABLED

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    try:
        device_uuid = uuid.UUID(request.device_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid device UUID")

    device = messaging_repository.get_user_e2ee_device(
        db, user_id=current_user.account_id, device_id=device_uuid
    )
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    if device.status == "revoked":
        return {"success": True, "message": "Device already revoked"}

    device.status = "revoked"
    messaging_repository.create_device_revocation(
        db,
        user_id=current_user.account_id,
        device_id=device_uuid,
        reason=request.reason,
    )
    db.commit()
    logger.info(f"Device {device_uuid} revoked by user {current_user.account_id}")
    return {"success": True}


def claim_e2ee_prekey(db, *, current_user, request):
    import uuid

    from fastapi import HTTPException
    from sqlalchemy import and_, or_

    from config import E2EE_DM_ENABLED
    from models import Block

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    try:
        device_uuid = uuid.UUID(request.device_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid device UUID")

    device = messaging_repository.get_e2ee_device(db, device_id=device_uuid)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    if device.status == "revoked":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="DEVICE_REVOKED",
            headers={"X-Error-Code": "DEVICE_REVOKED"},
        )

    if device.user_id != current_user.account_id:
        is_blocked = (
            messaging_repository.query(db, Block)
            .filter(
                or_(
                    and_(
                        Block.blocker_id == device.user_id,
                        Block.blocked_id == current_user.account_id,
                    ),
                    and_(
                        Block.blocker_id == current_user.account_id,
                        Block.blocked_id == device.user_id,
                    ),
                )
            )
            .first()
        )
        if is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="BLOCKED",
                headers={"X-Error-Code": "BLOCKED"},
            )

        if not _has_dm_relationship(db, device.user_id, current_user.account_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="RELATIONSHIP_REQUIRED")

    claimed_id = messaging_repository.claim_prekey(
        db, device_id=device_uuid, prekey_id=request.prekey_id
    )
    if not claimed_id:
        available_count = messaging_repository.count_unclaimed_prekeys(
            db, device_id=device_uuid
        )
        if available_count == 0:
            key_bundle = messaging_repository.get_e2ee_key_bundle(
                db, device_id=device_uuid
            )
            bundle_version = key_bundle.bundle_version if key_bundle else 1
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="PREKEYS_EXHAUSTED",
                headers={
                    "X-Error-Code": "PREKEYS_EXHAUSTED",
                    "X-Bundle-Version": str(bundle_version),
                },
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Prekey not found or already claimed",
        )

    remaining = messaging_repository.count_unclaimed_prekeys(
        db, device_id=device_uuid
    )
    key_bundle = messaging_repository.get_e2ee_key_bundle(db, device_id=device_uuid)
    if key_bundle:
        key_bundle.prekeys_remaining = remaining
    db.commit()
    return {"claimed": True, "prekey_id": request.prekey_id}


# --- Group invites ---


def _generate_invite_code() -> str:
    import secrets

    return secrets.token_urlsafe(8)[:12].upper()


def create_group_invite(db, *, current_user, group_id: str, request):
    import uuid
    from datetime import datetime, timedelta

    from fastapi import HTTPException
    from sqlalchemy.exc import IntegrityError

    from config import GROUP_INVITE_EXPIRY_HOURS, GROUPS_ENABLED
    from models import Group, GroupInvite

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.is_closed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Group is closed")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    expires_at = request.expires_at
    if not expires_at:
        expires_at = datetime.utcnow() + timedelta(hours=GROUP_INVITE_EXPIRY_HOURS)
    if expires_at and expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="EXPIRY_IN_PAST")

    if request.type == "direct" and not request.target_user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="TARGET_USER_REQUIRED")

    for _ in range(5):
        code = _generate_invite_code()
        invite = GroupInvite(
            id=uuid.uuid4(),
            group_id=group_uuid,
            created_by=current_user.account_id,
            type=request.type,
            code=code,
            expires_at=expires_at,
            max_uses=request.max_uses,
            uses=0,
            target_user_id=request.target_user_id,
        )
        db.add(invite)
        try:
            db.commit()
            db.refresh(invite)
            return {
                "id": str(invite.id),
                "code": invite.code,
                "type": invite.type,
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
                "max_uses": invite.max_uses,
                "uses": invite.uses,
                "target_user_id": invite.target_user_id,
            }
        except IntegrityError:
            db.rollback()
            continue
        except Exception as exc:
            db.rollback()
            logger.error(f"Error creating invite: {exc}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create invite")

    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate unique invite code")


def list_group_invites(db, *, current_user, group_id: str):
    import uuid
    from datetime import datetime

    from fastapi import HTTPException
    from sqlalchemy import or_

    from config import GROUPS_ENABLED
    from models import GroupInvite

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    now = datetime.utcnow()
    active_invites = (
        messaging_repository.query(db, GroupInvite)
        .filter(
            GroupInvite.group_id == group_uuid,
            GroupInvite.expires_at > now,
            or_(GroupInvite.max_uses.is_(None), GroupInvite.uses < GroupInvite.max_uses),
        )
        .all()
    )

    payload = []
    for invite in active_invites:
        payload.append(
            {
                "id": str(invite.id),
                "code": invite.code,
                "type": invite.type,
                "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
                "max_uses": invite.max_uses,
                "uses": invite.uses,
                "created_at": invite.created_at.isoformat() if invite.created_at else None,
                "target_user_id": invite.target_user_id,
            }
        )

    return {"invites": payload}


def revoke_group_invite(db, *, current_user, group_id: str, invite_id: str):
    import uuid

    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import GroupInvite

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
        invite_uuid = uuid.UUID(invite_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    invite = (
        messaging_repository.query(db, GroupInvite)
        .filter(GroupInvite.id == invite_uuid, GroupInvite.group_id == group_uuid)
        .first()
    )
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")

    db.delete(invite)
    try:
        db.commit()
        return {"message": "Invite revoked"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error revoking invite: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to revoke invite")


def join_group_by_invite(db, *, current_user, request):
    from datetime import datetime

    from fastapi import HTTPException

    from config import GROUP_MAX_PARTICIPANTS, GROUPS_ENABLED
    from models import Group, GroupBan, GroupInvite, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    invite = (
        messaging_repository.query(db, GroupInvite)
        .filter(GroupInvite.code == request.code)
        .with_for_update()
        .first()
    )
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid invite code")

    if invite.expires_at and invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="GONE")

    if invite.max_uses and invite.uses >= invite.max_uses:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MAX_USES")

    if invite.type == "direct" and invite.target_user_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="NOT_INVITED")

    group = messaging_repository.query(db, Group).filter(Group.id == invite.group_id).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.is_closed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Group is closed")

    ban = (
        messaging_repository.query(db, GroupBan)
        .filter(GroupBan.group_id == invite.group_id, GroupBan.user_id == current_user.account_id)
        .first()
    )
    if ban:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="BANNED")

    member_count = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == invite.group_id, GroupParticipant.is_banned.is_(False))
        .count()
    )
    if member_count >= GROUP_MAX_PARTICIPANTS:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="GROUP_FULL")

    existing = (
        messaging_repository.query(db, GroupParticipant)
        .filter(
            GroupParticipant.group_id == invite.group_id,
            GroupParticipant.user_id == current_user.account_id,
        )
        .first()
    )
    if existing and not existing.is_banned:
        return {"message": "Already a member", "group_id": str(invite.group_id)}

    now = datetime.utcnow()
    if existing and existing.is_banned:
        existing.is_banned = False
        existing.joined_at = now
        existing.role = "member"
    else:
        db.add(
            GroupParticipant(
                group_id=invite.group_id,
                user_id=current_user.account_id,
                role="member",
                joined_at=now,
            )
        )

    invite.uses += 1
    increment_group_epoch(db, group)

    try:
        db.commit()
        return {
            "message": "Joined group",
            "group_id": str(invite.group_id),
            "new_epoch": group.group_epoch,
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"Error joining group: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to join group")


# --- Group members helpers (used by multiple endpoints) ---


def check_group_role(db, group_id, user_id: int, required_roles):
    from fastapi import HTTPException

    from models import GroupParticipant

    participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_id, GroupParticipant.user_id == user_id)
        .first()
    )
    if not participant or participant.is_banned:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="NOT_MEMBER")
    if participant.role not in required_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")
    return participant


def increment_group_epoch(db, group):
    import asyncio
    from datetime import datetime

    from utils.redis_pubsub import publish_group_message

    group.group_epoch += 1
    group.updated_at = datetime.utcnow()

    event = {"type": "epoch_changed", "group_id": str(group.id), "new_epoch": group.group_epoch}
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(publish_group_message(str(group.id), event))
    else:
        loop.create_task(publish_group_message(str(group.id), event))


# --- Groups ---


def create_group(db, *, current_user, request):
    import uuid
    from fastapi import HTTPException
    from datetime import datetime

    from config import GROUP_MAX_PARTICIPANTS, GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    new_group = Group(
        id=uuid.uuid4(),
        title=request.title,
        about=request.about,
        photo_url=request.photo_url,
        created_by=current_user.account_id,
        max_participants=GROUP_MAX_PARTICIPANTS,
        group_epoch=0,
        is_closed=False,
    )
    db.add(new_group)

    owner_participant = GroupParticipant(
        group_id=new_group.id,
        user_id=current_user.account_id,
        role="owner",
        joined_at=datetime.utcnow(),
    )
    db.add(owner_participant)

    try:
        db.commit()
        db.refresh(new_group)
        return {
            "id": str(new_group.id),
            "title": new_group.title,
            "about": new_group.about,
            "photo_url": new_group.photo_url,
            "created_by": new_group.created_by,
            "created_at": new_group.created_at.isoformat(),
            "max_participants": new_group.max_participants,
            "group_epoch": new_group.group_epoch,
            "is_closed": new_group.is_closed,
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"Error creating group: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create group")


def list_groups(db, *, current_user, limit: int, offset: int):
    from fastapi import HTTPException
    from sqlalchemy import desc, func

    from config import GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    participant_counts_subq = (
        messaging_repository.query(db, 
            GroupParticipant.group_id.label("group_id"),
            func.count(GroupParticipant.user_id).label("participant_count"),
        )
        .filter(GroupParticipant.is_banned.is_(False))
        .group_by(GroupParticipant.group_id)
        .subquery()
    )

    member_subq = (
        messaging_repository.query(db, 
            GroupParticipant.group_id.label("group_id"),
            GroupParticipant.role.label("role"),
            GroupParticipant.is_banned.label("is_banned"),
        )
        .filter(GroupParticipant.user_id == current_user.account_id)
        .subquery()
    )

    groups = (
        messaging_repository.query(db, 
            Group,
            member_subq.c.role.label("my_role"),
            participant_counts_subq.c.participant_count.label("participant_count"),
        )
        .join(member_subq, Group.id == member_subq.c.group_id)
        .outerjoin(
            participant_counts_subq, Group.id == participant_counts_subq.c.group_id
        )
        .filter(member_subq.c.is_banned.is_(False))
        .order_by(desc(Group.updated_at))
        .offset(offset)
        .limit(limit)
        .all()
    )

    result = []
    for group, my_role, participant_count in groups:
        result.append(
            {
                "id": str(group.id),
                "title": group.title,
                "about": group.about,
                "photo_url": group.photo_url,
                "created_at": group.created_at.isoformat(),
                "updated_at": group.updated_at.isoformat() if group.updated_at else None,
                "participant_count": participant_count or 0,
                "max_participants": group.max_participants,
                "group_epoch": group.group_epoch,
                "is_closed": group.is_closed,
                "my_role": my_role,
            }
        )
    return {"groups": result, "total": len(result)}


def get_group(db, *, current_user, group_id: str):
    import uuid
    from fastapi import HTTPException
    from sqlalchemy import func

    from config import GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    participant_counts_subq = (
        messaging_repository.query(db, 
            GroupParticipant.group_id.label("group_id"),
            func.count(GroupParticipant.user_id).label("participant_count"),
        )
        .filter(GroupParticipant.is_banned.is_(False))
        .group_by(GroupParticipant.group_id)
        .subquery()
    )

    member_subq = (
        messaging_repository.query(db, 
            GroupParticipant.group_id.label("group_id"),
            GroupParticipant.role.label("role"),
            GroupParticipant.is_banned.label("is_banned"),
        )
        .filter(
            GroupParticipant.user_id == current_user.account_id,
            GroupParticipant.group_id == group_uuid,
        )
        .subquery()
    )

    row = (
        messaging_repository.query(db, 
            Group,
            member_subq.c.role.label("my_role"),
            member_subq.c.is_banned.label("is_banned"),
            participant_counts_subq.c.participant_count.label("participant_count"),
        )
        .outerjoin(member_subq, Group.id == member_subq.c.group_id)
        .outerjoin(participant_counts_subq, Group.id == participant_counts_subq.c.group_id)
        .filter(Group.id == group_uuid)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    group, my_role, is_banned, participant_count = row
    if not my_role or is_banned:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this group")

    return {
        "id": str(group.id),
        "title": group.title,
        "about": group.about,
        "photo_url": group.photo_url,
        "created_by": group.created_by,
        "created_at": group.created_at.isoformat(),
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        "participant_count": participant_count or 0,
        "max_participants": group.max_participants,
        "group_epoch": group.group_epoch,
        "is_closed": group.is_closed,
        "my_role": my_role,
    }


def update_group(db, *, current_user, group_id: str, request):
    import uuid
    from datetime import datetime

    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.is_closed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group is closed")

    participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == current_user.account_id)
        .first()
    )
    if not participant or participant.is_banned:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this group")
    if participant.role not in ["owner", "admin"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions. Owner or admin required.")

    if request.title is not None:
        group.title = request.title
    if request.about is not None:
        group.about = request.about
    if request.photo_url is not None:
        group.photo_url = request.photo_url
    group.updated_at = datetime.utcnow()

    try:
        db.commit()
        db.refresh(group)
        return {
            "id": str(group.id),
            "title": group.title,
            "about": group.about,
            "photo_url": group.photo_url,
            "updated_at": group.updated_at.isoformat(),
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"Error updating group: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update group")


def delete_group(db, *, current_user, group_id: str):
    import uuid
    from datetime import datetime

    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.is_closed:
        return {"message": "Group already closed"}

    participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == current_user.account_id)
        .first()
    )
    if not participant or participant.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner can close the group")

    group.is_closed = True
    group.updated_at = datetime.utcnow()

    try:
        db.commit()
        return {"message": "Group closed successfully"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error closing group: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to close group")


def _get_group_capacity(group):
    from config import GROUP_MAX_PARTICIPANTS

    return getattr(group, "max_participants", GROUP_MAX_PARTICIPANTS)


def _get_group_member_count(db, group, group_id):
    from models import GroupParticipant

    cached_count = getattr(group, "participant_count", None)
    if cached_count is not None:
        return cached_count
    return (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_id, GroupParticipant.is_banned == False)
        .count()
    )


def _set_group_member_count(group, count: int):
    if hasattr(group, "participant_count"):
        group.participant_count = max(count, 0)


def _adjust_group_member_count(group, delta: int):
    if hasattr(group, "participant_count") and group.participant_count is not None:
        group.participant_count = max(group.participant_count + delta, 0)


def list_group_members(db, *, current_user, group_id: str):
    import uuid
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin", "member"])

    participants = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.is_banned == False)
        .all()
    )
    return {
        "members": [
            {
                "user_id": p.user_id,
                "role": p.role,
                "joined_at": p.joined_at.isoformat() if p.joined_at else None,
            }
            for p in participants
        ]
    }


def add_group_members(db, *, current_user, group_id: str, request):
    import uuid
    from datetime import datetime

    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from core.users import get_users_by_ids
    from models import Group, GroupBan, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.is_closed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Group is closed")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    current_count = _get_group_member_count(db, group, group_uuid)
    max_participants = _get_group_capacity(group)
    user_ids = list(dict.fromkeys(request.user_ids))

    added_users = []
    users = get_users_by_ids(db, account_ids=list(user_ids))
    user_map = {user.account_id: user for user in users}

    existing_participants = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id.in_(user_ids))
        .all()
    )
    existing_map = {participant.user_id: participant for participant in existing_participants}

    bans = (
        messaging_repository.query(db, GroupBan)
        .filter(GroupBan.group_id == group_uuid, GroupBan.user_id.in_(user_ids))
        .all()
    )
    banned_ids = {ban.user_id for ban in bans}

    pending_additions = 0
    for user_id in user_ids:
        if user_id not in user_map or user_id in banned_ids:
            continue
        existing = existing_map.get(user_id)
        if existing and not existing.is_banned:
            continue
        pending_additions += 1

    if current_count + pending_additions > max_participants:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="GROUP_FULL")

    new_participants = []
    active_delta = 0
    now = datetime.utcnow()
    for user_id in user_ids:
        if user_id not in user_map:
            continue
        if user_id in banned_ids:
            continue

        existing = existing_map.get(user_id)
        if existing:
            if existing.is_banned:
                existing.is_banned = False
                existing.role = "member"
                existing.joined_at = now
                active_delta += 1
                added_users.append(user_id)
            else:
                continue
        else:
            new_participants.append(
                GroupParticipant(group_id=group_uuid, user_id=user_id, role="member", joined_at=now)
            )
            added_users.append(user_id)
            active_delta += 1

    if new_participants:
        db.add_all(new_participants)

    if added_users:
        increment_group_epoch(db, group)
        _set_group_member_count(group, current_count + active_delta)
        try:
            db.commit()
            return {"added_user_ids": added_users, "new_epoch": group.group_epoch}
        except Exception as exc:
            db.rollback()
            logger.error(f"Error adding members: {exc}")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to add members")

    return {"added_user_ids": [], "message": "No new members added"}


def remove_group_member(db, *, current_user, group_id: str, user_id: int):
    import uuid

    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    target_participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == user_id)
        .first()
    )
    if not target_participant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not a member")
    if target_participant.role == "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot remove owner")

    db.delete(target_participant)
    if not target_participant.is_banned:
        _adjust_group_member_count(group, -1)
    increment_group_epoch(db, group)

    try:
        db.commit()
        return {"message": "Member removed", "new_epoch": group.group_epoch}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error removing member: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to remove member")


def leave_group(db, *, current_user, group_id: str):
    import uuid

    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == current_user.account_id)
        .first()
    )
    if not participant:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    if participant.role == "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner cannot leave. Transfer ownership or close group.",
        )

    db.delete(participant)
    if not participant.is_banned:
        _adjust_group_member_count(group, -1)
    increment_group_epoch(db, group)

    try:
        db.commit()
        return {"message": "Left group", "new_epoch": group.group_epoch}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error leaving group: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to leave group")


def promote_group_member(db, *, current_user, group_id: str, user_id: int):
    import uuid
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import Group, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    target_participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == user_id)
        .first()
    )
    if not target_participant or target_participant.is_banned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not a member")
    if target_participant.role == "admin":
        return {"message": "User is already an admin"}

    target_participant.role = "admin"
    try:
        db.commit()
        return {"message": "Member promoted to admin"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error promoting member: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to promote member")


def demote_group_admin(db, *, current_user, group_id: str, user_id: int):
    import uuid
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    check_group_role(db, group_uuid, current_user.account_id, ["owner"])

    target_participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == user_id)
        .first()
    )
    if not target_participant or target_participant.is_banned:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User is not a member")
    if target_participant.role != "admin":
        return {"message": "User is not an admin"}

    target_participant.role = "member"
    try:
        db.commit()
        return {"message": "Admin demoted to member"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error demoting admin: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to demote admin")


def ban_group_user(db, *, current_user, group_id: str, request):
    import uuid
    from datetime import datetime

    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import Group, GroupBan, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    target_participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == request.user_id)
        .first()
    )
    if target_participant and target_participant.role == "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot ban owner")

    if target_participant:
        was_active = not target_participant.is_banned
        target_participant.is_banned = True
        if was_active:
            _adjust_group_member_count(group, -1)
    else:
        target_participant = GroupParticipant(
            group_id=group_uuid, user_id=request.user_id, role="member", is_banned=True
        )
        db.add(target_participant)

    ban = (
        messaging_repository.query(db, GroupBan)
        .filter(GroupBan.group_id == group_uuid, GroupBan.user_id == request.user_id)
        .first()
    )
    if not ban:
        ban = GroupBan(
            group_id=group_uuid,
            user_id=request.user_id,
            banned_by=current_user.account_id,
            reason=request.reason,
            banned_at=datetime.utcnow(),
        )
        db.add(ban)

    increment_group_epoch(db, group)

    try:
        db.commit()
        return {"message": "User banned", "new_epoch": group.group_epoch}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error banning user: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to ban user")


def unban_group_user(db, *, current_user, group_id: str, user_id: int):
    import uuid
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import Group, GroupBan, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).with_for_update().first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin"])

    ban = (
        messaging_repository.query(db, GroupBan)
        .filter(GroupBan.group_id == group_uuid, GroupBan.user_id == user_id)
        .first()
    )
    if ban:
        db.delete(ban)

    participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == user_id)
        .first()
    )
    if participant:
        was_banned = participant.is_banned
        participant.is_banned = False
        if was_banned:
            _adjust_group_member_count(group, 1)

    try:
        db.commit()
        return {"message": "User unbanned"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error unbanning user: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to unban user")


def mute_group(db, *, current_user, group_id: str, request):
    import uuid
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    participant = (
        messaging_repository.query(db, GroupParticipant)
        .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.user_id == current_user.account_id)
        .first()
    )
    if not participant:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")

    participant.mute_until = request.mute_until
    try:
        db.commit()
        return {"message": "Group muted" if request.mute_until else "Group unmuted"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error muting group: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to mute group")


# --- Group messages ---


async def list_group_messages(db, *, current_user, group_id: str, limit: int, before, after):
    import base64
    import uuid
    from fastapi import HTTPException
    from sqlalchemy import desc

    from config import GROUPS_ENABLED
    from models import GroupMessage

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin", "member"])

    query = messaging_repository.query(db, GroupMessage).filter(GroupMessage.group_id == group_uuid)

    if before:
        try:
            before_uuid = uuid.UUID(before)
            before_msg = messaging_repository.query(db, GroupMessage).filter(GroupMessage.id == before_uuid).first()
            if before_msg:
                query = query.filter(
                    (GroupMessage.created_at < before_msg.created_at)
                    | ((GroupMessage.created_at == before_msg.created_at) & (GroupMessage.id < before_uuid))
                )
        except ValueError:
            pass

    if after:
        try:
            after_uuid = uuid.UUID(after)
            after_msg = messaging_repository.query(db, GroupMessage).filter(GroupMessage.id == after_uuid).first()
            if after_msg:
                query = query.filter(
                    (GroupMessage.created_at > after_msg.created_at)
                    | ((GroupMessage.created_at == after_msg.created_at) & (GroupMessage.id > after_uuid))
                )
        except ValueError:
            pass

    messages = (
        query.order_by(desc(GroupMessage.created_at), desc(GroupMessage.id))
        .limit(limit)
        .all()
    )

    result = []
    for msg in messages:
        message_data = {
            "id": str(msg.id),
            "sender_user_id": msg.sender_user_id,
            "sender_device_id": str(msg.sender_device_id),
            "ciphertext": base64.b64encode(msg.ciphertext).decode("utf-8"),
            "proto": msg.proto,
            "group_epoch": msg.group_epoch,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }
        if msg.reply_to_message_id:
            message_data["reply_to_message_id"] = str(msg.reply_to_message_id)
        result.append(message_data)

    return {"messages": result}


async def send_group_message(db, *, current_user, group_id: str, request):
    import base64
    import uuid
    from datetime import datetime, timedelta
    from fastapi import HTTPException

    from config import (
        E2EE_DM_MAX_MESSAGE_SIZE,
        GROUP_BURST_PER_5S,
        GROUP_BURST_WINDOW_SECONDS,
        GROUP_MESSAGE_RATE_PER_USER_PER_MIN,
        GROUPS_ENABLED,
    )
    from models import E2EEDevice, Group, GroupDelivery, GroupMessage, GroupParticipant
    from utils.redis_pubsub import publish_group_message

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        group_uuid = uuid.UUID(group_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid group ID format")

    group = messaging_repository.query(db, Group).filter(Group.id == group_uuid).first()
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if group.is_closed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Group is closed")

    check_group_role(db, group_uuid, current_user.account_id, ["owner", "admin", "member"])

    if request.group_epoch != group.group_epoch:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="EPOCH_STALE",
            headers={"X-Error-Code": "EPOCH_STALE", "X-Current-Epoch": str(group.group_epoch)},
        )

    sender_device = (
        messaging_repository.query(db, E2EEDevice)
        .filter(E2EEDevice.user_id == current_user.account_id, E2EEDevice.status == "active")
        .first()
    )
    if not sender_device:
        revoked_device = (
            messaging_repository.query(db, E2EEDevice)
            .filter(E2EEDevice.user_id == current_user.account_id, E2EEDevice.status == "revoked")
            .first()
        )
        if revoked_device:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="DEVICE_REVOKED",
                headers={"X-Error-Code": "DEVICE_REVOKED"},
            )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active device found")

    if request.client_message_id:
        existing = (
            messaging_repository.query(db, GroupMessage)
            .filter(GroupMessage.client_message_id == request.client_message_id)
            .first()
        )
        if existing:
            return {
                "id": str(existing.id),
                "client_message_id": existing.client_message_id,
                "created_at": existing.created_at.isoformat() if existing.created_at else None,
            }

    try:
        ciphertext_bytes = base64.b64decode(request.ciphertext)
        if len(ciphertext_bytes) > E2EE_DM_MAX_MESSAGE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Message exceeds maximum size of {E2EE_DM_MAX_MESSAGE_SIZE} bytes",
            )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid base64 ciphertext: {str(exc)}")

    minute_rl = default_rate_limiter.allow(
        key=f"rl:group_chat:minute:{current_user.account_id}",
        limit=GROUP_MESSAGE_RATE_PER_USER_PER_MIN,
        window_seconds=60,
    )
    if not minute_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {GROUP_MESSAGE_RATE_PER_USER_PER_MIN} messages per minute.",
            headers={
                "X-Retry-After": str(minute_rl.retry_after_seconds),
                "X-RateLimit-Limit": str(GROUP_MESSAGE_RATE_PER_USER_PER_MIN),
                "X-RateLimit-Remaining": "0",
            },
        )

    burst_rl = default_rate_limiter.allow(
        key=f"rl:group_chat:burst:{current_user.account_id}:{group_uuid}",
        limit=GROUP_BURST_PER_5S,
        window_seconds=GROUP_BURST_WINDOW_SECONDS,
    )
    if not burst_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Burst rate limit exceeded.",
            headers={
                "X-Retry-After": str(burst_rl.retry_after_seconds),
                "X-RateLimit-Limit": str(GROUP_BURST_PER_5S),
                "X-RateLimit-Remaining": "0",
            },
        )

    reply_to_message_uuid = None
    if request.reply_to_message_id:
        try:
            reply_to_message_uuid = uuid.UUID(request.reply_to_message_id)
            reply_to_message = (
                messaging_repository.query(db, GroupMessage)
                .filter(GroupMessage.id == reply_to_message_uuid, GroupMessage.group_id == group_uuid)
                .first()
            )
            if not reply_to_message:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Message {request.reply_to_message_id} not found in this group",
                )
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid reply_to_message_id format")

    new_message = GroupMessage(
        id=uuid.uuid4(),
        group_id=group_uuid,
        sender_user_id=current_user.account_id,
        sender_device_id=sender_device.device_id,
        ciphertext=ciphertext_bytes,
        proto=request.proto,
        group_epoch=request.group_epoch,
        client_message_id=request.client_message_id,
        reply_to_message_id=reply_to_message_uuid,
    )
    db.add(new_message)
    group.updated_at = datetime.utcnow()

    try:
        db.flush()
        participants = (
            messaging_repository.query(db, GroupParticipant)
            .filter(GroupParticipant.group_id == group_uuid, GroupParticipant.is_banned == False)
            .all()
        )
        deliveries = [
            GroupDelivery(message_id=new_message.id, recipient_user_id=p.user_id)
            for p in participants
            if p.user_id != current_user.account_id
        ]
        if deliveries:
            db.bulk_save_objects(deliveries)
        db.commit()
        db.refresh(new_message)

        event = {
            "type": "group_message",
            "group_id": str(group_uuid),
            "message_id": str(new_message.id),
            "sender_user_id": current_user.account_id,
            "sender_device_id": str(sender_device.device_id),
            "ciphertext": request.ciphertext,
            "proto": request.proto,
            "group_epoch": request.group_epoch,
            "created_at": new_message.created_at.isoformat() if new_message.created_at else None,
        }
        if new_message.reply_to_message_id:
            event["reply_to_message_id"] = str(new_message.reply_to_message_id)
        await publish_group_message(str(group_uuid), event)

        return {
            "id": str(new_message.id),
            "client_message_id": new_message.client_message_id,
            "group_epoch": new_message.group_epoch,
            "created_at": new_message.created_at.isoformat() if new_message.created_at else None,
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"Error sending group message: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to send message")


def mark_group_message_delivered(db, *, current_user, message_id: str):
    import uuid
    from datetime import datetime
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import GroupDelivery, GroupMessage

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message ID format")

    message = messaging_repository.query(db, GroupMessage).filter(GroupMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    delivery = (
        messaging_repository.query(db, GroupDelivery)
        .filter(GroupDelivery.message_id == msg_uuid, GroupDelivery.recipient_user_id == current_user.account_id)
        .first()
    )
    if not delivery:
        delivery = GroupDelivery(
            message_id=msg_uuid,
            recipient_user_id=current_user.account_id,
            delivered_at=datetime.utcnow(),
        )
        db.add(delivery)
    else:
        if not delivery.delivered_at:
            delivery.delivered_at = datetime.utcnow()

    try:
        db.commit()
        return {"message": "Marked as delivered"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error marking delivered: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to mark delivered")


def mark_group_message_read(db, *, current_user, message_id: str):
    import uuid
    from datetime import datetime
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import GroupDelivery, GroupMessage

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message ID format")

    message = messaging_repository.query(db, GroupMessage).filter(GroupMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    delivery = (
        messaging_repository.query(db, GroupDelivery)
        .filter(GroupDelivery.message_id == msg_uuid, GroupDelivery.recipient_user_id == current_user.account_id)
        .first()
    )
    if not delivery:
        delivery = GroupDelivery(
            message_id=msg_uuid,
            recipient_user_id=current_user.account_id,
            delivered_at=datetime.utcnow(),
            read_at=datetime.utcnow(),
        )
        db.add(delivery)
    else:
        if not delivery.read_at:
            delivery.read_at = datetime.utcnow()
        if not delivery.delivered_at:
            delivery.delivered_at = datetime.utcnow()

    try:
        db.commit()
        return {"message": "Marked as read"}
    except Exception as exc:
        db.rollback()
        logger.error(f"Error marking read: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to mark read")


def delete_group_message(db, *, current_user, message_id: str):
    import uuid
    from fastapi import HTTPException

    from config import GROUPS_ENABLED
    from models import GroupMessage, GroupParticipant

    if not GROUPS_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Groups feature is not enabled")

    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message ID format")

    message = messaging_repository.query(db, GroupMessage).filter(GroupMessage.id == msg_uuid).first()
    if not message:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    if message.sender_user_id != current_user.account_id:
        participant = (
            messaging_repository.query(db, GroupParticipant)
            .filter(GroupParticipant.group_id == message.group_id, GroupParticipant.user_id == current_user.account_id)
            .first()
        )
        if not participant or participant.role not in ["owner", "admin"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="FORBIDDEN")

    return {"message": "Message deleted"}


def _require_dm_enabled() -> None:
    if not E2EE_DM_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled"
        )


def send_dm_message(
    db,
    *,
    current_user,
    conversation_id: str,
    request,
    background_tasks: BackgroundTasks,
):
    _require_dm_enabled()

    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid conversation ID"
        )

    conversation = messaging_repository.get_conversation_if_participant(
        db, conversation_id=conv_uuid, user_id=current_user.account_id
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    sender_device = messaging_repository.get_active_device_for_user(
        db, user_id=current_user.account_id
    )
    if not sender_device:
        if messaging_repository.has_revoked_device(db, user_id=current_user.account_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="DEVICE_REVOKED",
                headers={"X-Error-Code": "DEVICE_REVOKED"},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active device found. Please register a device first.",
        )

    if request.client_message_id:
        existing_message = messaging_repository.get_existing_message_by_client_id(
            db,
            conversation_id=conv_uuid,
            sender_user_id=current_user.account_id,
            client_message_id=request.client_message_id,
        )
        if existing_message:
            logger.debug(f"Duplicate message detected: {request.client_message_id}")
            return {
                "message_id": str(existing_message.id),
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True,
            }

    try:
        ciphertext_bytes = base64.b64decode(request.ciphertext)
        if len(ciphertext_bytes) > E2EE_DM_MAX_MESSAGE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Message exceeds maximum size of {E2EE_DM_MAX_MESSAGE_SIZE} bytes",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid base64 ciphertext: {str(exc)}",
        )

    minute_rl = default_rate_limiter.allow(
        key=f"rl:dm:minute:{current_user.account_id}",
        limit=E2EE_DM_MAX_MESSAGES_PER_MINUTE,
        window_seconds=60,
    )
    if not minute_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {E2EE_DM_MAX_MESSAGES_PER_MINUTE} messages per minute.",
            headers={
                "X-Retry-After": str(minute_rl.retry_after_seconds),
                "X-RateLimit-Limit": str(E2EE_DM_MAX_MESSAGES_PER_MINUTE),
                "X-RateLimit-Remaining": "0",
            },
        )

    burst_rl = default_rate_limiter.allow(
        key=f"rl:dm:burst:{current_user.account_id}:{conv_uuid}",
        limit=E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST,
        window_seconds=E2EE_DM_BURST_WINDOW_SECONDS,
    )
    if not burst_rl.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Burst rate limit exceeded. Maximum {E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST} messages "
                f"per {E2EE_DM_BURST_WINDOW_SECONDS} seconds per conversation."
            ),
            headers={
                "X-Retry-After": str(burst_rl.retry_after_seconds),
                "X-RateLimit-Limit": str(E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST),
                "X-RateLimit-Remaining": "0",
            },
        )

    participants = messaging_repository.list_participants(db, conversation_id=conv_uuid)
    recipient_participant = next(
        (p for p in participants if p.user_id != current_user.account_id), None
    )
    if not recipient_participant:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Recipient not found in conversation",
        )
    recipient_user_id = recipient_participant.user_id

    if check_blocked(db, current_user.account_id, recipient_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="BLOCKED",
            headers={"X-Error-Code": "BLOCKED"},
        )

    new_message_id = uuid.uuid4()
    new_message, _delivery_record = messaging_repository.create_message_and_delivery(
        db,
        message_id=new_message_id,
        conversation_id=conv_uuid,
        sender_user_id=current_user.account_id,
        sender_device_id=sender_device.device_id,
        ciphertext=ciphertext_bytes,
        proto=request.proto,
        client_message_id=request.client_message_id,
        recipient_user_id=recipient_user_id,
    )

    conversation.last_message_at = datetime.utcnow()
    db.commit()
    db.refresh(new_message)

    event = {
        "type": "dm",
        "message_id": str(new_message.id),
        "conversation_id": conversation_id,
        "sender_user_id": current_user.account_id,
        "sender_device_id": str(sender_device.device_id),
        "ciphertext": request.ciphertext,
        "proto": request.proto,
        "created_at": new_message.created_at.isoformat(),
    }
    background_tasks.add_task(
        publish_dm_message, conversation_id, recipient_user_id, event
    )

    logger.info(f"Message sent: {new_message.id} in conversation {conversation_id}")
    return {
        "message_id": str(new_message.id),
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False,
    }


def get_dm_messages(
    db, *, current_user, conversation_id: str, limit: int, since: Optional[str]
):
    _require_dm_enabled()

    try:
        conv_uuid = uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid conversation ID"
        )

    participant = messaging_repository.get_participant(
        db, conversation_id=conv_uuid, user_id=current_user.account_id
    )
    if not participant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )

    if since:
        try:
            since_uuid = uuid.UUID(since)
        except ValueError:
            since_uuid = None

        if since_uuid:
            since_message = messaging_repository.get_message(db, message_id=since_uuid)
            if since_message:
                messages = messaging_repository.list_messages_for_conversation_before(
                    db,
                    conversation_id=conv_uuid,
                    limit=limit,
                    cursor_created_at=since_message.created_at,
                    cursor_id=since_uuid,
                )
            else:
                messages = messaging_repository.list_messages_for_conversation(
                    db, conversation_id=conv_uuid, limit=limit
                )
        else:
            messages = messaging_repository.list_messages_for_conversation(
                db, conversation_id=conv_uuid, limit=limit
            )
    else:
        messages = messaging_repository.list_messages_for_conversation(
            db, conversation_id=conv_uuid, limit=limit
        )

    messages = list(reversed(messages))
    return {
        "messages": [
            {
                "id": str(msg.id),
                "sender_user_id": msg.sender_user_id,
                "sender_device_id": str(msg.sender_device_id),
                "ciphertext": base64.b64encode(msg.ciphertext).decode("utf-8"),
                "proto": msg.proto,
                "created_at": msg.created_at.isoformat(),
                "client_message_id": msg.client_message_id,
            }
            for msg in messages
        ]
    }


def mark_dm_delivered(db, *, current_user, message_id: str):
    _require_dm_enabled()
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message ID"
        )

    message = messaging_repository.get_message(db, message_id=msg_uuid)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )

    delivery = messaging_repository.get_delivery(
        db, message_id=msg_uuid, recipient_user_id=current_user.account_id
    )
    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to mark this message as delivered",
        )

    if not delivery.delivered_at:
        delivery.delivered_at = datetime.utcnow()
        db.commit()

    return {"message_id": message_id, "delivered_at": delivery.delivered_at.isoformat()}


def mark_dm_read(db, *, current_user, message_id: str):
    _require_dm_enabled()
    try:
        msg_uuid = uuid.UUID(message_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid message ID"
        )

    message = messaging_repository.get_message(db, message_id=msg_uuid)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )

    delivery = messaging_repository.get_delivery(
        db, message_id=msg_uuid, recipient_user_id=current_user.account_id
    )
    if not delivery:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to mark this message as read",
        )

    if not delivery.read_at:
        delivery.read_at = datetime.utcnow()
        db.commit()

    return {"message_id": message_id, "read_at": delivery.read_at.isoformat()}
