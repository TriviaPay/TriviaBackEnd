"""Trivia/Draws/Rewards service layer."""

from datetime import datetime, timedelta
from typing import Optional
import json

from utils.draw_calculations import get_next_draw_time
from utils.trivia_mode_service import get_today_in_app_timezone

from . import repository as trivia_repository

def get_next_draw_with_prize_pool(db):
    next_draw_time = get_next_draw_time()

    mode_pools = _get_mode_prize_pools(db)
    return {
        "next_draw_time": next_draw_time.isoformat(),
        "mode_pools": mode_pools,
    }


def _get_mode_prize_pools(db):
    """
    Best-effort mode-wise pools for display/telemetry.

    - Bronze/Silver use the subscription-derived pool from `rewards_logic.calculate_mode_prize_pool`.
    """
    from rewards_logic import calculate_mode_prize_pool

    bronze_calc = calculate_mode_prize_pool(db, get_today_in_app_timezone(), "bronze")
    silver_calc = calculate_mode_prize_pool(db, get_today_in_app_timezone(), "silver")

    return {
        "bronze": {
            "reward_type": "money",
            "total_pool": bronze_calc.get("daily_pool") or 0.0,
            "source": "mode_subscription_pool",
            "subscriber_count": bronze_calc.get("subscriber_count", 0),
            "subscription_amount": bronze_calc.get("subscription_amount"),
            "fee_per_user": bronze_calc.get("fee_per_user"),
            "net_per_user": bronze_calc.get("net_per_user"),
            "expenditure_offset": bronze_calc.get("expenditure_offset"),
            "share_applied": bronze_calc.get("share_applied"),
            "prize_pool_share": bronze_calc.get("prize_pool_share"),
        },
        "silver": {
            "reward_type": "money",
            "total_pool": silver_calc.get("daily_pool") or 0.0,
            "source": "mode_subscription_pool",
            "subscriber_count": silver_calc.get("subscriber_count", 0),
            "subscription_amount": silver_calc.get("subscription_amount"),
            "fee_per_user": silver_calc.get("fee_per_user"),
            "net_per_user": silver_calc.get("net_per_user"),
            "expenditure_offset": silver_calc.get("expenditure_offset"),
            "share_applied": silver_calc.get("share_applied"),
            "prize_pool_share": silver_calc.get("prize_pool_share"),
        },
    }


def round_down(value: float, decimals: int = 2) -> float:
    multiplier = 10**decimals
    import math

    return math.floor(value * multiplier) / multiplier


def get_recent_winners(db, current_user):
    from utils.chat_helpers import get_user_chat_profile_data_bulk
    from utils.trivia_mode_service import (
        get_active_draw_date,
        get_today_in_app_timezone,
    )

    draw_date = trivia_repository.get_most_recent_winner_draw_date(db)
    if not draw_date:
        active_date = get_active_draw_date()
        today = get_today_in_app_timezone()
        draw_date = active_date if active_date == today else active_date

    bronze_winners = trivia_repository.get_bronze_winners_for_date(
        db, draw_date, limit=10
    )
    silver_winners = trivia_repository.get_silver_winners_for_date(
        db, draw_date, limit=10
    )

    all_user_ids = {w.account_id for w in bronze_winners} | {
        w.account_id for w in silver_winners
    }
    users = {
        u.account_id: u
        for u in trivia_repository.get_users_by_account_ids(db, all_user_ids)
    }

    profile_map = get_user_chat_profile_data_bulk(list(users.values()), db)

    def _sanitize_subscription_badges(badges):
        return [
            {key: value for key, value in badge.items() if key not in ("name", "price")}
            for badge in (badges or [])
        ]

    result = []
    for winner in bronze_winners:
        user = users.get(winner.account_id)
        if not user:
            continue
        profile_data = profile_map.get(winner.account_id, {})
        badge_data = profile_data.get("badge") or {}
        result.append(
            {
                "mode": "bronze",
                "position": winner.position,
                "username": user.username,
                "user_id": winner.account_id,
                "money_awarded": round_down(float(winner.money_awarded), 2),
                "profile_pic": profile_data.get("profile_pic_url"),
                "badge_image_url": badge_data.get("image_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "subscription_badges": _sanitize_subscription_badges(
                    profile_data.get("subscription_badges", [])
                ),
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
                "draw_date": draw_date.isoformat(),
            }
        )

    for winner in silver_winners:
        user = users.get(winner.account_id)
        if not user:
            continue
        profile_data = profile_map.get(winner.account_id, {})
        badge_data = profile_data.get("badge") or {}
        result.append(
            {
                "mode": "silver",
                "position": winner.position,
                "username": user.username,
                "user_id": winner.account_id,
                "money_awarded": round_down(float(winner.money_awarded), 2),
                "profile_pic": profile_data.get("profile_pic_url"),
                "badge_image_url": badge_data.get("image_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "subscription_badges": _sanitize_subscription_badges(
                    profile_data.get("subscription_badges", [])
                ),
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
                "draw_date": draw_date.isoformat(),
            }
        )

    return result


# --- Trivia live chat ---


def trivia_live_chat_is_active() -> bool:
    import os
    from datetime import datetime, timedelta

    import pytz

    from config import (
        TRIVIA_LIVE_CHAT_ENABLED,
        TRIVIA_LIVE_CHAT_POST_HOURS,
        TRIVIA_LIVE_CHAT_PRE_HOURS,
    )
    from utils.draw_calculations import get_next_draw_time

    if not TRIVIA_LIVE_CHAT_ENABLED:
        return False

    try:
        next_draw_time = get_next_draw_time()
        timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)

        next_chat_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
        next_chat_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)

        prev_draw_time = next_draw_time - timedelta(days=1)
        prev_chat_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
        prev_chat_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)

        return (next_chat_start <= now <= next_chat_end) or (
            prev_chat_start <= now <= prev_chat_end
        )
    except Exception:
        return False


def _ensure_datetime(value):
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.utcnow()


def _ensure_date(value):
    from datetime import date

    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return datetime.utcnow().date()


def publish_to_pusher_trivia_live(
    message_id: int,
    user_id: int,
    username: str,
    profile_pic,
    avatar_url,
    frame_url,
    badge,
    message: str,
    created_at,
    draw_date,
    reply_to=None,
):
    from utils.pusher_client import publish_chat_message_sync

    try:
        created_at_dt = _ensure_datetime(created_at)
        draw_date_val = _ensure_date(draw_date)
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
            "draw_date": draw_date_val.isoformat(),
        }
        if reply_to:
            event_data["reply_to"] = reply_to
        publish_chat_message_sync("trivia-live-chat", "new-message", event_data)
    except Exception:
        pass


def send_push_for_trivia_live_chat_sync(
    message_id: int,
    sender_id: int,
    sender_username: str,
    message: str,
    draw_date,
    created_at,
):
    import asyncio
    from datetime import timedelta

    from db import get_db
    from utils.chat_mute import get_muted_user_ids
    from utils.notification_storage import create_notifications_batch
    from utils.onesignal_client import (
        ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS,
        send_push_notification_async,
    )

    db = next(get_db())
    try:
        created_at_dt = _ensure_datetime(created_at)
        draw_date_val = _ensure_date(draw_date)

        all_players = trivia_repository.list_valid_onesignal_players_excluding_user(
            db, excluded_user_id=sender_id
        )
        if not all_players:
            return

        player_user_ids = {player.user_id for player in all_players}
        muted_user_ids = get_muted_user_ids(list(player_user_ids), "trivia_live", db)
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

        heading = "Trivia Live Chat"
        content = f"{sender_username}: {message[:100]}"
        data = {
            "type": "trivia_live_chat",
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_username": sender_username,
            "message": message,
            "draw_date": draw_date_val.isoformat(),
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

        all_recipient_ids = list(player_user_ids - muted_user_ids)
        if all_recipient_ids:
            create_notifications_batch(
                db=db,
                user_ids=all_recipient_ids,
                title=heading,
                body=content,
                notification_type="chat_trivia_live",
                data=data,
            )
    except Exception:
        pass
    finally:
        db.close()


async def trivia_live_chat_send_message(db, *, current_user, request, background_tasks):
    import os
    from datetime import datetime, timedelta

    import pytz
    from fastapi import HTTPException, status
    from sqlalchemy.exc import IntegrityError

    from config import (
        TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS,
        TRIVIA_LIVE_CHAT_ENABLED,
        TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST,
        TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE,
    )
    from utils.chat_helpers import get_user_chat_profile_data
    from utils.chat_redis import check_burst_limit, check_rate_limit, enqueue_chat_event
    from utils.draw_calculations import get_next_draw_time
    from utils.message_sanitizer import sanitize_message

    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is disabled")

    if not trivia_live_chat_is_active():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is not active")

    message_text = sanitize_message(request.message)
    if not message_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message cannot be empty")

    next_draw_time = get_next_draw_time()
    draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()

    if request.client_message_id:
        existing_message = trivia_repository.get_trivia_live_chat_message_by_client_id(
            db,
            user_id=current_user.account_id,
            draw_date=draw_date,
            client_message_id=request.client_message_id,
        )
        if existing_message:
            return {
                "message_id": existing_message.id,
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True,
            }

    burst_allowed = await check_burst_limit(
        "trivia",
        f"{current_user.account_id}:{draw_date.isoformat()}",
        TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST,
        TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS,
    )
    if burst_allowed is False:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Burst rate limit exceeded. Maximum "
                f"{TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST} messages per "
                f"{TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS} seconds."
            ),
        )
    if burst_allowed is None:
        burst_window_ago = datetime.utcnow() - timedelta(
            seconds=TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS
        )
        recent_burst = trivia_repository.list_recent_trivia_live_chat_message_ids_since(
            db,
            user_id=current_user.account_id,
            since_dt=burst_window_ago,
            limit=TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST,
        )
        if len(recent_burst) >= TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Burst rate limit exceeded. Maximum "
                    f"{TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST} messages per "
                    f"{TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS} seconds."
                ),
            )

    minute_allowed = await check_rate_limit(
        "trivia", current_user.account_id, TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE, 60
    )
    if minute_allowed is False:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Rate limit exceeded. Maximum "
                f"{TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
            ),
        )
    if minute_allowed is None:
        one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
        recent_messages = trivia_repository.count_trivia_live_chat_messages_since(
            db, user_id=current_user.account_id, since_dt=one_minute_ago
        )
        if recent_messages >= TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Rate limit exceeded. Maximum "
                    f"{TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
                ),
            )

    reply_to_message = None
    if request.reply_to_message_id:
        reply_to_message = trivia_repository.get_trivia_live_chat_message_for_draw(
            db, message_id=request.reply_to_message_id, draw_date=draw_date
        )
        if not reply_to_message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Message {request.reply_to_message_id} not found in this session",
            )

    from models import TriviaLiveChatMessage

    new_message = TriviaLiveChatMessage(
        user_id=current_user.account_id,
        draw_date=draw_date,
        message=message_text,
        client_message_id=request.client_message_id,
        reply_to_message_id=request.reply_to_message_id,
    )
    db.add(new_message)

    try:
        existing_viewer = trivia_repository.get_trivia_live_chat_viewer(
            db, user_id=current_user.account_id, draw_date=draw_date
        )
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()
        else:
            trivia_repository.create_trivia_live_chat_viewer(
                db,
                user_id=current_user.account_id,
                draw_date=draw_date,
                last_seen=datetime.utcnow(),
            )
            db.flush()
    except IntegrityError:
        db.rollback()
        existing_viewer = trivia_repository.get_trivia_live_chat_viewer(
            db, user_id=current_user.account_id, draw_date=draw_date
        )
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()

    db.commit()
    db.refresh(new_message)

    profile_data = get_user_chat_profile_data(current_user, db)

    def _display_username(user) -> str:
        if user.username and user.username.strip():
            return user.username
        if user.email:
            return user.email.split("@")[0]
        return f"User{user.account_id}"

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

    event_enqueued = await enqueue_chat_event(
        "trivia_message",
        {
            "pusher_args": {
                "message_id": new_message.id,
                "user_id": current_user.account_id,
                "username": username,
                "profile_pic": profile_data["profile_pic_url"],
                "avatar_url": profile_data["avatar_url"],
                "frame_url": profile_data["frame_url"],
                "badge": profile_data["badge"],
                "message": new_message.message,
                "created_at": new_message.created_at.isoformat(),
                "draw_date": draw_date.isoformat(),
                "reply_to": reply_info,
            },
            "push_args": {
                "message_id": new_message.id,
                "sender_id": current_user.account_id,
                "sender_username": username,
                "message": new_message.message,
                "draw_date": draw_date.isoformat(),
                "created_at": new_message.created_at.isoformat(),
            },
        },
    )

    if not event_enqueued:
        use_worker = os.getenv("USE_WORKER_QUEUE", "false").lower() == "true"
        if use_worker:
            from core.queue import enqueue_task

            background_tasks.add_task(
                enqueue_task,
                name="pusher.trivia_live_chat",
                payload={
                    "message_id": new_message.id,
                    "user_id": current_user.account_id,
                    "username": username,
                    "profile_pic": profile_data["profile_pic_url"],
                    "avatar_url": profile_data["avatar_url"],
                    "frame_url": profile_data["frame_url"],
                    "badge": profile_data["badge"],
                    "message": new_message.message,
                    "created_at": new_message.created_at.isoformat(),
                    "draw_date": draw_date.isoformat(),
                    "reply_to": reply_info,
                },
            )
            background_tasks.add_task(
                enqueue_task,
                name="push.trivia_live_chat",
                payload={
                    "message_id": new_message.id,
                    "sender_id": current_user.account_id,
                    "sender_username": username,
                    "message": new_message.message,
                    "draw_date": draw_date.isoformat(),
                    "created_at": new_message.created_at.isoformat(),
                },
            )
        else:
            background_tasks.add_task(
                publish_to_pusher_trivia_live,
                new_message.id,
                current_user.account_id,
                username,
                profile_data["profile_pic_url"],
                profile_data["avatar_url"],
                profile_data["frame_url"],
                profile_data["badge"],
                new_message.message,
                new_message.created_at,
                draw_date,
                reply_info,
            )
            background_tasks.add_task(
                send_push_for_trivia_live_chat_sync,
                new_message.id,
                current_user.account_id,
                username,
                new_message.message,
                draw_date,
                new_message.created_at,
            )

    return {
        "message_id": new_message.id,
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False,
    }


async def trivia_live_chat_get_messages(db, *, current_user, limit: int):
    import os
    from datetime import datetime, timedelta

    import pytz
    from fastapi import HTTPException, status
    from config import (
        TRIVIA_LIVE_CHAT_ENABLED,
        TRIVIA_LIVE_CHAT_POST_HOURS,
        TRIVIA_LIVE_CHAT_PRE_HOURS,
    )
    from utils.chat_helpers import get_user_chat_profile_data_bulk
    from utils.draw_calculations import get_next_draw_time

    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is disabled")

    if not trivia_live_chat_is_active():
        return {"messages": [], "is_active": False, "message": "Trivia live chat is not currently active"}

    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)

    next_window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    next_window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)

    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)

    in_next_window = next_window_start <= now <= next_window_end
    in_prev_window = prev_window_start <= now <= prev_window_end

    if in_next_window:
        draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
        window_start_utc = next_window_start.astimezone(pytz.UTC).replace(tzinfo=None)
        window_end_utc = next_window_end.astimezone(pytz.UTC).replace(tzinfo=None)
        window_start = next_window_start
        window_end = next_window_end
    elif in_prev_window:
        draw_date = prev_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
        window_start_utc = prev_window_start.astimezone(pytz.UTC).replace(tzinfo=None)
        window_end_utc = prev_window_end.astimezone(pytz.UTC).replace(tzinfo=None)
        window_start = prev_window_start
        window_end = prev_window_end
    else:
        return {"messages": [], "is_active": False, "message": "Trivia live chat is not currently active"}

    messages = trivia_repository.list_trivia_live_chat_messages_in_window(
        db,
        draw_date=draw_date,
        window_start_utc=window_start_utc,
        window_end_utc=window_end_utc,
        limit=limit,
    )

    try:
        existing_viewer = trivia_repository.get_trivia_live_chat_viewer(
            db, user_id=current_user.account_id, draw_date=draw_date
        )
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()
        else:
            trivia_repository.create_trivia_live_chat_viewer(
                db,
                user_id=current_user.account_id,
                draw_date=draw_date,
                last_seen=datetime.utcnow(),
            )
            db.flush()
    except Exception:
        db.rollback()
        existing_viewer = trivia_repository.get_trivia_live_chat_viewer(
            db, user_id=current_user.account_id, draw_date=draw_date
        )
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()
    db.commit()

    cutoff_time = datetime.utcnow() - timedelta(minutes=5)
    active_viewers = trivia_repository.count_trivia_live_chat_active_viewers(
        db, draw_date=draw_date, cutoff_dt=cutoff_time
    )
    total_likes = trivia_repository.count_trivia_live_chat_session_likes(db, draw_date=draw_date)

    reply_message_ids = {msg.reply_to_message_id for msg in messages if msg.reply_to_message_id}
    replied_messages = {}
    if reply_message_ids:
        replied_msgs = trivia_repository.list_trivia_live_chat_messages_by_ids(
            db, ids=reply_message_ids
        )
        replied_messages = {msg.id: msg for msg in replied_msgs}

    users_by_id = {msg.user_id: msg.user for msg in messages if msg.user}
    for replied_msg in replied_messages.values():
        if replied_msg.user:
            users_by_id.setdefault(replied_msg.user_id, replied_msg.user)

    profile_cache = get_user_chat_profile_data_bulk(list(users_by_id.values()), db)

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
        if msg.reply_to_message_id:
            replied_msg = replied_messages.get(msg.reply_to_message_id)
            if replied_msg:
                replied_profile = profile_cache.get(replied_msg.user_id, {})
                reply_info = {
                    "message_id": replied_msg.id,
                    "sender_id": replied_msg.user_id,
                    "sender_username": _display_username(replied_msg.user),
                    "message": replied_msg.message,
                    "sender_profile_pic": replied_profile.get("profile_pic_url"),
                    "sender_avatar_url": replied_profile.get("avatar_url"),
                    "sender_frame_url": replied_profile.get("frame_url"),
                    "sender_badge": replied_profile.get("badge"),
                    "created_at": replied_msg.created_at.isoformat(),
                }

        result_messages.append(
            {
                "id": msg.id,
                "user_id": msg.user_id,
                "username": _display_username(msg.user),
                "profile_pic": profile_data.get("profile_pic_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "frame_url": profile_data.get("frame_url"),
                "badge": profile_data.get("badge"),
                "message": msg.message,
                "created_at": msg.created_at.isoformat(),
                "draw_date": draw_date.isoformat(),
                "reply_to": reply_info,
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
            }
        )

    return {
        "messages": result_messages,
        "is_active": True,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "next_draw_time": next_draw_time.isoformat(),
        "viewer_count": active_viewers,
        "like_count": total_likes,
    }


async def trivia_live_chat_debug_config():
    import os
    from datetime import datetime, timedelta

    import pytz

    from config import (
        TRIVIA_LIVE_CHAT_ENABLED,
        TRIVIA_LIVE_CHAT_POST_HOURS,
        TRIVIA_LIVE_CHAT_PRE_HOURS,
    )
    from utils.draw_calculations import get_next_draw_time

    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)

    next_window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    next_window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)

    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)

    in_next_window = next_window_start <= now <= next_window_end
    in_prev_window = prev_window_start <= now <= prev_window_end

    return {
        "enabled": bool(TRIVIA_LIVE_CHAT_ENABLED),
        "is_active": trivia_live_chat_is_active(),
        "in_next_window": in_next_window,
        "in_prev_window": in_prev_window,
        "current_time": now.isoformat(),
        "next_draw_time": next_draw_time.isoformat(),
        "next_window_start": next_window_start.isoformat(),
        "next_window_end": next_window_end.isoformat(),
        "prev_window_start": prev_window_start.isoformat(),
        "prev_window_end": prev_window_end.isoformat(),
        "pre_hours": TRIVIA_LIVE_CHAT_PRE_HOURS,
        "post_hours": TRIVIA_LIVE_CHAT_POST_HOURS,
    }


async def trivia_live_chat_status(db, *, current_user):
    from config import TRIVIA_LIVE_CHAT_ENABLED
    from fastapi import HTTPException, status

    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is disabled")

    if not trivia_live_chat_is_active():
        info = await trivia_live_chat_debug_config()
        info["message"] = "Trivia live chat is not currently active"
        return info

    info = await trivia_live_chat_debug_config()
    # Determine draw date for like state
    import os
    import pytz
    from datetime import datetime, timedelta
    from config import TRIVIA_LIVE_CHAT_POST_HOURS, TRIVIA_LIVE_CHAT_PRE_HOURS
    from utils.draw_calculations import get_next_draw_time
    from models import TriviaLiveChatLike

    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    if prev_window_start <= now <= prev_window_end:
        draw_date = prev_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    else:
        draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()

    total_likes = trivia_repository.count_trivia_live_chat_session_likes(db, draw_date=draw_date)
    user_liked = trivia_repository.has_trivia_live_chat_session_like(
        db, user_id=current_user.account_id, draw_date=draw_date
    )
    info["like_count"] = total_likes
    info["user_liked"] = user_liked
    return info


async def trivia_live_chat_like(db, *, current_user):
    import os
    from datetime import datetime, timedelta

    import pytz
    from fastapi import HTTPException, status

    from config import (
        TRIVIA_LIVE_CHAT_ENABLED,
        TRIVIA_LIVE_CHAT_POST_HOURS,
        TRIVIA_LIVE_CHAT_PRE_HOURS,
    )
    from models import TriviaLiveChatLike
    from utils.draw_calculations import get_next_draw_time
    from utils.pusher_client import publish_chat_message_sync

    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is disabled")

    if not trivia_live_chat_is_active():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is not currently active")

    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    if prev_window_start <= now <= prev_window_end:
        draw_date = prev_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    else:
        draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()

    existing_like = trivia_repository.get_trivia_live_chat_session_like(
        db, user_id=current_user.account_id, draw_date=draw_date
    )
    total_likes = trivia_repository.count_trivia_live_chat_session_likes(db, draw_date=draw_date)
    if existing_like:
        return {"message": "Already liked", "total_likes": total_likes, "already_liked": True, "draw_date": draw_date.isoformat()}

    trivia_repository.create_trivia_live_chat_session_like(
        db, user_id=current_user.account_id, draw_date=draw_date
    )
    db.commit()

    total_likes = trivia_repository.count_trivia_live_chat_session_likes(db, draw_date=draw_date)
    try:
        publish_chat_message_sync(
            "trivia-live-chat",
            "like-update",
            {
                "draw_date": draw_date.isoformat(),
                "total_likes": total_likes,
                "user_id": current_user.account_id,
            },
        )
    except Exception:
        pass

    return {"message": "Trivia live chat liked successfully", "total_likes": total_likes, "already_liked": False, "draw_date": draw_date.isoformat()}


async def trivia_live_chat_get_likes(db, *, current_user):
    import os
    from datetime import datetime, timedelta

    import pytz
    from fastapi import HTTPException, status

    from config import (
        TRIVIA_LIVE_CHAT_ENABLED,
        TRIVIA_LIVE_CHAT_POST_HOURS,
        TRIVIA_LIVE_CHAT_PRE_HOURS,
    )
    from models import TriviaLiveChatLike
    from utils.draw_calculations import get_next_draw_time

    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is disabled")

    if not trivia_live_chat_is_active():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Trivia live chat is not currently active")

    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    if prev_window_start <= now <= prev_window_end:
        draw_date = prev_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    else:
        draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()

    total_likes = trivia_repository.count_trivia_live_chat_session_likes(db, draw_date=draw_date)
    user_liked = trivia_repository.has_trivia_live_chat_session_like(
        db, user_id=current_user.account_id, draw_date=draw_date
    )
    return {"total_likes": total_likes, "draw_date": draw_date.isoformat(), "user_liked": user_liked}


# --- Free mode ---


def free_mode_current_question(db, *, user):
    from fastapi import HTTPException, status
    from utils.trivia_mode_service import (
        get_active_draw_date,
        get_daily_questions_for_mode,
        get_date_range_for_query,
    )

    questions = get_daily_questions_for_mode(db, "free_mode", user)
    if not questions:
        target_date = get_active_draw_date()
        start_range, end_range = get_date_range_for_query(target_date)
        daily_allocated = trivia_repository.count_free_mode_daily_allocated(
            db, start_range=start_range, end_range=end_range
        )
        pool_size = trivia_repository.count_free_mode_pool_size(db)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No questions available for today",
        )

    for q in questions:
        if q["status"] in ["locked", "viewed"]:
            return {"question": q}
    return {"message": "All questions completed", "questions": questions}


def free_mode_leaderboard(db, *, user, draw_date: Optional[str]):
    from datetime import date

    from fastapi import HTTPException, status

    from core.cache import default_cache
    from core.config import FREE_MODE_LEADERBOARD_CACHE_SECONDS
    from utils.chat_helpers import get_user_chat_profile_data_bulk
    from utils.trivia_mode_service import get_active_draw_date, get_today_in_app_timezone

    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD",
            )
    else:
        active_date = get_active_draw_date()
        today = get_today_in_app_timezone()
        target_date = active_date if active_date == today else active_date

    cache_key = f"free_mode_leaderboard:{target_date.isoformat()}"
    cached = default_cache.get(cache_key)
    if cached is not None:
        return cached

    entries = trivia_repository.list_free_mode_leaderboard_entries(
        db, draw_date=target_date
    )

    user_ids = [entry.account_id for entry in entries]
    users = {}
    if user_ids:
        user_rows = trivia_repository.get_users_by_account_ids(db, account_ids=user_ids)
        users = {u.account_id: u for u in user_rows}
    profile_map = get_user_chat_profile_data_bulk(list(users.values()), db)

    result = []
    for entry in entries:
        user_obj = users.get(entry.account_id)
        if not user_obj:
            continue
        profile_data = profile_map.get(entry.account_id, {})
        badge_data = profile_data.get("badge") or {}
        result.append(
            {
                "position": entry.position,
                "username": user_obj.username,
                "user_id": entry.account_id,
                "gems_awarded": entry.gems_awarded,
                "completed_at": entry.completed_at.isoformat() if entry.completed_at else None,
                "profile_pic": profile_data.get("profile_pic_url"),
                "badge_image_url": badge_data.get("image_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "frame_url": profile_data.get("frame_url"),
                "subscription_badges": profile_data.get("subscription_badges", []),
                "date_won": target_date.isoformat(),
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
            }
        )
    response = {"draw_date": target_date.isoformat(), "leaderboard": result}
    default_cache.set(
        cache_key,
        response,
        ttl_seconds=FREE_MODE_LEADERBOARD_CACHE_SECONDS,
    )
    return response


def free_mode_double_gems(db, *, user, draw_date: Optional[str]):
    from datetime import date

    from fastapi import HTTPException, status

    from utils.trivia_mode_service import get_active_draw_date

    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD",
            )
    else:
        target_date = get_active_draw_date() - date.resolution

    winner = trivia_repository.get_free_mode_winner(
        db, account_id=user.account_id, draw_date=target_date
    )
    if not winner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="You are not a winner for this draw date",
        )
    if winner.double_gems_flag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You have already doubled your gems for this draw",
        )

    doubled_gems = winner.gems_awarded * 2
    winner.double_gems_flag = True
    winner.final_gems = doubled_gems
    user.gems += winner.gems_awarded
    db.commit()
    return {
        "success": True,
        "original_gems": winner.gems_awarded,
        "doubled_gems": doubled_gems,
        "total_gems": user.gems,
        "message": f"Successfully doubled your gems! You now have {doubled_gems} gems for this draw.",
    }


def free_mode_status(db, *, user):
    from fastapi import HTTPException, status

    from utils.trivia_mode_service import get_active_draw_date, get_today_in_app_timezone

    target_date = get_active_draw_date()
    attempts = trivia_repository.list_free_mode_attempts(
        db, account_id=user.account_id, target_date=target_date
    )

    first_three = [a for a in attempts if a.question_order in [1, 2, 3]]
    answered_attempts = [a for a in first_three if a.status in ["answered_correct", "answered_wrong"]]
    questions_answered = len(answered_attempts)
    correct_attempts = [a for a in first_three if a.is_correct == True and a.status == "answered_correct"]
    correct_count = len(correct_attempts)
    correct_orders = {a.question_order for a in correct_attempts}
    completed = correct_count == 3 and correct_orders == {1, 2, 3}

    third_question = next((a for a in attempts if a.question_order == 3 and a.third_question_completed_at), None)
    answers = []
    for attempt in sorted(attempts, key=lambda x: x.question_order):
        answers.append(
            {
                "question_order": attempt.question_order,
                "user_answer": attempt.user_answer,
                "is_correct": attempt.is_correct,
                "answered_at": attempt.answered_at.isoformat() if attempt.answered_at else None,
            }
        )

    today = get_today_in_app_timezone()
    winner_draw_date = target_date if target_date == today else target_date
    is_winner = (
        trivia_repository.get_free_mode_winner(
            db, account_id=user.account_id, draw_date=winner_draw_date
        )
        is not None
    )

    return {
        "progress": {
            "questions_answered": questions_answered,
            "correct_answers": correct_count,
            "total_questions": 3,
            "completed": completed,
            "all_questions_answered": questions_answered == 3,
        },
        "completion_time": third_question.third_question_completed_at.isoformat() if third_question else None,
        "is_winner": is_winner,
        "current_date": target_date.isoformat(),
        "fill_in_answer": answers,
    }


# --- Bronze/Silver modes ---


def _ensure_mode_config(
    db,
    *,
    mode_id: str,
    subscription_amount: float,
    mode_name: str,
    fee_per_user: float = 0.0,
    expenditure_offset: int = 0,
    prize_pool_share: Optional[float] = None,
):
    import json

    from fastapi import HTTPException

    from models import TriviaModeConfig
    from utils.trivia_mode_service import get_mode_config

    mode_config = get_mode_config(db, mode_id)
    if mode_config:
        return mode_config

    try:
        reward_distribution = {
            "reward_type": "money",
            "distribution_method": "harmonic_sum",
            "requires_subscription": True,
            "subscription_amount": subscription_amount,
        }
        config_kwargs = {
            "mode_id": mode_id,
            "mode_name": mode_name,
            "questions_count": 1,
            "reward_distribution": json.dumps(reward_distribution),
            "amount": subscription_amount,
            "fee_per_user": fee_per_user,
            "expenditure_offset": expenditure_offset,
            "leaderboard_types": json.dumps(["daily"]),
            "ad_config": json.dumps({}),
            "survey_config": json.dumps({}),
        }
        if prize_pool_share is not None:
            config_kwargs["prize_pool_share"] = prize_pool_share

        mode_config = TriviaModeConfig(**config_kwargs)
        db.add(mode_config)
        db.commit()
        db.refresh(mode_config)
        return mode_config
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Mode configuration not found and could not be created",
        )


def ensure_bronze_mode_config(db):
    return _ensure_mode_config(
        db,
        mode_id="bronze",
        subscription_amount=5.0,
        mode_name="Bronze Mode - First-Come Reward",
        fee_per_user=1.0,
        expenditure_offset=200,
        prize_pool_share=0.82,
    )


def ensure_silver_mode_config(db):
    return _ensure_mode_config(
        db,
        mode_id="silver",
        subscription_amount=10.0,
        mode_name="Silver Mode - First-Come Reward",
    )


async def bronze_mode_get_question(db, *, user):
    from fastapi import HTTPException, status
    from utils.subscription_service import check_mode_access
    from utils.trivia_mode_service import (
        get_active_draw_date,
        get_correct_answer_letter,
        get_date_range_for_query,
    )

    ensure_bronze_mode_config(db)

    access_check = check_mode_access(db, user, "bronze")
    if not access_check["has_access"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=access_check["message"])

    target_date = get_active_draw_date()
    start_datetime, end_datetime = get_date_range_for_query(target_date)

    daily_question = trivia_repository.get_bronze_daily_question(
        db, start_datetime=start_datetime, end_datetime=end_datetime
    )

    if not daily_question:
        selected_question = trivia_repository.get_random_unused_bronze_question(db)
        if not selected_question:
            selected_question = trivia_repository.get_random_bronze_question(db)
            if not selected_question:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No questions available in the question pool. Please add questions first.",
                )

        daily_question = trivia_repository.create_bronze_daily_question(
            db, start_datetime=start_datetime, question_id=selected_question.id
        )
        trivia_repository.mark_bronze_question_used(db, question=selected_question)
        db.commit()
        db.refresh(daily_question)
        daily_question = trivia_repository.get_bronze_daily_question_by_id(
            db, daily_id=daily_question.id
        )

    user_attempt = trivia_repository.get_bronze_attempt(
        db, account_id=user.account_id, target_date=target_date
    )

    question = daily_question.question
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    answered_at = (
        user_attempt.submitted_at.isoformat()
        if user_attempt and user_attempt.submitted_at
        else None
    )
    return {
        "question": {
            "question_id": question.id,
            "id": question.id,
            "question": question.question,
            "option_a": question.option_a,
            "option_b": question.option_b,
            "option_c": question.option_c,
            "option_d": question.option_d,
            "correct_answer": get_correct_answer_letter(question),
            "hint": question.hint,
            "fill_in_answer": (
                user_attempt.user_answer
                if user_attempt and user_attempt.user_answer
                else None
            ),
            "explanation": question.explanation,
            "category": question.category,
            "difficulty_level": question.difficulty_level,
            "picture_url": question.picture_url,
            "status": user_attempt.status if user_attempt else "locked",
            "is_correct": user_attempt.is_correct if user_attempt else None,
            "answered_at": answered_at,
            "date": target_date.isoformat(),
        },
        "has_submitted": bool(user_attempt and user_attempt.submitted_at),
        "submitted_at": answered_at,
        "has_access": access_check["has_access"],
        "subscription_status": access_check.get("subscription_status"),
    }


async def silver_mode_get_question(db, *, user):
    import random

    from fastapi import HTTPException, status
    from utils.subscription_service import check_mode_access
    from utils.trivia_mode_service import (
        get_active_draw_date,
        get_correct_answer_letter,
        get_date_range_for_query,
    )

    ensure_silver_mode_config(db)

    access_check = check_mode_access(db, user, "silver")
    if not access_check["has_access"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=access_check["message"])

    target_date = get_active_draw_date()
    start_datetime, end_datetime = get_date_range_for_query(target_date)

    daily_question = trivia_repository.get_silver_daily_question(
        db, start_datetime=start_datetime, end_datetime=end_datetime
    )

    if not daily_question:
        unused_count = trivia_repository.count_unused_silver_questions(db)
        if unused_count < 1:
            total_count = trivia_repository.count_silver_questions(db)
            if total_count < 1:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No questions available in the question pool. Please add questions first.",
                )
            random_offset = random.randrange(total_count)
            selected_question = trivia_repository.get_silver_question_by_offset(
                db, offset=random_offset
            )
        else:
            random_offset = random.randrange(unused_count)
            selected_question = trivia_repository.get_unused_silver_question_by_offset(
                db, offset=random_offset
            )
        if not selected_question:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No questions available in the question pool. Please add questions first.",
            )

        daily_question = trivia_repository.create_silver_daily_question(
            db, start_datetime=start_datetime, question_id=selected_question.id
        )
        trivia_repository.mark_silver_question_used(db, question=selected_question)
        db.commit()
        db.refresh(daily_question)
        daily_question = trivia_repository.get_silver_daily_question_by_id(
            db, daily_id=daily_question.id
        )

    user_attempt = trivia_repository.get_silver_attempt(
        db, account_id=user.account_id, target_date=target_date
    )

    question = daily_question.question
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    answered_at = (
        user_attempt.submitted_at.isoformat()
        if user_attempt and user_attempt.submitted_at
        else None
    )
    return {
        "question": {
            "question_id": question.id,
            "id": question.id,
            "question": question.question,
            "option_a": question.option_a,
            "option_b": question.option_b,
            "option_c": question.option_c,
            "option_d": question.option_d,
            "correct_answer": get_correct_answer_letter(question),
            "hint": question.hint,
            "fill_in_answer": (
                user_attempt.user_answer
                if user_attempt and user_attempt.user_answer
                else None
            ),
            "explanation": question.explanation,
            "category": question.category,
            "difficulty_level": question.difficulty_level,
            "picture_url": question.picture_url,
            "status": user_attempt.status if user_attempt else "locked",
            "is_correct": user_attempt.is_correct if user_attempt else None,
            "answered_at": answered_at,
            "date": target_date.isoformat(),
        },
        "has_submitted": bool(user_attempt and user_attempt.submitted_at),
        "submitted_at": answered_at,
        "has_access": access_check["has_access"],
        "subscription_status": access_check.get("subscription_status"),
    }


async def _mode_submit_answer(
    db,
    *,
    user,
    mode_id: str,
    question_model,
    daily_model,
    attempt_model,
    winners_model,
    request,
):
    import os

    import pytz
    from fastapi import HTTPException, status

    from utils.subscription_service import check_mode_access
    from utils.trivia_mode_service import get_active_draw_date, get_correct_answer_letter, get_date_range_for_query

    if mode_id == "bronze":
        ensure_bronze_mode_config(db)
    else:
        ensure_silver_mode_config(db)

    access_check = check_mode_access(db, user, mode_id)
    if not access_check["has_access"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=access_check["message"])

    target_date = get_active_draw_date()
    existing_attempt = trivia_repository.get_mode_attempt(
        db,
        attempt_model=attempt_model,
        account_id=user.account_id,
        target_date=target_date,
    )
    if existing_attempt and existing_attempt.submitted_at:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="You have already submitted an answer for today")

    question = trivia_repository.get_mode_question(
        db,
        question_model=question_model,
        question_id=request.question_id,
    )
    if not question:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    start_datetime, end_datetime = get_date_range_for_query(target_date)
    daily_q = trivia_repository.get_mode_daily_record(
        db,
        daily_model=daily_model,
        question_id=request.question_id,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
    )
    if not daily_q:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question not available for today")

    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    draw_time_hour = int(os.getenv("DRAW_TIME_HOUR", "18"))
    draw_time_minute = int(os.getenv("DRAW_TIME_MINUTE", "0"))
    draw_time = now.replace(hour=draw_time_hour, minute=draw_time_minute, second=0, microsecond=0)
    if now >= draw_time:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question submission is closed")

    correct_letter = get_correct_answer_letter(question)
    submitted_letter = (request.answer or "").strip().lower()
    is_correct = submitted_letter == correct_letter

    if existing_attempt:
        existing_attempt.user_answer = request.answer
        existing_attempt.is_correct = is_correct
        existing_attempt.submitted_at = datetime.utcnow()
        existing_attempt.status = "answered"
    else:
        user_attempt = attempt_model(
            account_id=user.account_id,
            date=target_date,
            question_id=request.question_id,
            user_answer=request.answer,
            is_correct=is_correct,
            submitted_at=datetime.utcnow(),
            status="answered",
        )
        db.add(user_attempt)
    db.commit()

    from utils.user_level_service import track_answer_and_update_level

    level_info = track_answer_and_update_level(user, db)
    return {
        "status": "success",
        "is_correct": is_correct,
        "submitted_at": datetime.utcnow().isoformat(),
        "message": "Answer submitted successfully",
        "level_info": level_info,
    }


async def bronze_mode_submit_answer(db, *, user, request):
    from models import (
        TriviaBronzeModeWinners,
        TriviaQuestionsBronzeMode,
        TriviaQuestionsBronzeModeDaily,
        TriviaUserBronzeModeDaily,
    )

    return await _mode_submit_answer(
        db,
        user=user,
        mode_id="bronze",
        question_model=TriviaQuestionsBronzeMode,
        daily_model=TriviaQuestionsBronzeModeDaily,
        attempt_model=TriviaUserBronzeModeDaily,
        winners_model=TriviaBronzeModeWinners,
        request=request,
    )


async def silver_mode_submit_answer(db, *, user, request):
    from models import (
        TriviaQuestionsSilverMode,
        TriviaQuestionsSilverModeDaily,
        TriviaSilverModeWinners,
        TriviaUserSilverModeDaily,
    )

    return await _mode_submit_answer(
        db,
        user=user,
        mode_id="silver",
        question_model=TriviaQuestionsSilverMode,
        daily_model=TriviaQuestionsSilverModeDaily,
        attempt_model=TriviaUserSilverModeDaily,
        winners_model=TriviaSilverModeWinners,
        request=request,
    )


def bronze_mode_status(db, *, user):
    from fastapi import HTTPException, status

    from utils.subscription_service import check_mode_access
    from utils.trivia_mode_service import get_active_draw_date, get_today_in_app_timezone

    ensure_bronze_mode_config(db)
    target_date = get_active_draw_date()
    access_check = check_mode_access(db, user, "bronze")
    user_attempt = trivia_repository.get_bronze_attempt(
        db, account_id=user.account_id, target_date=target_date
    )
    today = get_today_in_app_timezone()
    winner_draw_date = target_date if target_date == today else target_date
    is_winner = (
        trivia_repository.get_bronze_winner_for_user(
            db, account_id=user.account_id, draw_date=winner_draw_date
        )
        is not None
    )
    return {
        "has_access": access_check["has_access"],
        "subscription_status": access_check.get("subscription_status"),
        "has_submitted": bool(user_attempt and user_attempt.submitted_at),
        "submitted_at": user_attempt.submitted_at.isoformat() if user_attempt and user_attempt.submitted_at else None,
        "is_correct": user_attempt.is_correct if user_attempt else None,
        "fill_in_answer": user_attempt.user_answer if user_attempt and user_attempt.user_answer else None,
        "is_winner": is_winner,
        "current_date": target_date.isoformat(),
    }


def silver_mode_status(db, *, user):
    from utils.subscription_service import check_mode_access
    from utils.trivia_mode_service import get_active_draw_date, get_today_in_app_timezone
    from fastapi import HTTPException, status

    ensure_silver_mode_config(db)
    access_check = check_mode_access(db, user, "silver")
    if not access_check["has_access"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=access_check["message"])

    target_date = get_active_draw_date()
    user_attempt = trivia_repository.get_silver_attempt(
        db, account_id=user.account_id, target_date=target_date
    )
    today = get_today_in_app_timezone()
    winner_draw_date = target_date if target_date == today else target_date
    is_winner = (
        trivia_repository.get_silver_winner_for_user(
            db, account_id=user.account_id, draw_date=winner_draw_date
        )
        is not None
    )
    return {
        "has_submitted": bool(user_attempt and user_attempt.submitted_at),
        "submitted_at": user_attempt.submitted_at.isoformat() if user_attempt and user_attempt.submitted_at else None,
        "is_correct": user_attempt.is_correct if user_attempt else None,
        "fill_in_answer": user_attempt.user_answer if user_attempt and user_attempt.user_answer else None,
        "is_winner": is_winner,
        "current_date": target_date.isoformat(),
    }


def _mode_leaderboard(db, *, mode_id: str, leaderboard_model, draw_date: Optional[str]):
    from datetime import date

    from fastapi import HTTPException, status

    from utils.chat_helpers import get_user_chat_profile_data_bulk
    from utils.trivia_mode_service import get_active_draw_date, get_today_in_app_timezone

    if draw_date:
        try:
            target_date = date.fromisoformat(draw_date)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid date format. Use YYYY-MM-DD")
    else:
        active_date = get_active_draw_date()
        today = get_today_in_app_timezone()
        target_date = active_date if active_date == today else active_date

    entries = trivia_repository.list_mode_leaderboard_entries(
        db, leaderboard_model=leaderboard_model, draw_date=target_date
    )

    result = []
    if entries:
        account_ids = [entry.account_id for entry in entries]
        users = trivia_repository.get_users_by_account_ids(
            db, account_ids=list(set(account_ids))
        )
        users_by_id = {u.account_id: u for u in users}
        profile_cache = get_user_chat_profile_data_bulk(list(users_by_id.values()), db)
        for entry in entries:
            user_obj = users_by_id.get(entry.account_id)
            if not user_obj:
                continue
            profile_data = profile_cache.get(entry.account_id, {})
            badge_data = profile_data.get("badge") or {}
            row = {
                "position": entry.position,
                "username": user_obj.username,
                "user_id": entry.account_id,
                "submitted_at": entry.submitted_at.isoformat() if entry.submitted_at else None,
                "profile_pic": profile_data.get("profile_pic_url"),
                "badge_image_url": badge_data.get("image_url"),
                "avatar_url": profile_data.get("avatar_url"),
                "frame_url": profile_data.get("frame_url"),
                "subscription_badges": profile_data.get("subscription_badges", []),
                "date_won": target_date.isoformat(),
                "level": profile_data.get("level", 1),
                "level_progress": profile_data.get("level_progress", "0/100"),
            }
            if hasattr(entry, "money_awarded"):
                row["money_awarded"] = entry.money_awarded
            result.append(row)

    return {"draw_date": target_date.isoformat(), "leaderboard": result}


def bronze_mode_leaderboard(db, *, draw_date: Optional[str]):
    from models import TriviaBronzeModeLeaderboard

    return _mode_leaderboard(db, mode_id="bronze", leaderboard_model=TriviaBronzeModeLeaderboard, draw_date=draw_date)


def silver_mode_leaderboard(db, *, draw_date: Optional[str]):
    from models import TriviaSilverModeLeaderboard

    return _mode_leaderboard(db, mode_id="silver", leaderboard_model=TriviaSilverModeLeaderboard, draw_date=draw_date)


# --- Internal endpoints ---


def _internal_is_authorized(secret: str) -> bool:
    import os
    import secrets

    return secrets.compare_digest(secret or "", os.getenv("INTERNAL_SECRET", ""))


def _advisory_lock_key(value: str) -> int:
    import hashlib

    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1)


def _try_advisory_lock(db, key: int) -> bool:
    try:
        return bool(trivia_repository.try_advisory_lock(db, key=key))
    except Exception:
        return False


def _release_advisory_lock(db, key: int) -> None:
    try:
        trivia_repository.advisory_unlock(db, key=key)
    except Exception:
        return None


def internal_health():
    return {
        "status": "healthy",
        "service": "triviapay-internal",
        "timestamp": datetime.utcnow().isoformat(),
    }


def internal_monthly_reset(db, *, secret: str):
    from fastapi import HTTPException, status

    from rewards_logic import reset_monthly_subscriptions
    from updated_scheduler import get_detailed_monthly_reset_metrics

    if not _internal_is_authorized(secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    metrics = get_detailed_monthly_reset_metrics(db)
    reset_monthly_subscriptions(db)
    return {
        "status": "success",
        "message": "All subscription flags reset",
        "triggered_by": "external_cron",
        "detailed_metrics": metrics,
        "timestamp": datetime.now().isoformat(),
    }


def internal_weekly_rewards_reset(db, *, secret: str):
    from fastapi import HTTPException, status

    from rewards_logic import reset_weekly_daily_rewards

    if not _internal_is_authorized(secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    reset_weekly_daily_rewards(db)
    return {
        "status": "success",
        "message": "All weekly daily rewards reset",
        "triggered_by": "external_cron",
        "timestamp": datetime.now().isoformat(),
    }


def internal_daily_revenue_update(db, *, secret: str):
    from fastapi import HTTPException, status

    from rewards_logic import calculate_prize_pool
    from utils.trivia_mode_service import get_today_in_app_timezone

    if not _internal_is_authorized(secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    draw_date = get_today_in_app_timezone()
    calculate_prize_pool(db, draw_date, commit_revenue=True)
    return {
        "status": "success",
        "message": "Company revenue updated",
        "triggered_by": "external_cron",
        "draw_date": draw_date.isoformat(),
        "timestamp": datetime.now().isoformat(),
    }


def internal_free_mode_draw(db, *, secret: str):
    from fastapi import HTTPException, status

    from utils.free_mode_rewards import (
        calculate_reward_distribution,
        cleanup_old_leaderboard,
        distribute_rewards_to_winners,
        get_eligible_participants_free_mode,
        rank_participants_by_completion,
    )
    from utils.trivia_mode_service import get_active_draw_date, get_mode_config

    if not _internal_is_authorized(secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    lock_key = None
    try:
        draw_date = get_active_draw_date() - timedelta(days=1)
        lock_key = _advisory_lock_key(f"free_mode:{draw_date.isoformat()}")
        if not _try_advisory_lock(db, lock_key):
            return {"status": "already_running", "draw_date": draw_date.isoformat(), "message": "Draw already running"}

        existing_draw = trivia_repository.get_any_free_mode_winner_for_draw(
            db, draw_date=draw_date
        )
        if existing_draw:
            return {"status": "already_performed", "draw_date": draw_date.isoformat(), "message": f"Draw for {draw_date} has already been performed"}

        mode_config = get_mode_config(db, "free_mode")
        if not mode_config:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Free mode config not found")

        participants = get_eligible_participants_free_mode(db, draw_date)
        if not participants:
            return {"status": "no_participants", "draw_date": draw_date.isoformat(), "message": f"No eligible participants for draw on {draw_date}", "total_participants": 0}

        ranked_participants = rank_participants_by_completion(participants)
        reward_info = calculate_reward_distribution(mode_config, len(ranked_participants))
        winner_count = reward_info["winner_count"]
        gem_amounts = reward_info["gem_amounts"]

        winners_list = ranked_participants if len(ranked_participants) <= winner_count else ranked_participants[:winner_count]
        winners = []
        for i, participant in enumerate(winners_list):
            winners.append(
                {
                    "account_id": participant["account_id"],
                    "username": participant["username"],
                    "position": i + 1,
                    "gems_awarded": gem_amounts[i] if i < len(gem_amounts) else 0,
                    "completed_at": participant["third_question_completed_at"],
                }
            )

        distribution_result = distribute_rewards_to_winners(db, winners, mode_config, draw_date)
        cleanup_old_leaderboard(db, draw_date - timedelta(days=1))

        winner_ids = [winner["account_id"] for winner in winners]
        winners_data = []
        if winner_ids:
            users = trivia_repository.get_users_by_account_ids(db, account_ids=winner_ids)
            users_by_id = {u.account_id: u for u in users}
            for winner in winners:
                u = users_by_id.get(winner["account_id"])
                if u:
                    winners_data.append(
                        {
                            "position": winner.get("position"),
                            "username": winner.get("username"),
                            "email": u.email if u.email else None,
                            "gems_awarded": winner.get("gems_awarded", 0),
                        }
                    )

        return {
            "status": "success",
            "draw_date": draw_date.isoformat(),
            "total_participants": len(ranked_participants),
            "total_winners": len(winners),
            "total_gems_awarded": distribution_result.get("total_gems_awarded"),
            "winners": winners_data,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error in free mode draw")
    finally:
        if lock_key is not None:
            _release_advisory_lock(db, lock_key)


def internal_mode_draw(db, *, secret: str, mode_id: str):
    from fastapi import HTTPException, status

    from utils.mode_draw_service import execute_mode_draw
    from utils.trivia_mode_service import get_active_draw_date, get_mode_config

    if not _internal_is_authorized(secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    lock_key = None
    try:
        draw_date = get_active_draw_date() - timedelta(days=1)
        lock_key = _advisory_lock_key(f"{mode_id}:{draw_date.isoformat()}")
        if not _try_advisory_lock(db, lock_key):
            return {"status": "already_running", "draw_date": draw_date.isoformat(), "message": "Draw already running"}

        if mode_id == "free_mode":
            existing_draw = trivia_repository.get_any_free_mode_winner_for_draw(
                db, draw_date=draw_date
            )
        elif mode_id == "bronze":
            existing_draw = trivia_repository.get_any_bronze_winner_for_draw(
                db, draw_date=draw_date
            )
        elif mode_id == "silver":
            existing_draw = trivia_repository.get_any_silver_winner_for_draw(
                db, draw_date=draw_date
            )
        else:
            existing_draw = None
        if existing_draw:
            return {"status": "already_performed", "draw_date": draw_date.isoformat(), "message": f"Draw for {draw_date} has already been performed"}

        result = execute_mode_draw(db, mode_id, draw_date)
        if result.get("status") == "no_participants":
            return {"status": "no_participants", "draw_date": draw_date.isoformat(), "message": f"No eligible participants for draw on {draw_date}", "total_participants": 0}
        if result.get("status") != "success":
            return {"status": result.get("status", "error"), "draw_date": draw_date.isoformat(), "message": result.get("message", "Unknown error")}

        mode_config = get_mode_config(db, mode_id)
        if not mode_config:
            return {"status": "error", "draw_date": draw_date.isoformat(), "message": f"Mode config not found for {mode_id}"}

        winners = result.get("winners", [])
        winner_ids = [winner["account_id"] for winner in winners]
        users_by_id = {}
        if winner_ids:
            users = trivia_repository.get_users_by_account_ids(db, account_ids=winner_ids)
            users_by_id = {u.account_id: u for u in users}

        winners_data = []
        for winner in winners:
            u = users_by_id.get(winner["account_id"])
            if not u:
                continue
            winner_data = {
                "position": winner.get("position"),
                "username": winner.get("username"),
                "email": u.email if u.email else None,
            }
            if "gems_awarded" in winner:
                winner_data["gems_awarded"] = winner["gems_awarded"]
            if "reward_amount" in winner:
                winner_data["money_awarded"] = winner["reward_amount"]
            winners_data.append(winner_data)

        return {
            "status": "success",
            "draw_date": draw_date.isoformat(),
            "total_participants": result.get("total_participants", 0),
            "total_winners": len(winners),
            "winners": winners_data,
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error in {mode_id} draw")
    finally:
        if lock_key is not None:
            _release_advisory_lock(db, lock_key)


def _build_trivia_reminder_players_query(db, *, active_draw_date, only_incomplete_users: bool):
    return trivia_repository.list_onesignal_players_for_reminder(
        db, only_incomplete_users=only_incomplete_users, active_draw_date=active_draw_date
    )


def _send_trivia_reminder_job(active_draw_date, heading: str, message: str, only_incomplete_users: bool):
    import asyncio
    import math
    import os

    from fastapi import HTTPException

    from config import ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY
    from db import get_db
    from utils.onesignal_client import send_push_notification_async
    from utils.notification_storage import create_notifications_batch

    db = next(get_db())
    try:
        players_q = _build_trivia_reminder_players_query(
            db, active_draw_date=active_draw_date, only_incomplete_users=only_incomplete_users
        )
        players = players_q.all()
        player_ids = [p.player_id for p in players if p.player_id]
        user_ids = list({p.user_id for p in players if p.user_id})

        batch_size = 2000
        batches = [player_ids[i : i + batch_size] for i in range(0, len(player_ids), batch_size)]

        data = {"type": "trivia_reminder", "draw_date": active_draw_date.isoformat()}

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        for batch in batches:
            loop.run_until_complete(
                send_push_notification_async(
                    player_ids=batch,
                    heading=heading,
                    content=message,
                    data=data,
                    is_in_app_notification=False,
                )
            )

        if user_ids:
            create_notifications_batch(
                db=db,
                user_ids=user_ids,
                title=heading,
                body=message,
                notification_type="trivia_reminder",
                data=data,
            )
            db.commit()
    finally:
        db.close()


def internal_trivia_reminder(db, *, secret: str, request, background_tasks):
    from fastapi import HTTPException, status

    from config import ONESIGNAL_ENABLED, ONESIGNAL_APP_ID, ONESIGNAL_REST_API_KEY
    from utils.trivia_mode_service import get_active_draw_date

    if not _internal_is_authorized(secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    if not ONESIGNAL_ENABLED:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="OneSignal is disabled")
    if not ONESIGNAL_APP_ID or not ONESIGNAL_REST_API_KEY:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="OneSignal credentials not configured.")

    active_draw_date = get_active_draw_date()
    players_q = _build_trivia_reminder_players_query(
        db, active_draw_date=active_draw_date, only_incomplete_users=request.only_incomplete_users
    )
    players_subq = players_q.subquery()
    from sqlalchemy import func

    total_targeted = trivia_repository.count_rows_in_subquery(db, subquery=players_subq)
    total_users = trivia_repository.count_distinct_users_in_subquery(
        db, subquery=players_subq
    )
    if total_targeted == 0:
        return {"status": "no_players", "sent_to": 0, "draw_date": active_draw_date.isoformat(), "only_incomplete_users": request.only_incomplete_users}

    background_tasks.add_task(
        _send_trivia_reminder_job,
        active_draw_date,
        request.heading,
        request.message,
        request.only_incomplete_users,
    )

    return {
        "status": "queued",
        "targeted_players": total_targeted,
        "targeted_users": total_users,
        "draw_date": active_draw_date.isoformat(),
        "only_incomplete_users": request.only_incomplete_users,
    }


    return {
        "draw_date": draw_date.isoformat(),
        "total_winners": len(result),
        "bronze_winners": len([w for w in result if w["mode"] == "bronze"]),
        "silver_winners": len([w for w in result if w["mode"] == "silver"]),
        "winners": result,
    }


def get_daily_login_status(db, user):
    from utils.trivia_mode_service import get_today_in_app_timezone

    today = get_today_in_app_timezone()
    week_start = today - timedelta(days=today.weekday())

    user_rewards = trivia_repository.get_user_daily_rewards_for_week(
        db, user.account_id, week_start
    )

    if not user_rewards:
        days_claimed = []
        total_gems_earned = 0
    else:
        days_claimed = []
        if user_rewards.day1_status:
            days_claimed.append(1)
        if user_rewards.day2_status:
            days_claimed.append(2)
        if user_rewards.day3_status:
            days_claimed.append(3)
        if user_rewards.day4_status:
            days_claimed.append(4)
        if user_rewards.day5_status:
            days_claimed.append(5)
        if user_rewards.day6_status:
            days_claimed.append(6)
        if user_rewards.day7_status:
            days_claimed.append(7)

        total_gems_earned = len([d for d in days_claimed if d != 7]) * 10
        if 7 in days_claimed:
            total_gems_earned += 30

    current_day = today.weekday() + 1
    days_remaining = 7 - len(days_claimed)

    return {
        "week_start_date": week_start.isoformat(),
        "current_day": current_day,
        "days_claimed": days_claimed,
        "days_remaining": days_remaining,
        "total_gems_earned_this_week": total_gems_earned,
        "day_status": {
            "monday": user_rewards.day1_status if user_rewards else False,
            "tuesday": user_rewards.day2_status if user_rewards else False,
            "wednesday": user_rewards.day3_status if user_rewards else False,
            "thursday": user_rewards.day4_status if user_rewards else False,
            "friday": user_rewards.day5_status if user_rewards else False,
            "saturday": user_rewards.day6_status if user_rewards else False,
            "sunday": user_rewards.day7_status if user_rewards else False,
        },
    }


def process_daily_login(db, user):
    from fastapi import HTTPException, status

    from utils.trivia_mode_service import get_today_in_app_timezone

    today = get_today_in_app_timezone()
    week_start = today - timedelta(days=today.weekday())

    user_rewards = trivia_repository.get_user_daily_rewards_for_week(
        db, user.account_id, week_start
    )
    if not user_rewards:
        user_rewards = trivia_repository.create_user_daily_rewards_for_week(
            db, user.account_id, week_start
        )

    day_of_week = today.weekday() + 1
    day_status_map = {
        1: user_rewards.day1_status,
        2: user_rewards.day2_status,
        3: user_rewards.day3_status,
        4: user_rewards.day4_status,
        5: user_rewards.day5_status,
        6: user_rewards.day6_status,
        7: user_rewards.day7_status,
    }

    if day_status_map[day_of_week]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Daily reward already claimed today",
        )

    gems_earned = 30 if day_of_week == 7 else 10
    user.gems += gems_earned

    if day_of_week == 1:
        user_rewards.day1_status = True
    elif day_of_week == 2:
        user_rewards.day2_status = True
    elif day_of_week == 3:
        user_rewards.day3_status = True
    elif day_of_week == 4:
        user_rewards.day4_status = True
    elif day_of_week == 5:
        user_rewards.day5_status = True
    elif day_of_week == 6:
        user_rewards.day6_status = True
    else:
        user_rewards.day7_status = True

    db.commit()
    db.refresh(user_rewards)

    return {
        "message": "Daily login reward claimed successfully",
        "gems_earned": gems_earned,
        "day_claimed": day_of_week,
        "week_start_date": week_start.isoformat(),
    }


def get_free_mode_questions(db, user):
    from utils.trivia_mode_service import get_daily_questions_for_mode

    questions = get_daily_questions_for_mode(db, "free_mode", user)
    return {"questions": questions}


def submit_free_mode_answer(db, user, question_id: int, answer: str):
    from utils.trivia_mode_service import submit_answer_for_mode

    return submit_answer_for_mode(db, "free_mode", user, question_id, answer)
