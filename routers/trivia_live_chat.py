from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta, date
from typing import Optional
import pytz
import os

from db import get_db
from models import User, TriviaLiveChatMessage, GlobalChatMessage, TriviaLiveChatViewer
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
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trivia-live-chat", tags=["Trivia Live Chat"])


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=TRIVIA_LIVE_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(None, description="Client-provided ID for idempotency")


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


def publish_to_pusher_trivia_live(message_id: int, user_id: int, username: str, profile_pic: Optional[str],
                                   message: str, created_at: datetime, draw_date: date):
    """Background task to publish to Pusher for trivia live chat"""
    try:
        publish_chat_message_sync(
            "trivia-live-chat",
            "new-message",
            {
                "id": message_id,
                "user_id": user_id,
                "username": username,
                "profile_pic": profile_pic,
                "message": message,
                "created_at": created_at.isoformat(),
                "draw_date": draw_date.isoformat()
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish trivia live chat message to Pusher: {e}")


def publish_to_pusher_global_from_trivia(message_id: int, user_id: int, username: str, profile_pic: Optional[str],
                                         message: str, created_at: datetime):
    """Background task to publish to global chat (from trivia live)"""
    try:
        publish_chat_message_sync(
            "global-chat",
            "new-message",
            {
                "id": message_id,
                "user_id": user_id,
                "username": username,
                "profile_pic": profile_pic,
                "message": message,
                "created_at": created_at.isoformat(),
                "is_from_trivia_live": True
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish trivia message to global chat via Pusher: {e}")


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
                "global_message_id": None,  # Would need to look up
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True
            }
    
    # Burst rate limiting (3 messages per 3 seconds)
    burst_window_ago = datetime.utcnow() - timedelta(seconds=TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS)
    recent_burst = db.query(TriviaLiveChatMessage).filter(
        TriviaLiveChatMessage.user_id == current_user.account_id,
        TriviaLiveChatMessage.created_at >= burst_window_ago
    ).count()
    
    if recent_burst >= TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST:
        raise HTTPException(
            status_code=429,
            detail=f"Burst rate limit exceeded. Maximum {TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_BURST} messages per {TRIVIA_LIVE_CHAT_BURST_WINDOW_SECONDS} seconds."
        )
    
    # Per-minute rate limiting
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
    recent_messages = db.query(TriviaLiveChatMessage).filter(
        TriviaLiveChatMessage.user_id == current_user.account_id,
        TriviaLiveChatMessage.created_at >= one_minute_ago
    ).count()
    
    if recent_messages >= TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {TRIVIA_LIVE_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
        )
    
    # Create trivia live chat message
    new_message = TriviaLiveChatMessage(
        user_id=current_user.account_id,
        message=message_text,
        draw_date=draw_date,
        client_message_id=request.client_message_id
    )
    db.add(new_message)
    
    # Update or create viewer tracking (user is active in trivia live chat)
    existing_viewer = db.query(TriviaLiveChatViewer).filter(
        TriviaLiveChatViewer.user_id == current_user.account_id,
        TriviaLiveChatViewer.draw_date == draw_date
    ).first()
    
    if existing_viewer:
        existing_viewer.last_seen = datetime.utcnow()
    else:
        viewer = TriviaLiveChatViewer(
            user_id=current_user.account_id,
            draw_date=draw_date,
            last_seen=datetime.utcnow()
        )
        db.add(viewer)
    
    # Also add to global chat (no expiry)
    global_message = GlobalChatMessage(
        user_id=current_user.account_id,
        message=message_text,
        is_from_trivia_live=True,
        client_message_id=request.client_message_id  # Same client_message_id for both
    )
    db.add(global_message)
    db.commit()
    db.refresh(new_message)
    db.refresh(global_message)
    
    # Publish to both channels in background
    username = get_display_username(current_user)
    background_tasks.add_task(
        publish_to_pusher_trivia_live,
        new_message.id,
        current_user.account_id,
        username,
        current_user.profile_pic_url,
        new_message.message,
        new_message.created_at,
        draw_date
    )
    
    background_tasks.add_task(
        publish_to_pusher_global_from_trivia,
        global_message.id,
        current_user.account_id,
        username,
        current_user.profile_pic_url,
        global_message.message,
        global_message.created_at
    )
    
    return {
        "message_id": new_message.id,
        "global_message_id": global_message.id,
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
    messages = db.query(TriviaLiveChatMessage).filter(
        TriviaLiveChatMessage.draw_date == draw_date,
        TriviaLiveChatMessage.created_at >= window_start_utc,
        TriviaLiveChatMessage.created_at <= window_end_utc
    ).order_by(TriviaLiveChatMessage.created_at.desc()).limit(limit).all()
    
    # Update viewer tracking (user is viewing trivia live chat)
    existing_viewer = db.query(TriviaLiveChatViewer).filter(
        TriviaLiveChatViewer.user_id == current_user.account_id,
        TriviaLiveChatViewer.draw_date == draw_date
    ).first()
    
    if existing_viewer:
        existing_viewer.last_seen = datetime.utcnow()
    else:
        viewer = TriviaLiveChatViewer(
            user_id=current_user.account_id,
            draw_date=draw_date,
            last_seen=datetime.utcnow()
        )
        db.add(viewer)
    db.commit()
    
    # Get active viewer count (users active within last 5 minutes)
    cutoff_time = datetime.utcnow() - timedelta(minutes=5)
    active_viewers = db.query(TriviaLiveChatViewer).filter(
        TriviaLiveChatViewer.draw_date == draw_date,
        TriviaLiveChatViewer.last_seen >= cutoff_time
    ).count()
    
    return {
        "messages": [
            {
                "id": msg.id,
                "user_id": msg.user_id,
                "username": get_display_username(msg.user),
                "profile_pic": msg.user.profile_pic_url,
                "message": msg.message,
                "created_at": msg.created_at.isoformat()
            }
            for msg in reversed(messages)
        ],
        "is_active": True,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "viewer_count": active_viewers
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
        
        return {
            "enabled": True,
            "is_active": True,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "next_draw_time": next_draw_time.isoformat(),
            "viewer_count": active_viewers,
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

