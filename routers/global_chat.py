from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import and_
from datetime import datetime, timedelta
from typing import Optional

from db import get_db
from models import User, GlobalChatMessage, GlobalChatViewer
from routers.dependencies import get_current_user
from config import (
    GLOBAL_CHAT_ENABLED,
    GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE,
    GLOBAL_CHAT_MAX_MESSAGE_LENGTH,
    GLOBAL_CHAT_RETENTION_DAYS,
    GLOBAL_CHAT_MAX_MESSAGES_PER_BURST,
    GLOBAL_CHAT_BURST_WINDOW_SECONDS
)
from utils.pusher_client import publish_chat_message_sync
from utils.message_sanitizer import sanitize_message
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/global-chat", tags=["Global Chat"])


class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=GLOBAL_CHAT_MAX_MESSAGE_LENGTH)
    client_message_id: Optional[str] = Field(None, description="Client-provided ID for idempotency")


def get_display_username(user: User) -> str:
    """Get display username with fallback logic"""
    if user.username and user.username.strip():
        return user.username
    if user.email:
        return user.email.split('@')[0]
    return f"User{user.account_id}"


def publish_to_pusher_global(message_id: int, user_id: int, username: str, profile_pic: Optional[str], 
                             message: str, created_at: datetime, is_from_trivia_live: bool):
    """Background task to publish to Pusher"""
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
                "is_from_trivia_live": is_from_trivia_live
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish global chat message to Pusher: {e}")


@router.post("/send")
async def send_global_message(
    request: SendMessageRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send message to global chat"""
    if not GLOBAL_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Global chat is disabled")
    
    # Sanitize message to prevent XSS
    message_text = sanitize_message(request.message)
    if not message_text:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # Check for duplicate message (idempotency)
    if request.client_message_id:
        existing_message = db.query(GlobalChatMessage).filter(
            GlobalChatMessage.user_id == current_user.account_id,
            GlobalChatMessage.client_message_id == request.client_message_id
        ).first()
        
        if existing_message:
            logger.debug(f"Duplicate global chat message detected: {request.client_message_id}")
            return {
                "message_id": existing_message.id,
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True
            }
    
    # Burst rate limiting (3 messages per 3 seconds)
    burst_window_ago = datetime.utcnow() - timedelta(seconds=GLOBAL_CHAT_BURST_WINDOW_SECONDS)
    recent_burst = db.query(GlobalChatMessage).filter(
        GlobalChatMessage.user_id == current_user.account_id,
        GlobalChatMessage.created_at >= burst_window_ago
    ).count()
    
    if recent_burst >= GLOBAL_CHAT_MAX_MESSAGES_PER_BURST:
        raise HTTPException(
            status_code=429,
            detail=f"Burst rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_BURST} messages per {GLOBAL_CHAT_BURST_WINDOW_SECONDS} seconds."
        )
    
    # Per-minute rate limiting
    one_minute_ago = datetime.utcnow() - timedelta(minutes=1)
    recent_messages = db.query(GlobalChatMessage).filter(
        GlobalChatMessage.user_id == current_user.account_id,
        GlobalChatMessage.created_at >= one_minute_ago
    ).count()
    
    if recent_messages >= GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum {GLOBAL_CHAT_MAX_MESSAGES_PER_MINUTE} messages per minute."
        )
    
    # Create message
    new_message = GlobalChatMessage(
        user_id=current_user.account_id,
        message=message_text,
        is_from_trivia_live=False,
        client_message_id=request.client_message_id
    )
    
    db.add(new_message)
    
    # Update or create viewer tracking (user is active in global chat)
    existing_viewer = db.query(GlobalChatViewer).filter(
        GlobalChatViewer.user_id == current_user.account_id
    ).first()
    
    if existing_viewer:
        existing_viewer.last_seen = datetime.utcnow()
    else:
        viewer = GlobalChatViewer(
            user_id=current_user.account_id,
            last_seen=datetime.utcnow()
        )
        db.add(viewer)
    
    db.commit()
    db.refresh(new_message)
    
    # Publish to Pusher in background
    username = get_display_username(current_user)
    background_tasks.add_task(
        publish_to_pusher_global,
        new_message.id,
        current_user.account_id,
        username,
        current_user.profile_pic_url,
        new_message.message,
        new_message.created_at,
        False
    )
    
    return {
        "message_id": new_message.id,
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False
    }


@router.get("/messages")
async def get_global_messages(
    limit: int = Query(50, ge=1, le=100),
    before: Optional[int] = Query(None, description="Message ID to fetch messages before"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get global chat messages with pagination"""
    if not GLOBAL_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Global chat is disabled")
    
    query = db.query(GlobalChatMessage).order_by(GlobalChatMessage.created_at.desc())
    
    if before:
        before_msg = db.query(GlobalChatMessage).filter(GlobalChatMessage.id == before).first()
        if before_msg:
            query = query.filter(GlobalChatMessage.created_at < before_msg.created_at)
    
    messages = query.limit(limit).all()
    
    # Update viewer tracking (user is viewing global chat)
    existing_viewer = db.query(GlobalChatViewer).filter(
        GlobalChatViewer.user_id == current_user.account_id
    ).first()
    
    if existing_viewer:
        existing_viewer.last_seen = datetime.utcnow()
    else:
        viewer = GlobalChatViewer(
            user_id=current_user.account_id,
            last_seen=datetime.utcnow()
        )
        db.add(viewer)
    db.commit()
    
    # Get active online count (users active within last 5 minutes)
    cutoff_time = datetime.utcnow() - timedelta(minutes=5)
    online_count = db.query(GlobalChatViewer).filter(
        GlobalChatViewer.last_seen >= cutoff_time
    ).count()
    
    return {
        "messages": [
            {
                "id": msg.id,
                "user_id": msg.user_id,
                "username": get_display_username(msg.user),
                "profile_pic": msg.user.profile_pic_url,
                "message": msg.message,
                "created_at": msg.created_at.isoformat(),
                "is_from_trivia_live": msg.is_from_trivia_live
            }
            for msg in reversed(messages)
        ],
        "online_count": online_count
    }


@router.post("/cleanup")
async def cleanup_old_messages(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Cleanup old messages based on retention policy (admin only)"""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    if not GLOBAL_CHAT_ENABLED:
        raise HTTPException(status_code=403, detail="Global chat is disabled")
    
    cutoff_date = datetime.utcnow() - timedelta(days=GLOBAL_CHAT_RETENTION_DAYS)
    
    deleted_count = db.query(GlobalChatMessage).filter(
        GlobalChatMessage.created_at < cutoff_date
    ).delete()
    
    db.commit()
    
    logger.info(f"Cleaned up {deleted_count} old global chat messages (older than {GLOBAL_CHAT_RETENTION_DAYS} days)")
    
    return {
        "deleted_count": deleted_count,
        "cutoff_date": cutoff_date.isoformat()
    }

