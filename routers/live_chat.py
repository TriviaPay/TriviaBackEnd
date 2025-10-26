from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Body
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Dict, Any
import json
import os
import pytz
from collections import defaultdict
import asyncio
import logging

from db import get_db
from models import User, LiveChatSession, LiveChatMessage, LiveChatLike, LiveChatViewer
from routers.dependencies import get_current_user
from utils.draw_calculations import get_next_draw_time
from config import (
    LIVE_CHAT_ENABLED, 
    LIVE_CHAT_PRE_DRAW_HOURS, 
    LIVE_CHAT_POST_DRAW_HOURS,
    LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE,
    LIVE_CHAT_MESSAGE_HISTORY_LIMIT
)

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-chat", tags=["Live Chat"])

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = defaultdict(list)
        self.user_sessions: Dict[WebSocket, int] = {}  # WebSocket -> user_id mapping
        
    async def connect(self, websocket: WebSocket, user_id: int, session_id: int):
        await websocket.accept()
        self.active_connections[session_id].append(websocket)
        self.user_sessions[websocket] = user_id
        logger.info(f"User {user_id} connected to session {session_id}")
        
    def disconnect(self, websocket: WebSocket, session_id: int):
        if websocket in self.active_connections[session_id]:
            self.active_connections[session_id].remove(websocket)
        if websocket in self.user_sessions:
            user_id = self.user_sessions[websocket]
            del self.user_sessions[websocket]
            logger.info(f"User {user_id} disconnected from session {session_id}")
            
    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"Failed to send personal message: {e}")
        
    async def broadcast_to_session(self, message: str, session_id: int, exclude_user: int = None):
        disconnected_websockets = []
        for websocket in self.active_connections[session_id]:
            if exclude_user and self.user_sessions.get(websocket) == exclude_user:
                continue
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.error(f"Failed to broadcast to websocket: {e}")
                disconnected_websockets.append(websocket)
        
        # Remove broken connections
        for websocket in disconnected_websockets:
            self.disconnect(websocket, session_id)

manager = ConnectionManager()

def get_display_username(user: User) -> str:
    """
    Get display username with fallback logic.
    Priority: username -> email prefix -> User{account_id}
    """
    if user.username and user.username.strip():
        return user.username
    
    if user.email:
        return user.email.split('@')[0]
    
    return f"User{user.account_id}"

def is_chat_window_active() -> bool:
    """Check if we're within the chat window (before/after draw)"""
    if not LIVE_CHAT_ENABLED:
        return False
        
    try:
        next_draw_time = get_next_draw_time()
        now = datetime.now(pytz.timezone(os.getenv("DRAW_TIMEZONE", "US/Eastern")))
        
        # Calculate next draw's chat window
        next_chat_start = next_draw_time - timedelta(hours=LIVE_CHAT_PRE_DRAW_HOURS)
        next_chat_end = next_draw_time + timedelta(hours=LIVE_CHAT_POST_DRAW_HOURS)
        
        # Calculate previous draw's chat window
        prev_draw_time = next_draw_time - timedelta(days=1)
        prev_chat_start = prev_draw_time - timedelta(hours=LIVE_CHAT_PRE_DRAW_HOURS)
        prev_chat_end = prev_draw_time + timedelta(hours=LIVE_CHAT_POST_DRAW_HOURS)
        
        # Check if we're in either chat window
        in_next_window = next_chat_start <= now <= next_chat_end
        in_prev_window = prev_chat_start <= now <= prev_chat_end
        
        logger.debug(f"Next chat window: {next_chat_start} to {next_chat_end}")
        logger.debug(f"Prev chat window: {prev_chat_start} to {prev_chat_end}")
        logger.debug(f"Current time: {now}")
        logger.debug(f"In next window: {in_next_window}, In prev window: {in_prev_window}")
        
        return in_next_window or in_prev_window
    except Exception as e:
        logger.error(f"Error checking chat window: {e}")
        return False

# Session cache to prevent multiple session creation
_active_session_cache = None
_cache_timestamp = None
_cache_timeout = timedelta(minutes=1)

def get_or_create_active_session(db: Session) -> LiveChatSession:
    """Get or create the active chat session for the current draw window"""
    global _active_session_cache, _cache_timestamp
    
    # Use cached session if it's less than 1 minute old and still valid
    if (_active_session_cache and _cache_timestamp and 
        datetime.utcnow() - _cache_timestamp < _cache_timeout):
        # Verify cached session is still within its time window
        now = datetime.utcnow()
        if (_active_session_cache.start_time <= now <= _active_session_cache.end_time and
            _active_session_cache.is_active):
            return _active_session_cache
    
    # Get next draw time to determine the session window
    next_draw_time = get_next_draw_time()
    chat_start = next_draw_time - timedelta(hours=LIVE_CHAT_PRE_DRAW_HOURS)
    chat_end = next_draw_time + timedelta(hours=LIVE_CHAT_POST_DRAW_HOURS)
    
    # Check if there's an active session for this draw window
    active_session = db.query(LiveChatSession).filter(
        LiveChatSession.is_active == True,
        LiveChatSession.start_time == chat_start,
        LiveChatSession.end_time == chat_end
    ).first()
    
    if active_session:
        # Update cache
        _active_session_cache = active_session
        _cache_timestamp = datetime.utcnow()
        logger.info(f"Using existing chat session: {active_session.id}")
        return active_session
    
    # Deactivate any old sessions that might still be marked as active
    old_sessions = db.query(LiveChatSession).filter(
        LiveChatSession.is_active == True,
        LiveChatSession.end_time < datetime.utcnow()
    ).all()
    
    for old_session in old_sessions:
        old_session.is_active = False
    
    if old_sessions:
        db.commit()
        logger.info(f"Deactivated {len(old_sessions)} old sessions")
    
    # Create new session for this draw window
    new_session = LiveChatSession(
        session_name=f"Draw Chat - {next_draw_time.strftime('%B %d, %Y')}",
        start_time=chat_start,
        end_time=chat_end,
        is_active=True
    )
    
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    
    # Update cache
    _active_session_cache = new_session
    _cache_timestamp = datetime.utcnow()
    
    logger.info(f"Created new chat session: {new_session.id} for draw at {next_draw_time}")
    return new_session

def invalidate_session_cache():
    """Invalidate the session cache"""
    global _active_session_cache, _cache_timestamp
    _active_session_cache = None
    _cache_timestamp = None
    logger.debug("Session cache invalidated")

def get_current_active_session(db: Session) -> LiveChatSession:
    """Get the currently active session (for reading messages, etc.)"""
    global _active_session_cache, _cache_timestamp
    
    # First check cache
    if (_active_session_cache and _cache_timestamp and 
        datetime.utcnow() - _cache_timestamp < _cache_timeout):
        now = datetime.utcnow()
        if (_active_session_cache.start_time <= now <= _active_session_cache.end_time and
            _active_session_cache.is_active):
            return _active_session_cache
    
    # Find currently active session (within current time window)
    now = datetime.utcnow()
    active_session = db.query(LiveChatSession).filter(
        LiveChatSession.is_active == True,
        LiveChatSession.start_time <= now,
        LiveChatSession.end_time >= now
    ).first()
    
    if active_session:
        # Update cache
        _active_session_cache = active_session
        _cache_timestamp = datetime.utcnow()
        logger.info(f"Found current active session: {active_session.id}")
        return active_session
    
    # If no current session found, create one for the current draw window
    logger.warning("No current active session found, creating new one")
    return get_or_create_active_session(db)

async def broadcast_viewer_count_update(db: Session, session_id: int):
    """Broadcast viewer count update to all connected users"""
    active_viewers = db.query(LiveChatViewer).filter(
        LiveChatViewer.session_id == session_id,
        LiveChatViewer.is_active == True,
        LiveChatViewer.last_seen >= datetime.utcnow() - timedelta(minutes=5)
    ).count()
    
    await manager.broadcast_to_session(
        json.dumps({
            "type": "viewer_count_update",
            "viewer_count": active_viewers
        }),
        session_id
    )
    
    return active_viewers

@router.get("/status")
async def get_chat_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current chat status and session info"""
    if not LIVE_CHAT_ENABLED:
        return {"enabled": False, "message": "Live chat is disabled"}
    
    is_active = is_chat_window_active()
    session = None
    
    if is_active:
        session = get_or_create_active_session(db)
        
        # Add/update current user as a viewer
        existing_viewer = db.query(LiveChatViewer).filter(
            LiveChatViewer.session_id == session.id,
            LiveChatViewer.user_id == current_user.account_id
        ).first()
        
        if existing_viewer:
            # Update last seen time
            existing_viewer.last_seen = datetime.utcnow()
            existing_viewer.is_active = True
        else:
            # Create new viewer entry
            new_viewer = LiveChatViewer(
                session_id=session.id,
                user_id=current_user.account_id,
                joined_at=datetime.utcnow(),
                last_seen=datetime.utcnow(),
                is_active=True
            )
            db.add(new_viewer)
        
        db.commit()
        
        # Update viewer count
        active_viewers = db.query(LiveChatViewer).filter(
            LiveChatViewer.session_id == session.id,
            LiveChatViewer.is_active == True,
            LiveChatViewer.last_seen >= datetime.utcnow() - timedelta(minutes=5)
        ).count()
        
        session.viewer_count = active_viewers
        db.commit()
    
    return {
        "enabled": True,
        "is_active": is_active,
        "session": {
            "id": session.id if session else None,
            "name": session.session_name if session else None,
            "viewer_count": session.viewer_count if session else 0,
            "total_likes": session.total_likes if session else 0,
            "start_time": session.start_time.isoformat() if session else None,
            "end_time": session.end_time.isoformat() if session else None
        } if session else None
    }

@router.get("/messages")
async def get_chat_messages(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get recent chat messages"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    session = get_current_active_session(db)
    
    messages = db.query(LiveChatMessage).filter(
        LiveChatMessage.session_id == session.id
    ).order_by(LiveChatMessage.created_at.desc()).limit(min(limit, LIVE_CHAT_MESSAGE_HISTORY_LIMIT)).all()
    
    return {
        "messages": [
            {
                "id": msg.id,
                "user_id": msg.user_id,
                "username": get_display_username(msg.user),
                "profile_pic": msg.user.profile_pic_url,
                "message": msg.message,
                "message_type": msg.message_type,
                "likes": msg.likes,
                "created_at": msg.created_at.isoformat(),
                "is_winner": msg.user.badge_id in ["gold", "silver", "bronze"],  # Assuming winner badges
                "is_host": msg.user.is_admin
            }
            for msg in reversed(messages)
        ]
    }

@router.post("/like")
async def like_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Like the current live session"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    session = get_current_active_session(db)
    
    # Check if user already liked
    existing_like = db.query(LiveChatLike).filter(
        LiveChatLike.session_id == session.id,
        LiveChatLike.user_id == current_user.account_id,
        LiveChatLike.message_id.is_(None)
    ).first()
    
    if existing_like:
        raise HTTPException(status_code=400, detail="Already liked this session")
    
    # Add like
    new_like = LiveChatLike(
        session_id=session.id,
        user_id=current_user.account_id,
        message_id=None
    )
    
    db.add(new_like)
    session.total_likes += 1
    db.commit()
    
    # Broadcast like update
    await manager.broadcast_to_session(
        json.dumps({
            "type": "session_like_update",
            "total_likes": session.total_likes
        }),
        session.id
    )
    
    # Broadcast like count update
    await manager.broadcast_to_session(
        json.dumps({
            "type": "like_count_update",
            "total_likes": session.total_likes
        }),
        session.id
    )
    
    return {"message": "Session liked successfully", "total_likes": session.total_likes}

@router.post("/like-message/{message_id}")
async def like_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Like a specific message"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    message = db.query(LiveChatMessage).filter(LiveChatMessage.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    # Check if user already liked this message
    existing_like = db.query(LiveChatLike).filter(
        LiveChatLike.message_id == message_id,
        LiveChatLike.user_id == current_user.account_id
    ).first()
    
    if existing_like:
        raise HTTPException(status_code=400, detail="Already liked this message")
    
    # Add like
    new_like = LiveChatLike(
        session_id=message.session_id,
        user_id=current_user.account_id,
        message_id=message_id
    )
    
    db.add(new_like)
    message.likes += 1
    db.commit()
    
    # Broadcast message like update
    await manager.broadcast_to_session(
        json.dumps({
            "type": "message_like_update",
            "message_id": message_id,
            "likes": message.likes
        }),
        message.session_id
    )
    
    return {"message": "Message liked successfully", "likes": message.likes}

@router.get("/viewers")
async def get_viewer_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current viewer count for the active session"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    session = get_current_active_session(db)
    
    # Add/update current user as a viewer
    existing_viewer = db.query(LiveChatViewer).filter(
        LiveChatViewer.session_id == session.id,
        LiveChatViewer.user_id == current_user.account_id
    ).first()
    
    if existing_viewer:
        # Update last seen time
        existing_viewer.last_seen = datetime.utcnow()
        existing_viewer.is_active = True
    else:
        # Create new viewer entry
        new_viewer = LiveChatViewer(
            session_id=session.id,
            user_id=current_user.account_id,
            joined_at=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            is_active=True
        )
        db.add(new_viewer)
    
    db.commit()
    
    # Count active viewers (seen in last 5 minutes)
    active_viewers = db.query(LiveChatViewer).filter(
        LiveChatViewer.session_id == session.id,
        LiveChatViewer.is_active == True,
        LiveChatViewer.last_seen >= datetime.utcnow() - timedelta(minutes=5)
    ).count()
    
    # Update session viewer count
    session.viewer_count = active_viewers
    db.commit()
    
    return {
        "viewer_count": active_viewers,
        "session_id": session.id,
        "session_name": session.session_name
    }

@router.get("/likes")
async def get_like_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get current like count for the active session"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    session = get_current_active_session(db)
    
    return {
        "total_likes": session.total_likes,
        "session_id": session.id,
        "session_name": session.session_name
    }

@router.get("/stats")
async def get_chat_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get comprehensive chat statistics"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    session = get_current_active_session(db)
    
    # Add/update current user as a viewer
    existing_viewer = db.query(LiveChatViewer).filter(
        LiveChatViewer.session_id == session.id,
        LiveChatViewer.user_id == current_user.account_id
    ).first()
    
    if existing_viewer:
        # Update last seen time
        existing_viewer.last_seen = datetime.utcnow()
        existing_viewer.is_active = True
    else:
        # Create new viewer entry
        new_viewer = LiveChatViewer(
            session_id=session.id,
            user_id=current_user.account_id,
            joined_at=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            is_active=True
        )
        db.add(new_viewer)
    
    db.commit()
    
    # Count active viewers
    active_viewers = db.query(LiveChatViewer).filter(
        LiveChatViewer.session_id == session.id,
        LiveChatViewer.is_active == True,
        LiveChatViewer.last_seen >= datetime.utcnow() - timedelta(minutes=5)
    ).count()
    
    # Count total messages
    total_messages = db.query(LiveChatMessage).filter(
        LiveChatMessage.session_id == session.id
    ).count()
    
    # Count messages in last hour
    recent_messages = db.query(LiveChatMessage).filter(
        LiveChatMessage.session_id == session.id,
        LiveChatMessage.created_at >= datetime.utcnow() - timedelta(hours=1)
    ).count()
    
    # Update session viewer count
    session.viewer_count = active_viewers
    db.commit()
    
    return {
        "session_id": session.id,
        "session_name": session.session_name,
        "viewer_count": active_viewers,
        "total_likes": session.total_likes,
        "total_messages": total_messages,
        "recent_messages": recent_messages,
        "is_active": session.is_active,
        "start_time": session.start_time.isoformat(),
        "end_time": session.end_time.isoformat()
    }

@router.post("/send-message")
async def send_message_rest(
    message: str = Body(..., embed=True),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Send a message via REST API (for testing purposes)"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    session = get_current_active_session(db)
    
    # Rate limiting
    recent_messages = db.query(LiveChatMessage).filter(
        LiveChatMessage.user_id == current_user.account_id,
        LiveChatMessage.created_at >= datetime.utcnow() - timedelta(minutes=1)
    ).count()
    
    if recent_messages >= LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    
    # Save message
    new_message = LiveChatMessage(
        session_id=session.id,
        user_id=current_user.account_id,
        message=message.strip(),
        message_type="text"
    )
    
    db.add(new_message)
    db.commit()
    db.refresh(new_message)
    
    # Broadcast message to all connected users via WebSocket
    message_response = {
        "type": "new_message",
        "message": {
            "id": new_message.id,
            "user_id": current_user.account_id,
            "username": get_display_username(current_user),
            "profile_pic": current_user.profile_pic_url,
            "message": message.strip(),
            "message_type": "text",
            "likes": 0,
            "created_at": new_message.created_at.isoformat(),
            "is_winner": current_user.badge_id in ["gold", "silver", "bronze"],
            "is_host": current_user.is_admin
        }
    }
    
    await manager.broadcast_to_session(
        json.dumps(message_response),
        session.id
    )
    
    return {
        "message": "Message sent successfully",
        "message_id": new_message.id,
        "created_at": new_message.created_at.isoformat()
    }

# WebSocket authentication helper
async def authenticate_websocket_user(token: str, db: Session) -> User:
    """Authenticate user for WebSocket connection"""
    try:
        from auth import validate_descope_jwt
        user_info = validate_descope_jwt(token)
        
        # Find user in database
        user = db.query(User).filter(User.descope_user_id == user_info['userId']).first()
        if not user:
            # Try by email
            email = user_info.get('loginIds', [None])[0] or user_info.get('email')
            if email:
                user = db.query(User).filter(User.email == email).first()
        
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
            
        return user
    except Exception as e:
        logger.error(f"WebSocket authentication failed: {e}")
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: int,
    token: str = None,
    db: Session = Depends(get_db)
):
    """WebSocket endpoint for real-time chat"""
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA, reason="Chat not active")
        return
    
    # Authenticate user
    try:
        user = await authenticate_websocket_user(token, db)
        user_id = user.account_id
    except HTTPException as e:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(e.detail))
        return
    
    # Connect to session
    await manager.connect(websocket, user_id, session_id)
    
    # Add user to viewers
    session = db.query(LiveChatSession).filter(LiveChatSession.id == session_id).first()
    if session:
        # Check if viewer already exists
        existing_viewer = db.query(LiveChatViewer).filter(
            LiveChatViewer.session_id == session_id,
            LiveChatViewer.user_id == user_id
        ).first()
        
        if existing_viewer:
            existing_viewer.is_active = True
            existing_viewer.last_seen = datetime.utcnow()
        else:
            viewer = LiveChatViewer(
                session_id=session_id,
                user_id=user_id,
                is_active=True
            )
        db.add(viewer)
    
    db.commit()
    
    # Broadcast updated viewer count
    await broadcast_viewer_count_update(db, session_id)
    
    try:
        while True:
            # Receive message
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            if message_data.get("type") == "message":
                # Handle new message
                message_text = message_data.get("message", "").strip()
                if not message_text:
                    continue
                
                # Rate limiting
                recent_messages = db.query(LiveChatMessage).filter(
                    LiveChatMessage.user_id == user_id,
                    LiveChatMessage.created_at >= datetime.utcnow() - timedelta(minutes=1)
                ).count()
                
                if recent_messages >= LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE:
                    await manager.send_personal_message(
                        json.dumps({"type": "error", "message": "Rate limit exceeded"}),
                        websocket
                    )
                    continue
                
                # Save message
                new_message = LiveChatMessage(
                    session_id=session_id,
                    user_id=user_id,
                    message=message_text,
                    message_type="text"
                )
                
                db.add(new_message)
                db.commit()
                db.refresh(new_message)
                
                # Broadcast message to all connected users
                message_response = {
                    "type": "new_message",
                    "message": {
                        "id": new_message.id,
                        "user_id": user_id,
                        "username": get_display_username(user),
                        "profile_pic": user.profile_pic_url,
                        "message": message_text,
                        "message_type": "text",
                        "likes": 0,
                        "created_at": new_message.created_at.isoformat(),
                        "is_winner": user.badge_id in ["gold", "silver", "bronze"],
                        "is_host": user.is_admin
                    }
                }
                
                await manager.broadcast_to_session(
                    json.dumps(message_response),
                    session_id
                )
            
            elif message_data.get("type") == "ping":
                # Update viewer last_seen timestamp
                viewer = db.query(LiveChatViewer).filter(
                    LiveChatViewer.session_id == session_id,
                    LiveChatViewer.user_id == user_id
                ).first()
                if viewer:
                    viewer.last_seen = datetime.utcnow()
                    db.commit()
                
                await manager.send_personal_message(
                    json.dumps({"type": "pong"}),
                    websocket
                )
                
    except WebSocketDisconnect:
        manager.disconnect(websocket, session_id)
        
        # Update viewer status
        if session:
            viewer = db.query(LiveChatViewer).filter(
                LiveChatViewer.session_id == session_id,
                LiveChatViewer.user_id == user_id
            ).first()
            if viewer:
                viewer.is_active = False
                viewer.last_seen = datetime.utcnow()
                db.commit()
                
                # Broadcast updated viewer count
                await broadcast_viewer_count_update(db, session_id)
