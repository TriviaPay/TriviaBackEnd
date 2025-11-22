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
from utils.chat_helpers import get_user_chat_profile_data
from utils.onesignal_client import send_push_notification_async, should_send_push, get_user_player_ids, is_user_active
from utils.chat_mute import is_chat_muted
from models import OneSignalPlayer
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
                             avatar_url: Optional[str], frame_url: Optional[str], badge: Optional[dict],
                             message: str, created_at: datetime):
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
                "avatar_url": avatar_url,
                "frame_url": frame_url,
                "badge": badge,
                "message": message,
                "created_at": created_at.isoformat()
            }
        )
    except Exception as e:
        logger.error(f"Failed to publish global chat message to Pusher: {e}")


def send_push_for_global_chat_sync(message_id: int, sender_id: int, sender_username: str, message: str, created_at: datetime):
    """Background task to send push notifications for global chat to all users (except sender)"""
    import asyncio
    from db import get_db
    
    db = next(get_db())
    try:
        # Get all users with OneSignal players (except sender)
        all_players = db.query(OneSignalPlayer).filter(
            OneSignalPlayer.user_id != sender_id,
            OneSignalPlayer.is_valid == True
        ).all()
        
        if not all_players:
            logger.debug("No OneSignal players found for global chat push")
            return
        
        # Batch player IDs separately for active (in-app) and inactive (system) users
        BATCH_SIZE = 2000
        active_player_batches = []  # In-app notifications
        inactive_player_batches = []  # System push notifications
        active_current_batch = []
        inactive_current_batch = []
        
        for player in all_players:
            user_id = player.user_id
            
            # Check if user has muted global chat
            if is_chat_muted(user_id, 'global', db):
                continue
            
            # Check if user is active
            is_active = is_user_active(user_id, db)
            
            if is_active:
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
            "created_at": created_at.isoformat()
        }
        
        # Run async function in event loop
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Send in-app notifications to active users
        for batch in active_player_batches:
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
        logger.info(f"Sent global chat push notifications: {total_active} in-app, {total_inactive} system")
    except Exception as e:
        logger.error(f"Failed to send push notifications for global chat: {e}")
    finally:
        db.close()


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
    
    # Get user profile data (avatar, frame)
    profile_data = get_user_chat_profile_data(current_user, db)
    
    # Publish to Pusher in background
    username = get_display_username(current_user)
    background_tasks.add_task(
        publish_to_pusher_global,
        new_message.id,
        current_user.account_id,
        username,
        profile_data["profile_pic_url"],
        profile_data["avatar_url"],
        profile_data["frame_url"],
        profile_data["badge"],
        new_message.message,
        new_message.created_at
    )
    
    # Send push notifications in background (to all users except sender)
    background_tasks.add_task(
        send_push_for_global_chat_sync,
        new_message.id,
        current_user.account_id,
        username,
        new_message.message,
        new_message.created_at
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
    
    # Get profile data for all message senders
    result_messages = []
    for msg in reversed(messages):
        profile_data = get_user_chat_profile_data(msg.user, db)
        result_messages.append({
            "id": msg.id,
            "user_id": msg.user_id,
            "username": get_display_username(msg.user),
            "profile_pic": profile_data["profile_pic_url"],
            "avatar_url": profile_data["avatar_url"],
            "frame_url": profile_data["frame_url"],
            "badge": profile_data["badge"],
            "message": msg.message,
            "created_at": msg.created_at.isoformat()
        })
    
    return {
        "messages": result_messages,
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

