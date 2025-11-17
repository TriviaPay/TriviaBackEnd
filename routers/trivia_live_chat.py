from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta, date
from typing import Optional
import pytz
import os

from db import get_db
from models import User, TriviaLiveChatMessage, GlobalChatMessage
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
    Check if trivia live chat is active (3 hours before/after draw).
    Returns True if within active window.
    """
    if not TRIVIA_LIVE_CHAT_ENABLED:
        return False
    
    try:
        next_draw_time = get_next_draw_time()  # Returns timezone-aware datetime
        timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        chat_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
        chat_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
        
        return chat_start <= now <= chat_end
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
    
    # Get next draw time and calculate window
    next_draw_time = get_next_draw_time()  # Timezone-aware
    draw_date = next_draw_time.astimezone(pytz.UTC).replace(tzinfo=None).date()
    
    timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
    tz = pytz.timezone(timezone_str)
    now = datetime.now(tz)
    
    window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
    window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
    
    # Convert window to UTC naive for database comparison
    window_start_utc = window_start.astimezone(pytz.UTC).replace(tzinfo=None)
    window_end_utc = window_end.astimezone(pytz.UTC).replace(tzinfo=None)
    
    # Query messages by draw_date and created_at range
    messages = db.query(TriviaLiveChatMessage).filter(
        TriviaLiveChatMessage.draw_date == draw_date,
        TriviaLiveChatMessage.created_at >= window_start_utc,
        TriviaLiveChatMessage.created_at <= window_end_utc
    ).order_by(TriviaLiveChatMessage.created_at.desc()).limit(limit).all()
    
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
        "window_end": window_end.isoformat()
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
    
    if is_active:
        next_draw_time = get_next_draw_time()
        timezone_str = os.getenv("DRAW_TIMEZONE", "US/Eastern")
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        window_start = next_draw_time - timedelta(hours=TRIVIA_LIVE_CHAT_PRE_HOURS)
        window_end = next_draw_time + timedelta(hours=TRIVIA_LIVE_CHAT_POST_HOURS)
        
        return {
            "enabled": True,
            "is_active": True,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "next_draw_time": next_draw_time.isoformat()
        }
    else:
        return {
            "enabled": True,
            "is_active": False,
            "message": "Trivia live chat is not currently active"
        }

