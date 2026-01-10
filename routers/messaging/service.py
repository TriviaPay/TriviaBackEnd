"""Messaging/Realtime service layer."""

import base64
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import BackgroundTasks, HTTPException, status

from config import (
    E2EE_DM_BURST_WINDOW_SECONDS,
    E2EE_DM_ENABLED,
    E2EE_DM_MAX_MESSAGE_SIZE,
    E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST,
    E2EE_DM_MAX_MESSAGES_PER_MINUTE,
)
from utils.chat_blocking import check_blocked
from utils.redis_pubsub import publish_dm_message

from . import repository as messaging_repository

logger = logging.getLogger(__name__)

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
    from utils.chat_mute import add_muted_user, remove_muted_user

    if user_id == current_user.account_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot mute yourself")

    from models import User

    target_user = db.query(User).filter(User.account_id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if muted:
        add_muted_user(current_user.account_id, user_id, db)
        return {"message": f"User {user_id} muted for private chat", "muted": True}
    remove_muted_user(current_user.account_id, user_id, db)
    return {"message": f"User {user_id} unmuted for private chat", "muted": False}


def list_private_chat_muted_users(db, *, current_user):
    from utils.chat_mute import get_muted_users
    from models import User

    muted_user_ids = get_muted_users(current_user.account_id, db)
    muted_users = []
    if muted_user_ids:
        users = db.query(User).filter(User.account_id.in_(muted_user_ids)).all()
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
        db.query(UserPresence)
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
        db.query(UserPresence)
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
                db.query(UserPresence)
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


def create_status_post(db, *, current_user, request):
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
        import asyncio

        if asyncio.iscoroutinefunction(publish_dm_message):
            # publish_dm_message is async; call from async endpoint via await in router if needed
            pass
        else:
            pass
        # router will handle publishing since it's async

    return {
        "id": str(new_post.id),
        "created_at": new_post.created_at.isoformat() if new_post.created_at else None,
        "expires_at": new_post.expires_at.isoformat() if new_post.expires_at else None,
        "audience_count": len(audience_user_ids),
        "audience_user_ids": audience_user_ids,
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

    return {"messages": result_messages, "online_count": online_count}


def cleanup_global_chat_messages(db, *, current_user):
    from datetime import datetime, timedelta

    from fastapi import HTTPException

    from config import GLOBAL_CHAT_ENABLED, GLOBAL_CHAT_RETENTION_DAYS

    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")

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

    from config import (
        GLOBAL_CHAT_BURST_WINDOW_SECONDS,
        GLOBAL_CHAT_ENABLED,
        GLOBAL_CHAT_MAX_MESSAGES_PER_BURST,
        GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE,
    )
    from utils.chat_helpers import get_user_chat_profile_data
    from utils.chat_redis import check_burst_limit, check_rate_limit, enqueue_chat_event
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

    burst_allowed = await check_burst_limit(
        "global",
        current_user.account_id,
        GLOBAL_CHAT_MAX_MESSAGES_PER_BURST,
        GLOBAL_CHAT_BURST_WINDOW_SECONDS,
    )
    if burst_allowed is False:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Burst rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_BURST} messages "
                f"per {GLOBAL_CHAT_BURST_WINDOW_SECONDS} seconds."
            ),
        )
    if burst_allowed is None:
        burst_window_ago = datetime.utcnow() - timedelta(
            seconds=GLOBAL_CHAT_BURST_WINDOW_SECONDS
        )
        recent_burst = messaging_repository.list_recent_global_chat_message_ids_since(
            db,
            user_id=current_user.account_id,
            since_dt=burst_window_ago,
            limit=GLOBAL_CHAT_MAX_MESSAGES_PER_BURST,
        )
        if len(recent_burst) >= GLOBAL_CHAT_MAX_MESSAGES_PER_BURST:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Burst rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_BURST} messages "
                    f"per {GLOBAL_CHAT_BURST_WINDOW_SECONDS} seconds."
                ),
            )

    minute_allowed = await check_rate_limit(
        "global", current_user.account_id, GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE, 60
    )
    if minute_allowed is False:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
            ),
        )
    if minute_allowed is None:
        one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
        recent_messages = messaging_repository.list_recent_global_chat_message_ids_since(
            db,
            user_id=current_user.account_id,
            since_dt=one_minute_ago,
            limit=GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE,
        )
        if len(recent_messages) >= GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
                ),
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

    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="E2EE DM is not enabled")

    rows = messaging_repository.list_blocks_with_users(
        db, blocker_id=current_user.account_id, limit=limit, offset=offset
    )
    blocked_users = []
    for block, blocked_user in rows:
        blocked_users.append(
            {
                "user_id": blocked_user.account_id,
                "username": blocked_user.username,
                "blocked_at": block.created_at.isoformat(),
            }
        )
    return {"blocked_users": blocked_users}


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

    # Rate limiting - per user per minute
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
    recent_messages = messaging_repository.list_recent_sent_message_ids(
        db,
        sender_user_id=current_user.account_id,
        since_dt=one_minute_ago,
        limit=E2EE_DM_MAX_MESSAGES_PER_MINUTE,
    )

    if len(recent_messages) >= E2EE_DM_MAX_MESSAGES_PER_MINUTE:
        oldest_message = messaging_repository.get_oldest_sent_message_since(
            db, sender_user_id=current_user.account_id, since_dt=one_minute_ago
        )
        if oldest_message:
            time_until_reset = (
                oldest_message.created_at + timedelta(minutes=1) - datetime.utcnow()
            ).total_seconds()
            retry_in_seconds = max(1, int(time_until_reset))
        else:
            retry_in_seconds = 60

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {E2EE_DM_MAX_MESSAGES_PER_MINUTE} messages per minute.",
            headers={
                "X-Retry-After": str(retry_in_seconds),
                "X-RateLimit-Limit": str(E2EE_DM_MAX_MESSAGES_PER_MINUTE),
                "X-RateLimit-Remaining": "0",
            },
        )

    # Rate limiting - per conversation burst
    burst_window_start = datetime.utcnow() - timedelta(
        seconds=E2EE_DM_BURST_WINDOW_SECONDS
    )
    recent_conversation_messages = (
        messaging_repository.list_recent_conversation_message_ids(
            db,
            sender_user_id=current_user.account_id,
            conversation_id=conv_uuid,
            since_dt=burst_window_start,
            limit=E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST,
        )
    )

    if len(recent_conversation_messages) >= E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST:
        oldest_burst_message = (
            messaging_repository.get_oldest_conversation_message_since(
                db,
                sender_user_id=current_user.account_id,
                conversation_id=conv_uuid,
                since_dt=burst_window_start,
            )
        )
        if oldest_burst_message:
            time_until_reset = (
                oldest_burst_message.created_at
                + timedelta(seconds=E2EE_DM_BURST_WINDOW_SECONDS)
                - datetime.utcnow()
            ).total_seconds()
            retry_in_seconds = max(1, int(time_until_reset))
        else:
            retry_in_seconds = E2EE_DM_BURST_WINDOW_SECONDS

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Burst rate limit exceeded. Maximum {E2EE_DM_MAX_MESSAGES_PER_CONVERSATION_BURST} messages "
                f"per {E2EE_DM_BURST_WINDOW_SECONDS} seconds per conversation."
            ),
            headers={
                "X-Retry-After": str(retry_in_seconds),
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
