import asyncio
import hashlib
import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from auth import validate_descope_jwt
from config import (
    E2EE_DM_ENABLED,
    E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER,
    E2EE_DM_SSE_ALLOW_QUERY_TOKEN,
    GROUPS_ENABLED,
    PRESENCE_ENABLED,
    PRESENCE_UPDATE_INTERVAL_SECONDS,
    REDIS_RETRY_INTERVAL_SECONDS,
    SSE_HEARTBEAT_SECONDS,
    SSE_MAX_MISSED_HEARTBEATS,
)
from db import get_db_context
from models import User
from utils.redis_pubsub import get_redis, subscribe_dm_user, subscribe_group

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dm", tags=["DM SSE"])

# Connection tracking for per-user limits
_active_dm_sse_connections: dict[int, set] = defaultdict(
    set
)  # user_id -> set of connection IDs


def sse_format(
    data: dict, event: Optional[str] = None, id_: Optional[str] = None
) -> bytes:
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token"
        )

    try:
        user_info = validate_descope_jwt(token)

        # Find user in database
        user = (
            db.query(User).filter(User.descope_user_id == user_info["userId"]).first()
        )
        if not user:
            # Try by email
            email = user_info.get("loginIds", [None])[0] or user_info.get("email")
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


def get_token_expiry(token: str) -> Optional[float]:
    """Decode token expiry timestamp (seconds since epoch) once per connection."""
    try:
        from auth import decode_jwt_payload

        payload = decode_jwt_payload(token)
        exp = payload.get("exp")
        return float(exp) if exp else None
    except Exception as e:
        logger.debug(f"Token expiry decode failed: {e}")
        return None


def _load_user_context(token: str) -> tuple[int, list[str]]:
    with get_db_context() as db:
        user = get_user_from_token(token, db)
        user_id = user.account_id
        group_ids: list[str] = []
        if GROUPS_ENABLED:
            try:
                from models import GroupParticipant

                group_ids = [
                    str(participant.group_id)
                    for participant in db.query(GroupParticipant)
                    .filter(
                        GroupParticipant.user_id == user_id,
                        GroupParticipant.is_banned == False,
                    )
                    .all()
                ]
            except Exception as e:
                logger.warning(
                    f"Group models unavailable, skipping group subscriptions: {e}"
                )
        return user_id, group_ids


def _update_presence(
    user_id: int,
    last_seen_at: Optional[datetime],
    device_online: Optional[bool],
    create_if_missing: bool,
) -> None:
    if not PRESENCE_ENABLED:
        return
    with get_db_context() as db:
        from models import UserPresence

        presence = (
            db.query(UserPresence).filter(UserPresence.user_id == user_id).first()
        )
        if presence:
            if last_seen_at is not None:
                presence.last_seen_at = last_seen_at
            if device_online is not None:
                presence.device_online = device_online
        elif create_if_missing:
            presence = UserPresence(
                user_id=user_id,
                last_seen_at=last_seen_at,
                device_online=device_online if device_online is not None else False,
            )
            db.add(presence)
        else:
            return
        db.commit()


@router.options("/sse")
async def dm_sse_options():
    """CORS preflight handler for SSE endpoint."""
    return {"status": "ok"}


@router.get("/sse")
async def dm_sse(request: Request, token: Optional[str] = Query(default=None)):
    """
    SSE endpoint for real-time DM message delivery.
    Accepts token via query param ?token=... or Authorization header.
    Subscribes to per-user Redis channel for all DM messages.
    """
    if not E2EE_DM_ENABLED:
        raise HTTPException(status_code=403, detail="E2EE DM is not enabled")

    # Extract token from Authorization header first, then optional query param
    token_param = token
    token = None
    auth_header = request.headers.get("Authorization") or request.headers.get(
        "authorization"
    )
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        if token_param and E2EE_DM_SSE_ALLOW_QUERY_TOKEN:
            token = token_param
        elif token_param:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Use Authorization header for SSE",
            )

    # Authenticate user and load group subscriptions without holding a long-lived DB session
    user_id, group_ids = await run_in_threadpool(_load_user_context, token)
    user_id_hash = hash_user_id(user_id)

    # Check connection limit per user
    connection_id = id(request)  # Use request object ID as connection identifier
    active_connections = _active_dm_sse_connections[user_id]
    if len(active_connections) >= E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER:
        logger.warning(f"Connection limit exceeded for user {user_id_hash}")
        raise HTTPException(
            status_code=429,
            detail=f"Maximum {E2EE_DM_MAX_CONCURRENT_STREAMS_PER_USER} concurrent streams allowed per user",
        )

    # Track connection
    _active_dm_sse_connections[user_id].add(connection_id)
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
            token_expiry = get_token_expiry(current_token) if current_token else None

            # Check if Redis is available before subscribing
            redis_available = get_redis() is not None
            last_redis_check = time.time()

            # Redis subscription to per-user channel (for DM and status notifications)
            redis_iter = None
            if redis_available:
                try:
                    redis_msgs = subscribe_dm_user(user_id)
                    redis_iter = redis_msgs.__aiter__()
                except Exception as e:
                    logger.warning(
                        f"Failed to subscribe to DM channel for user {user_id_hash}: {e}"
                    )
                    redis_available = False
                    redis_iter = None
            else:
                logger.info(
                    f"Redis unavailable, skipping real-time subscriptions for user {user_id_hash}"
                )

            # Load user's group memberships and subscribe to group channels
            group_subscriptions = {}
            if GROUPS_ENABLED and redis_available and group_ids:
                for group_id in group_ids:
                    try:
                        group_msgs = subscribe_group(group_id)
                        group_subscriptions[group_id] = group_msgs.__aiter__()
                    except Exception as e:
                        logger.warning(f"Failed to subscribe to group {group_id}: {e}")

            # Update presence last_seen_at on connect
            last_presence_update = None
            if PRESENCE_ENABLED:
                now = datetime.utcnow()
                await run_in_threadpool(_update_presence, user_id, now, True, True)
                last_presence_update = now

            # Heartbeat task
            heartbeat = asyncio.create_task(asyncio.sleep(SSE_HEARTBEAT_SECONDS))
            last_heartbeat_time = datetime.utcnow()
            dm_task: Optional[asyncio.Task[Any]] = (
                asyncio.create_task(redis_iter.__anext__())
                if redis_iter is not None
                else None
            )
            group_tasks: Dict[str, asyncio.Task[Any]] = {
                group_id: asyncio.create_task(group_iter.__anext__())
                for group_id, group_iter in group_subscriptions.items()
            }

            try:
                while True:
                    # Check if client disconnected (top of loop)
                    if await request.is_disconnected():
                        logger.debug(f"DM SSE client disconnected: user={user_id_hash}")
                        break

                    # Check for missed heartbeats
                    time_since_last_heartbeat = (
                        datetime.utcnow() - last_heartbeat_time
                    ).total_seconds()
                    max_heartbeat_interval = SSE_HEARTBEAT_SECONDS * (
                        SSE_MAX_MISSED_HEARTBEATS + 1
                    )
                    if time_since_last_heartbeat > max_heartbeat_interval:
                        logger.warning(
                            f"Too many missed heartbeats for user {user_id_hash}, closing connection"
                        )
                        break

                    # Race heartbeat vs redis messages (DM + groups)
                    tasks = {heartbeat}
                    if dm_task is not None:
                        tasks.add(dm_task)
                    tasks.update(group_tasks.values())

                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )

                    if heartbeat in done:
                        # Check token expiry during heartbeat
                        if token_expiry is not None and time.time() >= token_expiry:
                            logger.info(
                                f"Token expired for user {user_id_hash}, disconnecting"
                            )
                            yield sse_format(
                                {"type": "auth_expired", "message": "Token expired"}
                            )
                            break

                        # Update last heartbeat time
                        last_heartbeat_time = datetime.utcnow()

                        # Update presence last_seen_at on heartbeat
                        if (
                            PRESENCE_ENABLED
                            and last_presence_update is not None
                            and PRESENCE_UPDATE_INTERVAL_SECONDS > 0
                        ):
                            now = datetime.utcnow()
                            if (
                                now - last_presence_update
                            ).total_seconds() >= PRESENCE_UPDATE_INTERVAL_SECONDS:
                                await run_in_threadpool(
                                    _update_presence, user_id, now, None, False
                                )
                                last_presence_update = now

                        # Check Redis status periodically to reduce per-heartbeat overhead
                        now_ts = time.time()
                        if now_ts - last_redis_check >= REDIS_RETRY_INTERVAL_SECONDS:
                            redis_available = get_redis() is not None
                            last_redis_check = now_ts
                            if redis_available and redis_iter is None:
                                try:
                                    redis_msgs = subscribe_dm_user(user_id)
                                    redis_iter = redis_msgs.__aiter__()
                                    dm_task = asyncio.create_task(
                                        redis_iter.__anext__()
                                    )
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to resubscribe to DM channel for user {user_id_hash}: {e}"
                                    )
                                    redis_iter = None
                            if redis_available and group_ids:
                                for group_id in group_ids:
                                    if group_id in group_subscriptions:
                                        continue
                                    try:
                                        group_msgs = subscribe_group(group_id)
                                        group_subscriptions[group_id] = (
                                            group_msgs.__aiter__()
                                        )
                                        group_tasks[group_id] = asyncio.create_task(
                                            group_subscriptions[group_id].__anext__()
                                        )
                                    except Exception as e:
                                        logger.warning(
                                            f"Failed to resubscribe to group {group_id}: {e}"
                                        )
                        redis_status = "available" if redis_available else "unavailable"
                        relay_lag = redis_status == "unavailable"

                        # Send heartbeat with status
                        yield sse_format(
                            {
                                "type": "heartbeat",
                                "relay_lag": relay_lag,
                                "redis_status": redis_status,
                            }
                        )
                        # Reset heartbeat
                        heartbeat = asyncio.create_task(
                            asyncio.sleep(SSE_HEARTBEAT_SECONDS)
                        )

                    for task in done:
                        if task is heartbeat:
                            continue
                        try:
                            raw = task.result()
                        except StopAsyncIteration:
                            if task is dm_task:
                                logger.debug(
                                    f"Redis subscription ended: user={user_id_hash}"
                                )
                                dm_task = None
                                redis_iter = None
                                break
                            for group_id, group_task in list(group_tasks.items()):
                                if group_task is task:
                                    group_tasks.pop(group_id, None)
                                    group_subscriptions.pop(group_id, None)
                                    break
                            continue
                        except Exception as e:
                            logger.error(f"Error processing Redis message: {e}")
                            raw = None

                        if raw:
                            try:
                                event_data = json.loads(raw)
                                if "type" not in event_data:
                                    event_data["type"] = "dm"
                                yield sse_format(event_data)
                            except json.JSONDecodeError as e:
                                logger.error(f"Failed to parse Redis message: {e}")

                        if task is dm_task and redis_iter is not None:
                            dm_task = asyncio.create_task(redis_iter.__anext__())
                        else:
                            for group_id, group_task in list(group_tasks.items()):
                                if group_task is task:
                                    group_iter = group_subscriptions.get(group_id)
                                    if group_iter is not None:
                                        group_tasks[group_id] = asyncio.create_task(
                                            group_iter.__anext__()
                                        )
                                    break

            except StopAsyncIteration:
                logger.debug(f"Redis subscription ended: user={user_id_hash}")
            except asyncio.CancelledError:
                logger.debug(f"DM SSE stream cancelled: user={user_id_hash}")
            except Exception as e:
                logger.error(
                    f"Error in DM SSE stream: user={user_id_hash}, error={str(e)}"
                )
            finally:
                # Cancel all pending tasks
                tasks_to_cancel = [heartbeat]
                if dm_task is not None:
                    tasks_to_cancel.append(dm_task)
                tasks_to_cancel.extend(group_tasks.values())
                for task in tasks_to_cancel:
                    task.cancel()
                for task in tasks_to_cancel:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

                # Update presence device_online on disconnect
                if PRESENCE_ENABLED:
                    await run_in_threadpool(
                        _update_presence, user_id, None, False, False
                    )

                # Remove connection tracking
                _active_dm_sse_connections[user_id].discard(connection_id)
                if not _active_dm_sse_connections[user_id]:
                    del _active_dm_sse_connections[user_id]
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
    except Exception:
        # If we fail before the generator runs, ensure we release the per-user connection slot.
        _active_dm_sse_connections[user_id].discard(connection_id)
        if not _active_dm_sse_connections[user_id]:
            del _active_dm_sse_connections[user_id]
        logger.error(
            f"Failed to establish DM SSE stream: user={user_id_hash}", exc_info=True
        )
        raise HTTPException(status_code=500, detail="Failed to establish DM SSE stream")
