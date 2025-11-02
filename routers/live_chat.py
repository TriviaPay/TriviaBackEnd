from fastapi import APIRouter, Depends, HTTPException, Request, status, Body, Query
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Dict, Any, AsyncGenerator, Optional, Set
from collections import defaultdict
import json
import os
import pytz
import asyncio
import logging
import hashlib

from db import get_db
from models import User, LiveChatSession, LiveChatMessage, LiveChatLike, LiveChatViewer
from routers.dependencies import get_current_user
from utils.draw_calculations import get_next_draw_time
from utils.redis_pubsub import publish_event, subscribe
from utils.viewer_tracking import mark_viewer_seen, get_active_viewer_count
from auth import validate_descope_jwt
from config import (
    LIVE_CHAT_ENABLED, 
    LIVE_CHAT_PRE_DRAW_HOURS, 
    LIVE_CHAT_POST_DRAW_HOURS,
    LIVE_CHAT_MAX_MESSAGES_PER_USER_PER_MINUTE,
    LIVE_CHAT_MESSAGE_HISTORY_LIMIT,
    LIVE_CHAT_MAX_MESSAGE_LENGTH,
    SSE_HEARTBEAT_SECONDS
)

# Configure logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live-chat", tags=["Live Chat"])

# Connection tracking for per-user limits
_active_sse_connections: Dict[int, Set[int]] = defaultdict(set)  # user_id -> set of session_ids
_max_concurrent_streams_per_user = 3


def sse_format(data: Dict, event: Optional[str] = None, id_: Optional[str] = None) -> bytes:
    """
    Build an SSE frame.
    JSON encoding prevents CRLF injection from user content.
    
    Args:
        data: Dictionary to send as JSON
        event: Optional event type name
        id_: Optional event ID
        
    Returns:
        Formatted SSE bytes
    """
    chunks = []
    if event:
        chunks.append(f"event: {event}\n")
    if id_:
        chunks.append(f"id: {id_}\n")
    # One 'data:' line; keep it under a few KB
    # JSON encoding ensures no CRLF injection from user content
    payload = json.dumps(data, separators=(",", ":"))
    chunks.append(f"data: {payload}\n\n")
    return "".join(chunks).encode("utf-8")


def sse_retry(ms: int = 5000) -> bytes:
    """Generate SSE retry hint frame."""
    return f"retry: {ms}\n\n".encode("utf-8")


def hash_user_id(user_id: int) -> str:
    """Hash user ID for logging (privacy)."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]


def get_user_from_token(token: Optional[str], db: Session) -> User:
    """
    Authenticate user from token (query param or Authorization header).
    
    Args:
        token: JWT token string (not logged for security)
        db: Database session
        
    Returns:
        User object
        
    Raises:
        HTTPException: If authentication fails
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    
    try:
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Authentication failed: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")


def check_token_expiry(token: str) -> bool:
    """
    Check if token is expired (for heartbeat checks).
    
    Args:
        token: JWT token string
        
    Returns:
        True if token is valid (not expired), False otherwise
    """
    try:
        from auth import decode_jwt_payload
        payload = decode_jwt_payload(token)
        exp = payload.get('exp')
        if not exp:
            return True  # No expiry claim, assume valid
        
        import time
        return exp > time.time()
    except Exception as e:
        logger.debug(f"Token expiry check failed: {e}")
        return False

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
    """
    Check if we're within the active chat window.
    
    Chat is active:
    - LIVE_CHAT_PRE_DRAW_HOURS (default 1h) before the next draw
    - LIVE_CHAT_POST_DRAW_HOURS (default 1h) after the draw completes
    
    This ensures chat is only available during the relevant draw windows.
    """
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
    """Publish viewer count update event to Redis"""
    active_viewers = get_active_viewer_count(session_id, db)
    
    await publish_event(session_id, {
        "type": "viewer_count_update",
        "viewer_count": active_viewers
    })
    
    return active_viewers

@router.options("/sse/{session_id}")
async def live_chat_sse_options():
    """
    CORS preflight handler for SSE endpoint.
    Returns CORS headers to allow GET requests from browser clients.
    """
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Origin, X-Requested-With",
            "Access-Control-Max-Age": "86400",  # 24 hours
        }
    )


@router.get("/sse/{session_id}")
async def live_chat_sse(
    request: Request,
    session_id: int,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db)
):
    """
    SSE endpoint for real-time chat events.
    Accepts token via query param ?token=... or Authorization header.
    
    Session window: Chat is only active 1h before and 1h after draw time.
    This is enforced by the is_chat_window_active() check.
    """
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    # Extract token from query param or Authorization header
    if not token:
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    
    # Authenticate user
    user = get_user_from_token(token, db)
    user_id_hash = hash_user_id(user.account_id)
    
    # Check connection limit per user
    active_connections = _active_sse_connections[user.account_id]
    if len(active_connections) >= _max_concurrent_streams_per_user:
        logger.warning(f"Connection limit exceeded for user {user_id_hash}")
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {_max_concurrent_streams_per_user} concurrent streams allowed per user"
        )
    
    # Verify session exists and is active
    # session_id is validated as int by FastAPI, preventing Redis channel injection
    if session_id <= 0:
        raise HTTPException(status_code=400, detail="Invalid session ID")
    
    session = db.query(LiveChatSession).filter(LiveChatSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Track connection
    _active_sse_connections[user.account_id].add(session_id)
    logger.info(f"SSE connection opened: user={user_id_hash}, session={session_id}")
    
    try:
        async def event_stream() -> AsyncGenerator[bytes, None]:
            """
            Multiplex 2 sources:
            1) Redis pub/sub messages for this session
            2) Heartbeats to keep the connection alive and refresh viewer presence
            """
            # Send retry hint first (helps clients know how fast to reconnect)
            yield sse_retry(5000)
            
            # Kick: immediately mark presence and send current viewer count
            await mark_viewer_seen(session_id, user.account_id, db)
            vc = get_active_viewer_count(session_id, db)
            yield sse_format({"type": "viewer_count_update", "viewer_count": vc})
            
            # Store token for expiry checks during heartbeats
            current_token = token
            
            # Redis subscription
            redis_msgs = subscribe(session_id)
            redis_iter = redis_msgs.__aiter__()
            
            # Heartbeat task
            heartbeat = asyncio.create_task(asyncio.sleep(SSE_HEARTBEAT_SECONDS))
            pending = set()
            
            try:
                while True:
                    # Check if client disconnected (top of loop)
                    if await request.is_disconnected():
                        logger.debug(f"SSE client disconnected: user={user_id_hash}, session={session_id}")
                        break
                    
                    # Race heartbeat vs redis message
                    redis_next_task = asyncio.create_task(redis_iter.__anext__())
                    pending = {heartbeat, redis_next_task}
                    done, pending = await asyncio.wait(
                        pending,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    
                    if heartbeat in done:
                        # Check token expiry during heartbeat
                        if current_token and not check_token_expiry(current_token):
                            logger.info(f"Token expired for user {user_id_hash}, disconnecting")
                            break
                        
                        # Update presence + send heartbeat + publish viewer count
                        await mark_viewer_seen(session_id, user.account_id, db)
                        vc = get_active_viewer_count(session_id, db)
                        # Publish viewer count update so all clients see it
                        await publish_event(session_id, {
                            "type": "viewer_count_update",
                            "viewer_count": vc
                        })
                        yield b": heartbeat\n\n"
                        # Reset heartbeat
                        heartbeat = asyncio.create_task(asyncio.sleep(SSE_HEARTBEAT_SECONDS))
                    
                    for task in list(done):
                        if task is heartbeat:
                            continue
                        # A redis message arrived
                        try:
                            raw = await task  # JSON string published by server
                            event_data = json.loads(raw)
                            yield sse_format(event_data)
                        except StopAsyncIteration:
                            logger.debug(f"Redis subscription ended: user={user_id_hash}, session={session_id}")
                            break
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse Redis message: {e}")
                            continue
                        except Exception as e:
                            logger.error(f"Error processing Redis message: {e}")
                            continue
                    
                    # Cancel any pending tasks (except heartbeat if it's still valid)
                    for task in list(pending):
                        if task != heartbeat:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                        
            except StopAsyncIteration:
                logger.debug(f"Redis subscription ended: user={user_id_hash}, session={session_id}")
            except asyncio.CancelledError:
                logger.debug(f"SSE stream cancelled: user={user_id_hash}, session={session_id}")
            except Exception as e:
                logger.error(f"Error in SSE stream: user={user_id_hash}, session={session_id}, error={str(e)}")
            finally:
                # Cancel all pending tasks
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
                for task in list(pending):
                    if task != heartbeat:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                logger.info(f"SSE connection closed: user={user_id_hash}, session={session_id}")
        
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable nginx buffering
                "Access-Control-Allow-Origin": "*",  # Or lock down to your app origin
            },
        )
    finally:
        # Remove connection tracking
        _active_sse_connections[user.account_id].discard(session_id)
        if not _active_sse_connections[user.account_id]:
            del _active_sse_connections[user.account_id]


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
    """Like the current live session. Idempotent: if already liked, returns current count."""
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
        # Already liked - recalculate total_likes from database to ensure accuracy
        # This handles cases where total_likes field might be out of sync
        actual_count = db.query(LiveChatLike).filter(
            LiveChatLike.session_id == session.id,
            LiveChatLike.message_id.is_(None)  # Only session-level likes
        ).count()
        
        # Sync the count back to the session if it differs
        if session.total_likes != actual_count:
            session.total_likes = actual_count
            db.commit()
            db.refresh(session)
        
        return {
            "message": "Session already liked",
            "total_likes": actual_count,
            "already_liked": True
        }
    
    # Add like
    new_like = LiveChatLike(
        session_id=session.id,
        user_id=current_user.account_id,
        message_id=None
    )
    
    db.add(new_like)
    session.total_likes += 1
    db.commit()
    db.refresh(session)
    
    # Publish like update to Redis
    await publish_event(session.id, {
        "type": "session_like_update",
        "total_likes": session.total_likes
    })
    
    return {
        "message": "Session liked successfully",
        "total_likes": session.total_likes,
        "already_liked": False
    }

@router.post("/like-message/{message_id}")
async def like_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Like a specific message. Idempotent: if already liked, returns current count."""
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
        # Already liked - return success with current count (idempotent behavior)
        db.refresh(message)
        return {
            "message": "Message already liked",
            "likes": message.likes,
            "already_liked": True
        }
    
    # Add like
    new_like = LiveChatLike(
        session_id=message.session_id,
        user_id=current_user.account_id,
        message_id=message_id
    )
    
    db.add(new_like)
    message.likes += 1
    db.commit()
    db.refresh(message)
    
    # Publish message like update to Redis
    await publish_event(message.session_id, {
        "type": "message_like_update",
        "message_id": message_id,
        "likes": message.likes
    })
    
    return {
        "message": "Message liked successfully",
        "likes": message.likes,
        "already_liked": False
    }

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
    
    # Recalculate to ensure accuracy (count from actual like records)
    actual_count = db.query(LiveChatLike).filter(
        LiveChatLike.session_id == session.id,
        LiveChatLike.message_id.is_(None)  # Only session-level likes
    ).count()
    
    # Sync if needed
    if session.total_likes != actual_count:
        session.total_likes = actual_count
        db.commit()
    
    return {
        "total_likes": actual_count,
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

class SendMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Message text")
    client_message_id: Optional[str] = Field(None, description="Optional client-provided ID for idempotency")


@router.post("/send-message")
async def send_message_rest(
    request: SendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Send a message via REST API.
    
    Supports idempotent writes: if client_message_id is provided and matches
    an existing message from the same user in this session, returns the existing
    message ID to prevent duplicates on mobile network retries.
    """
    if not LIVE_CHAT_ENABLED or not is_chat_window_active():
        raise HTTPException(status_code=403, detail="Chat is not active")
    
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # Validate message length
    if len(message) > LIVE_CHAT_MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Message exceeds maximum length of {LIVE_CHAT_MAX_MESSAGE_LENGTH} characters"
        )
    
    session = get_current_active_session(db)
    
    # Check for duplicate message (idempotent write)
    if request.client_message_id:
        existing_message = db.query(LiveChatMessage).filter(
            LiveChatMessage.session_id == session.id,
            LiveChatMessage.user_id == current_user.account_id,
            LiveChatMessage.client_message_id == request.client_message_id
        ).first()
        
        if existing_message:
            logger.debug(f"Duplicate message detected (idempotent write): client_message_id={request.client_message_id}")
            return {
                "message": "Message already sent",
                "message_id": existing_message.id,
                "created_at": existing_message.created_at.isoformat(),
                "duplicate": True
            }
    
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
        message=message,
        message_type="text",
        client_message_id=request.client_message_id if request.client_message_id else None
    )
    
    db.add(new_message)
    db.commit()
    db.refresh(new_message)
    
    # Publish message to Redis for SSE clients
    message_event = {
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
    
    await publish_event(session.id, message_event)
    
    return {
        "message": "Message sent successfully",
        "message_id": new_message.id,
        "created_at": new_message.created_at.isoformat(),
        "duplicate": False
    }


@router.get("/metrics")
async def get_live_chat_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get live chat metrics (admin-only endpoint).
    
    Returns:
    - Current open SSE connections count
    - Connections per user breakdown
    - Total messages sent today
    - Active sessions count
    """
    # Only allow admins to access metrics
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Count active SSE connections
    total_connections = sum(len(sessions) for sessions in _active_sse_connections.values())
    connections_per_user = {
        str(user_id): len(sessions) 
        for user_id, sessions in _active_sse_connections.items()
    }
    
    # Count messages sent today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    messages_today = db.query(LiveChatMessage).filter(
        LiveChatMessage.created_at >= today_start
    ).count()
    
    # Count active sessions
    active_sessions = db.query(LiveChatSession).filter(
        LiveChatSession.is_active == True
    ).count()
    
    # Count total active viewers across all sessions
    active_viewers_total = db.query(LiveChatViewer).filter(
        LiveChatViewer.is_active == True,
        LiveChatViewer.last_seen >= datetime.utcnow() - timedelta(minutes=5)
    ).count()
    
    return {
        "status": "success",
        "metrics": {
            "sse_connections": {
                "total": total_connections,
                "per_user": connections_per_user,
                "max_per_user": _max_concurrent_streams_per_user
            },
            "messages": {
                "today": messages_today
            },
            "sessions": {
                "active": active_sessions
            },
            "viewers": {
                "active_total": active_viewers_total
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    }

