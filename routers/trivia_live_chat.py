from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, exc
from datetime import datetime, timedelta, date
from typing import Optional, Union
import pytz
import os

from db import get_db
from models import User, TriviaLiveChatMessage, TriviaLiveChatViewer, TriviaLiveChatLike
from routers.dependencies import get_current_user
from config import (
    TRIVIA_LIVE_CHAT_ENABLED,
    TRIVIA_LIVE_CHAT_PRE_HOURS,
    TRIVIA_LIVE_CHAT_POST_HOURS,
    TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE,
    TRIVIA_LIVE_CHAT_MAX_MESSAGE_LENGTH,
    TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST,
    TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS
)
from utils.draw_calculations import get_next_draw_time
from utils.pusher_client import publish_chat_message_sync
from utils.message_sanitizer import sanitize_message
from utils.chat_helpers import get_user_chat_profile_data, get_user_chat_profile_data_bulk
from utils.onesignal_client import send_push_notification_async, ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS
from utils.chat_mute import get_muted_user_ids
from utils.chat_redis import check_burst_limit, check_rate_limit, enqueue_chat_event
from models import OneSignalPlayer
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trivia-live-chat", tags=["Trivia Live Chat"])


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=TRIVIA_LIVE_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(None, description="Client-provided ID for idempotency")
    reply_to_message_id: Optional[int] = Field(None, description="ID of message being replied to")


def get_display_username(user: User) -> str:
    """Get display username with fallback logic"""
    if user.username and user.username.strip():
        return user.username
    if user.email:
        return user.email.split('@')[0]
    return f"User{user.account_id}"


def is_trivia_live_chat_active() -> bool:
    """
    Check if trivia live chat is active (X hours before/after draw).
    Returns True if within active window of either previous or next draw.
    """
    if not TRIVIA_LIVE_CHAT_ENABLED:
        return False
    
    try:
        next_draw_time = get_next_draw_time()  # Returns timezone-aware datetime
        timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        # Check next draw window
        next_chat_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
        next_chat_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
        
        # Check previous draw window (in case we're in post-window)
        prev_draw_time = next_draw_time - timedelta(days=1)
        prev_chat_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
        prev_chat_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
        
        # Check if we're in either window
        in_next_window = next_chat_start <= now <= next_chat_end
        in_prev_window = prev_chat_start <= now <= prev_chat_end
        
        logger.debug(f"Trivia live chat - Next window: {next_chat_start} to {next_chat_end}")
        logger.debug(f"Trivia live chat - Prev window: {prev_chat_start} to {prev_chat_end}")
        logger.debug(f"Trivia live chat - Current time: {now}")
        logger.debug(f"Trivia live chat - In next window: {in_next_window}, In prev window: {in_prev_window}")
        
        return in_next_window or in_prev_window
    except Exception as e:
        logger.error(f"Error checking trivia live chat window: {e}")
        return False


def _ensure_datetime(value: Union[datetime, str]) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return datetime.utcnow()


def _ensure_date(value: Union[date, str]) -> date:
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return datetime.utcnow().date()


def publish_to_pusher_trivia_live(message_id: int, user_id: int, username: str, profile_pic: Optional[str],
                                   avatar_url: Optional[str], frame_url: Optional[str], badge: Optional[dict],
                                   message: str, created_at: Union[datetime, str], draw_date: Union[date, str],
                                   reply_to: Optional[dict] = None):
    """Background task to publish to Pusher for trivia live chat"""
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
            "draw_date": draw_date_val.isoformat()
        }
        if reply_to:
            event_data["reply_to"] = reply_to
        publish_chat_message_sync("trivia-live-chat", "new-message", event_data)
    except Exception as e:
        logger.error(f"Failed to publish trivia live chat message to Pusher: {e}")


def send_push_for_trivia_live_chat_sync(message_id: int, sender_id: int, sender_username: str, message: str,
                                        draw_date: Union[date, str], created_at: Union[datetime, str]):
    """Background task to send push notifications for trivia live chat to all users (except sender)"""
    import asyncio
    from db import get_db
    from utils.notification_storage import create_notifications_batch
    
    db = next(get_db())
    try:
        created_at_dt = _ensure_datetime(created_at)
        draw_date_val = _ensure_date(draw_date)
        # Get all users with OneSignal players (except sender)
        all_players = db.query(OneSignalPlayer).filter(
            OneSignalPlayer.user_id != sender_id,
            OneSignalPlayer.is_valid == True
        ).all()
        
        if not all_players:
            logger.debug("No OneSignal players found for trivia live chat push")
            return
        
        # Precompute muted users and active users to avoid per-user queries
        player_user_ids = {player.user_id for player in all_players}
        muted_user_ids = get_muted_user_ids(list(player_user_ids), 'trivia_live', db)
        threshold_time = datetime.utcnow() - timedelta(seconds=ONESIGNAL_ACTIVITY_THRESHOLD_SECONDS)
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
            
            # Check if user has muted trivia live chat
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
        heading = "Trivia Live Chat"
        content = f"{sender_username}: {message[:100]}"  # Truncate for notification
        data = {
            "type": "trivia_live_chat",
            "message_id": message_id,
            "sender_id": sender_id,
            "sender_username": sender_username,
            "message": message,
            "draw_date": draw_date_val.isoformat(),
            "created_at": created_at_dt.isoformat()
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
                    is_in_app_notification=True
                )
            )
        
        # Send system push notifications to inactive users
        for batch in inactive_player_batches:
            logger.debug(f"Sending system push notification batch: {len(batch)} players")
            loop.run_until_complete(
                send_push_notification_async(
                    player_ids=batch,
                    heading=heading,
                    content=content,
                    data=data,
                    is_in_app_notification=False
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
                notification_type="chat_trivia_live",
                data=data
            )
        
        logger.info(
            f"Sent trivia live chat push notifications | in-app={total_active} | system={total_inactive} | "
            f"sender_id={sender_id} | message_id={message_id}"
        )
    except Exception as e:
        logger.error(f"Failed to send push notifications for trivia live chat: {e}")
    finally:
        db.close()


@router.post("/send")
async def send_trivia_live_message(
    request: SendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send message to trivia live chat"""
    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Trivia live chat is disabled")
    
    if not is_trivia_live_chat_active():
        raise HTTPException(status_code=403, detail="Trivia live chat is not active")
    
    # Sanitize message to prevent XSS
    message_text = sanitize_message(request.message)
    if not message_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # Get next draw time and convert to date for storage
    next_draw_time = get_next_draw_time()  # Timezone-aware
    # Convert to UTC naive for storage (consistent with existing pattern)
    draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    
    # Check for duplicate message (idempotency)
    if request.client_message_id:
        existing_message = db.query(TriviaLiveChatMessage).filter(
            TriviaLiveChatMessage.user_id == current_user.account_id,
            TriviaLiveChatMessage.draw_date == draw_date,
            TriviaLiveChatMessage.client_message_id == request.client_message_id
        ).first()
        
        if existing_message:
            logger.debug(f"Duplicate trivia live chat message detected: {request.client_message_id}")
            return {
                "message_id": existing_message.id,
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True
            }
    
    # Burst rate limiting (Redis first, fallback to DB)
    burst_allowed = await check_burst_limit(
        "trivia",
        f"{current_user.account_id}:{draw_date.isoformat()}",
        TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST,
        TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS
    )
    if burst_allowed is False:
        raise HTTPException(
            status_code=429,
            detail=f"Burst rate limit exceeded. Maximum {TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST} messages per {TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS} seconds."
        )
    if burst_allowed is None:
        burst_window_ago = datetime.utcnow() - timedelta(seconds=TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS)
        recent_burst = db.query(TriviaLiveChatMessage.id).filter(
            TriviaLiveChatMessage.user_id == current_user.account_id,
            TriviaLiveChatMessage.created_at >= burst_window_ago
        ).order_by(TriviaLiveChatMessage.created_at.desc()).limit(
            TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST
        ).all()
        
        if len(recent_burst) >= TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST:
            raise HTTPException(
                status_code=429,
                detail=f"Burst rate limit exceeded. Maximum {TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST} messages per {TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS} seconds."
            )
    
    # Per-minute rate limiting
    minute_allowed = await check_rate_limit(
        "trivia",
        f"{current_user.account_id}:{draw_date.isoformat()}",
        TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE,
        60
    )
    if minute_allowed is False:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
        )
    if minute_allowed is None:
        one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
        recent_messages = db.query(TriviaLiveChatMessage.id).filter(
            TriviaLiveChatMessage.user_id == current_user.account_id,
            TriviaLiveChatMessage.created_at >= one_minute_ago,
            TriviaLiveChatMessage.draw_date == draw_date
        ).order_by(TriviaLiveChatMessage.created_at.desc()).limit(
            TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE
        ).all()
        
        if len(recent_messages) >= TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Maximum {TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
            )
    
    # Validate reply_to_message_id if provided
    reply_to_message = None
    if request.reply_to_message_id:
        reply_to_message = db.query(TriviaLiveChatMessage).filter(
            TriviaLiveChatMessage.id == request.reply_to_message_id,
            TriviaLiveChatMessage.draw_date == draw_date
        ).first()
        if not reply_to_message:
            raise HTTPException(
                status_code=404,
                detail=f"Message {request.reply_to_message_id} not found for this draw date"
            )
    
    # Create trivia live chat message
    new_message = TriviaLiveChatMessage(
        user_id=current_user.account_id,
        message=message_text,
        draw_date=draw_date,
        client_message_id=request.client_message_id,
        reply_to_message_id=request.reply_to_message_id  # Use directly since validation ensures it exists if provided
    )
    db.add(new_message)
    
    # Update or create viewer tracking (user is active in trivia live chat)
    # Use upsert pattern to handle race conditions
    try:
        existing_viewer = db.query(TriviaLiveChatViewer).filter(
            and_(
                TriviaLiveChatViewer.user_id == current_user.account_id,
                TriviaLiveChatViewer.draw_date == draw_date
            )
        ).first()
        
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()
        else:
            # Insert new viewer - handle race condition with try/except
            viewer = TriviaLiveChatViewer(
                user_id=current_user.account_id,
                draw_date=draw_date,
                last_seen=datetime.utcnow()
            )
            db.add(viewer)
            db.flush()  # Flush to trigger the insert and catch any IntegrityError
    except exc.IntegrityError as e:
        # Race condition: another request inserted the record between our check and insert
        # Rollback and update the existing record
        db.rollback()
        existing_viewer = db.query(TriviaLiveChatViewer).filter(
            and_(
                TriviaLiveChatViewer.user_id == current_user.account_id,
                TriviaLiveChatViewer.draw_date == draw_date
            )
        ).first()
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()
    
    db.commit()
    db.refresh(new_message)
    
    # Get user profile data (avatar, frame)
    profile_data = get_user_chat_profile_data(current_user, db)
    
    # Get reply information if this is a reply
    reply_info = None
    if reply_to_message:
        replied_sender_profile = get_user_chat_profile_data(reply_to_message.user, db)
        reply_info = {
            "message_id": reply_to_message.id,
            "sender_id": reply_to_message.user_id,
            "sender_username": get_display_username(reply_to_message.user),
            "message": reply_to_message.message,
            "sender_profile_pic": replied_sender_profile["profile_pic_url"],
            "sender_avatar_url": replied_sender_profile["avatar_url"],
            "sender_frame_url": replied_sender_profile["frame_url"],
            "sender_badge": replied_sender_profile["badge"],
            "created_at": reply_to_message.created_at.isoformat()
        }
    
    # Publish to trivia live chat channel via Redis queue (fallback to inline background tasks)
    username = get_display_username(current_user)
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
                "reply_to": reply_info
            },
            "push_args": {
                "message_id": new_message.id,
                "sender_id": current_user.account_id,
                "sender_username": username,
                "message": new_message.message,
                "draw_date": draw_date.isoformat(),
                "created_at": new_message.created_at.isoformat()
            }
        }
    )
    
    if not event_enqueued:
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
            reply_info
        )
        background_tasks.add_task(
            send_push_for_trivia_live_chat_sync,
            new_message.id,
            current_user.account_id,
            username,
            new_message.message,
            draw_date,
            new_message.created_at
        )
    
    return {
        "message_id": new_message.id,
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False
    }


@router.get("/messages")
async def get_trivia_live_messages(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get trivia live chat messages (only shows messages within active window)"""
    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Trivia live chat is disabled")
    
    if not is_trivia_live_chat_active():
        return {
            "messages": [],
            "is_active": False,
            "message": "Trivia live chat is not currently active"
        }
    
    # Get next draw time and calculate windows
    next_draw_time = get_next_draw_time()  # Timezone-aware
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    # Calculate both windows
    next_window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    next_window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    # Determine which window we're in
    in_next_window = next_window_start <= now <= next_window_end
    in_prev_window = prev_window_start <= now <= prev_window_end
    
    # Use the appropriate draw date and window
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
        # Not in any window, return empty
        return {
            "messages": [],
            "is_active": False,
            "message": "Trivia live chat is not currently active"
        }
    
    # Query messages by draw_date and created_at range
    messages = db.query(TriviaLiveChatMessage).options(
        joinedload(TriviaLiveChatMessage.user)
    ).filter(
        TriviaLiveChatMessage.draw_date == draw_date,
        TriviaLiveChatMessage.created_at >= window_start_utc,
        TriviaLiveChatMessage.created_at <= window_end_utc
    ).order_by(TriviaLiveChatMessage.created_at.desc()).limit(limit).all()
    
    # Update viewer tracking (user is viewing trivia live chat)
    # Use upsert pattern to handle race conditions
    try:
        # Try to update existing viewer first
        existing_viewer = db.query(TriviaLiveChatViewer).filter(
            and_(
                TriviaLiveChatViewer.user_id == current_user.account_id,
                TriviaLiveChatViewer.draw_date == draw_date
            )
        ).first()
        
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()
        else:
            # Insert new viewer - handle race condition with try/except
            viewer = TriviaLiveChatViewer(
                user_id=current_user.account_id,
                draw_date=draw_date,
                last_seen=datetime.utcnow()
            )
            db.add(viewer)
            db.flush()  # Flush to trigger the insert and catch any IntegrityError
    except exc.IntegrityError as e:
        # Race condition: another request inserted the record between our check and insert
        # Rollback and update the existing record
        db.rollback()
        existing_viewer = db.query(TriviaLiveChatViewer).filter(
            and_(
                TriviaLiveChatViewer.user_id == current_user.account_id,
                TriviaLiveChatViewer.draw_date == draw_date
            )
        ).first()
        if existing_viewer:
            existing_viewer.last_seen = datetime.utcnow()
    
    db.commit()
    
    # Get active viewer count (users active within last 5 minutes)
    cutoff_time = datetime.utcnow() - timedelta(minutes=5)
    active_viewers = db.query(TriviaLiveChatViewer).filter(
        TriviaLiveChatViewer.draw_date == draw_date,
        TriviaLiveChatViewer.last_seen >= cutoff_time
    ).count()
    
    # Get total likes for this draw
    total_likes = db.query(TriviaLiveChatLike).filter(
        and_(
            TriviaLiveChatLike.draw_date == draw_date,
            TriviaLiveChatLike.message_id.is_(None)  # Only session-level likes
        )
    ).count()
    
    # Batch load reply messages and user profile data
    reply_message_ids = {msg.reply_to_message_id for msg in messages if msg.reply_to_message_id}
    replied_messages = {}
    if reply_message_ids:
        replied_msgs = db.query(TriviaLiveChatMessage).options(
            joinedload(TriviaLiveChatMessage.user)
        ).filter(
            TriviaLiveChatMessage.id.in_(list(reply_message_ids))
        ).all()
        replied_messages = {msg.id: msg for msg in replied_msgs}

    users_by_id = {msg.user_id: msg.user for msg in messages if msg.user}
    for replied_msg in replied_messages.values():
        if replied_msg.user:
            users_by_id.setdefault(replied_msg.user_id, replied_msg.user)

    profile_cache = get_user_chat_profile_data_bulk(list(users_by_id.values()), db)

    # Get profile data for all message senders
    result_messages = []
    for msg in reversed(messages):
        profile_data = profile_cache.get(msg.user_id, {
            "profile_pic_url": None,
            "avatar_url": None,
            "frame_url": None,
            "badge": None,
            "subscription_badges": [],
            "level": 1,
            "level_progress": "0/100"
        })
        
        # Get reply information if this message is a reply
        reply_info = None
        if msg.reply_to_message_id:
            replied_msg = replied_messages.get(msg.reply_to_message_id)
            if replied_msg:
                replied_sender_profile = profile_cache.get(replied_msg.user_id, {
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
                    "sender_id": replied_msg.user_id,
                    "sender_username": get_display_username(replied_msg.user),
                    "message": replied_msg.message,
                    "sender_profile_pic": replied_sender_profile["profile_pic_url"],
                    "sender_avatar_url": replied_sender_profile["avatar_url"],
                    "sender_frame_url": replied_sender_profile["frame_url"],
                    "sender_badge": replied_sender_profile["badge"],
                    "created_at": replied_msg.created_at.isoformat(),
                    "sender_level": replied_sender_profile.get("level", 1),
                    "sender_level_progress": replied_sender_profile.get("level_progress", "0/100")
                }
        
        result_messages.append({
            "id": msg.id,
            "user_id": msg.user_id,
            "username": get_display_username(msg.user),
            "profile_pic": profile_data["profile_pic_url"],
            "avatar_url": profile_data["avatar_url"],
            "frame_url": profile_data["frame_url"],
            "badge": profile_data["badge"],
            "message": msg.message,
            "created_at": msg.created_at.isoformat(),
            "reply_to": reply_info,
            "level": profile_data.get("level", 1),
            "level_progress": profile_data.get("level_progress", "0/100")
        })
    
    return {
        "messages": result_messages,
        "is_active": True,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "viewer_count": active_viewers,
        "like_count": total_likes
    }


@router.get("/debug-config")
async def debug_trivia_live_chat_config(
    current_user: User = Depends(get_current_user)
):
    """Debug endpoint to check configuration values"""
    return {
        "TRIVIA_LIVE_CHAT_PRE_HOURS_env": os.getenv("TRIVIA_LIVE_CHAT_PRE_HOURS", "NOT SET"),
        "TRIVIA_LIVE_CHAT_POST_HOURS_env": os.getenv("TRIVIA_LIVE_CHAT_POST_HOURS", "NOT SET"),
        "loaded_pre_hours": TRIVIA_LIVE_CHAT_PRE_HOURS,
        "loaded_post_hours": TRIVIA_LIVE_CHAT_POST_HOURS,
        "DRAW_TIMEZONE": os.getenv("DRAW_TIMEZONE", "NOT SET"),
        "DRAW_TIME_HOUR": os.getenv("DRAW_TIME_HOUR", "NOT SET"),
        "DRAW_TIME_MINUTE": os.getenv("DRAW_TIME_MINUTE", "NOT SET"),
        "TRIVIA_LIVE_CHAT_ENABLED": TRIVIA_LIVE_CHAT_ENABLED
    }


@router.get("/status")
async def get_trivia_live_chat_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Check if trivia live chat is currently active"""
    if not TRIVIA_LIVE_CHAT_ENABLED:
        return {
            "enabled": False,
            "is_active": False,
            "message": "Trivia live chat is disabled"
        }
    
    is_active = is_trivia_live_chat_active()
    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    # Calculate both windows for display
    next_window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    next_window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    if is_active:
        # Determine which window we're in
        in_next_window = next_window_start <= now <= next_window_end
        in_prev_window = prev_window_start <= now <= prev_window_end
        
        # Use the appropriate draw date for viewer count
        if in_next_window:
            draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
            window_start = next_window_start
            window_end = next_window_end
        else:
            draw_date = prev_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
            window_start = prev_window_start
            window_end = prev_window_end
        
        # Get active viewer count (users active within last 5 minutes)
        cutoff_time = datetime.utcnow() - timedelta(minutes=5)
        active_viewers = db.query(TriviaLiveChatViewer).filter(
            TriviaLiveChatViewer.draw_date == draw_date,
            TriviaLiveChatViewer.last_seen >= cutoff_time
        ).count()
        
        # Get total likes for this draw
        total_likes = db.query(TriviaLiveChatLike).filter(
            and_(
                TriviaLiveChatLike.draw_date == draw_date,
                TriviaLiveChatLike.message_id.is_(None)  # Only session-level likes
            )
        ).count()
        
        # Check if current user has liked
        user_liked = db.query(TriviaLiveChatLike).filter(
            and_(
                TriviaLiveChatLike.user_id == current_user.account_id,
                TriviaLiveChatLike.draw_date == draw_date,
                TriviaLiveChatLike.message_id.is_(None)
            )
        ).first() is not None
        
        return {
            "enabled": True,
            "is_active": True,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "next_draw_time": next_draw_time.isoformat(),
            "viewer_count": active_viewers,
            "like_count": total_likes,
            "user_liked": user_liked,
            "current_time": now.isoformat(),
            "pre_hours": TRIVIA_LIVE_CHAT_PRE_HOURS,
            "post_hours": TRIVIA_LIVE_CHAT_POST_HOURS
        }
    else:
        return {
            "enabled": True,
            "is_active": False,
            "message": "Trivia live chat is not currently active",
            "next_window_start": next_window_start.isoformat(),
            "next_window_end": next_window_end.isoformat(),
            "prev_window_start": prev_window_start.isoformat(),
            "prev_window_end": prev_window_end.isoformat(),
            "current_time": now.isoformat(),
            "next_draw_time": next_draw_time.isoformat(),
            "pre_hours": TRIVIA_LIVE_CHAT_PRE_HOURS,
            "post_hours": TRIVIA_LIVE_CHAT_POST_HOURS
        }


@router.post("/like")
async def like_trivia_live_chat(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Like the trivia live chat session. Idempotent: if already liked, returns current count."""
    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Trivia live chat is disabled")
    
    if not is_trivia_live_chat_active():
        raise HTTPException(status_code=403, detail="Trivia live chat is not currently active")
    
    # Get the current draw date
    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    # Determine which draw date we're in
    next_window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    next_window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    if prev_window_start <= now <= prev_window_end:
        draw_date = prev_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    else:
        draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    
    # Check if user already liked this draw
    existing_like = db.query(TriviaLiveChatLike).filter(
        and_(
            TriviaLiveChatLike.user_id == current_user.account_id,
            TriviaLiveChatLike.draw_date == draw_date,
            TriviaLiveChatLike.message_id.is_(None)  # Session-level like
        )
    ).first()
    
    if existing_like:
        # Already liked - return current count
        total_likes = db.query(TriviaLiveChatLike).filter(
            and_(
                TriviaLiveChatLike.draw_date == draw_date,
                TriviaLiveChatLike.message_id.is_(None)
            )
        ).count()
        
        return {
            "message": "Already liked",
            "total_likes": total_likes,
            "already_liked": True,
            "draw_date": draw_date.isoformat()
        }
    
    # Add like
    new_like = TriviaLiveChatLike(
        user_id=current_user.account_id,
        draw_date=draw_date,
        message_id=None  # Session-level like
    )
    
    db.add(new_like)
    db.commit()
    db.refresh(new_like)
    
    # Get total likes
    total_likes = db.query(TriviaLiveChatLike).filter(
        and_(
            TriviaLiveChatLike.draw_date == draw_date,
            TriviaLiveChatLike.message_id.is_(None)
        )
    ).count()
    
    # Publish like update via Pusher
    try:
        publish_chat_message_sync(
            "trivia-live-chat",
            "like-update",
            {
                "draw_date": draw_date.isoformat(),
                "total_likes": total_likes,
                "user_id": current_user.account_id
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish like update to Pusher: {e}")
    
    return {
        "message": "Trivia live chat liked successfully",
        "total_likes": total_likes,
        "already_liked": False,
        "draw_date": draw_date.isoformat()
    }


@router.get("/likes")
async def get_trivia_live_chat_likes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current like count for the active trivia live chat session"""
    if not TRIVIA_LIVE_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Trivia live chat is disabled")
    
    if not is_trivia_live_chat_active():
        raise HTTPException(status_code=403, detail="Trivia live chat is not currently active")
    
    # Get the current draw date
    next_draw_time = get_next_draw_time()
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    # Determine which draw date we're in
    next_window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    next_window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    prev_draw_time = next_draw_time - timedelta(days=1)
    prev_window_start = prev_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    prev_window_end = prev_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    if prev_window_start <= now <= prev_window_end:
        draw_date = prev_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    else:
        draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    
    # Get total likes for this draw
    total_likes = db.query(TriviaLiveChatLike).filter(
        and_(
            TriviaLiveChatLike.draw_date == draw_date,
            TriviaLiveChatLike.message_id.is_(None)  # Only session-level likes
        )
    ).count()
    
    # Check if current user has liked
    user_liked = db.query(TriviaLiveChatLike).filter(
        and_(
            TriviaLiveChatLike.user_id == current_user.account_id,
            TriviaLiveChatLike.draw_date == draw_date,
            TriviaLiveChatLike.message_id.is_(None)
        )
    ).first() is not None
    
    return {
        "total_likes": total_likes,
        "draw_date": draw_date.isoformat(),
        "user_liked": user_liked
    }
