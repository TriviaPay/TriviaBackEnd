from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional, AsyncGenerator
from collections import defaultdict
import json
import asyncio
import logging
import hashlib

from db import get_db
from models import User, DMParticipant
from routers.dependencies import get_current_user
from auth import validate_descope_jwt
from config import (
    E2EE_DM_ENABLED, 
    SSE_HEARTBEAT_SECONDS,
    E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER,
    SSE_MAX_MISSED_HEARTBEATS
)
from utils.redis_pubsub import subscribe_dm_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dm", tags=["DM SSE"])

# Connection tracking for per-user limits
_active_dm_sse_connections: dict[int, set] = defaultdict(set)  # user_id -> set of connection IDs


def sse_format(data: dict, event: Optional[str] = None, id_: Optional[str] = None) -> bytes:
    """Build an SSE frame."""
    chunks = []
    if event:
        chunks.append(f"event: {event}\n")
    if id_:
        chunks.append(f"id: {id_}\n")
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
    """Authenticate user from token (query param or Authorization header)."""
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
    """Check if token is expired (for heartbeat checks)."""
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


@router.options("/sse")
async def dm_sse_options():
    """CORS preflight handler for SSE endpoint."""
    return {
        "status": "ok"
    }


@router.get("/sse")
async def dm_sse(
    request: Request,
    token: Optional[str] = Query(default=None),
    db: Session = Depends(get_db)
):
    """
    SSE endpoint for real-time DM message delivery.
    Accepts token via query param ?token=... or Authorization header.
    Subscribes to per-user Redis channel for all DM messages.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")
    
    # Extract token from query param or Authorization header
    if not token:
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            token = auth_header.split(" ", 1)[1].strip()
    
    # Authenticate user
    user = get_user_from_token(token, db)
    user_id_hash = hash_user_id(user.account_id)
    
    # Check connection limit per user
    connection_id = id(request)  # Use request object ID as connection identifier
    active_connections = _active_dm_sse_connections[user.account_id]
    if len(active_connections) >= E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER:
        logger.warning(f"Connection limit exceeded for user {user_id_hash}")
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER} concurrent streams allowed per user"
        )
    
    # Track connection
    _active_dm_sse_connections[user.account_id].add(connection_id)
    logger.info(f"DM SSE connection opened: user={user_id_hash}")
    
    try:
        async def event_stream() -> AsyncGenerator[bytes, None]:
            """
            Multiplex 2 sources:
            1) Redis pub/sub messages for this user
            2) Heartbeats to keep the connection alive
            """
            # Send retry hint first
            yield sse_retry(5000)
            
            # Store token for expiry checks during heartbeats
            current_token = token
            
            # Redis subscription to per-user channel
            redis_msgs = subscribe_dm_user(user.account_id)
            redis_iter = redis_msgs.__aiter__()
            
            # Heartbeat task
            heartbeat = asyncio.create_task(asyncio.sleep(SSE_HEARTBEAT_SECONDS))
            last_heartbeat_time = datetime.utcnow()
            pending = set()
            
            try:
                while True:
                    # Check if client disconnected (top of loop)
                    if await request.is_disconnected():
                        logger.debug(f"DM SSE client disconnected: user={user_id_hash}")
                        break
                    
                    # Check for missed heartbeats
                    time_since_last_heartbeat = (datetime.utcnow() - last_heartbeat_time).total_seconds()
                    max_heartbeat_interval = SSE_HEARTBEAT_SECONDS * (SSE_MAX_MISSED_HEARTBEATS + 1)
                    if time_since_last_heartbeat > max_heartbeat_interval:
                        logger.warning(f"Too many missed heartbeats for user {user_id_hash}, closing connection")
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
                            yield sse_format({"type": "auth_expired", "message": "Token expired"})
                            break
                        
                        # Update last heartbeat time
                        last_heartbeat_time = datetime.utcnow()
                        
                        # Check Redis status and include in heartbeat
                        from utils.redis_pubsub import get_redis
                        redis_status = "available" if get_redis() else "unavailable"
                        relay_lag = redis_status == "unavailable"
                        
                        # Send heartbeat with status
                        yield sse_format({
                            "type": "heartbeat",
                            "relay_lag": relay_lag,
                            "redis_status": redis_status
                        })
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
                            logger.debug(f"Redis subscription ended: user={user_id_hash}")
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
                logger.debug(f"Redis subscription ended: user={user_id_hash}")
            except asyncio.CancelledError:
                logger.debug(f"DM SSE stream cancelled: user={user_id_hash}")
            except Exception as e:
                logger.error(f"Error in DM SSE stream: user={user_id_hash}, error={str(e)}")
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
                logger.info(f"DM SSE connection closed: user={user_id_hash}")
        
        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            },
        )
    finally:
        # Remove connection tracking
        _active_dm_sse_connections[user.account_id].discard(connection_id)
        if not _active_dm_sse_connections[user.account_id]:
            del _active_dm_sse_connections[user.account_id]

