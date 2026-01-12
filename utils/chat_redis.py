import asyncio
import json
import logging
from typing import Any, Dict, Optional

import redis.asyncio as redis
from redis.exceptions import ConnectionError, RedisError, TimeoutError

from core.config import REDIS_URL
from utils.logging_helpers import log_error, log_info, log_warning

logger = logging.getLogger(__name__)

CHAT_EVENT_QUEUE_KEY = "chat:event_queue"
DEFAULT_TYPING_DEDUP_MS = 1500

_redis_client: Optional[redis.Redis] = None
_redis_lock = asyncio.Lock()


async def _check_connection_health(client: redis.Redis) -> bool:
    """Check if Redis connection is healthy by sending a ping."""
    try:
        await asyncio.wait_for(client.ping(), timeout=2.0)
        return True
    except (
        ConnectionError,
        TimeoutError,
        RedisError,
        asyncio.TimeoutError,
        OSError,
    ) as exc:
        logger.debug(f"Redis connection health check failed: {exc}")
        return False
    except Exception as exc:
        logger.debug(f"Redis connection health check failed (unexpected): {exc}")
        return False


async def _create_redis_client() -> Optional[redis.Redis]:
    """Create a new Redis client with connection pool settings."""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            client = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,  # Check connection health every 30 seconds
                retry_on_error=[ConnectionError, TimeoutError, OSError],
            )
            # Test the connection
            await asyncio.wait_for(client.ping(), timeout=2.0)
            log_info(logger, "Chat Redis client initialized and connected")
            return client
        except (
            ConnectionError,
            TimeoutError,
            RedisError,
            OSError,
            asyncio.TimeoutError,
        ) as exc:
            log_warning(
                logger,
                "Chat Redis initialization attempt failed",
                attempt=attempt + 1,
                max_attempts=max_attempts,
                error=str(exc),
                exc_info=True,
            )
            await asyncio.sleep(0.1 * (attempt + 1))
        except Exception as exc:
            log_error(
                logger,
                "Unexpected error while initializing chat Redis client",
                exc_info=True,
                error=str(exc),
            )
            return None

    log_error(
        logger,
        "Failed to initialize chat Redis client after retries",
        error="Max attempts reached",
    )
    return None


async def get_chat_redis() -> Optional[redis.Redis]:
    """Create or return cached Redis connection for chat features with health checking."""
    global _redis_client

    # Check if we have a cached client and if it's healthy
    if _redis_client:
        if await _check_connection_health(_redis_client):
            return _redis_client
        else:
            # Connection is stale, close it and reset
            log_warning(logger, "Redis connection is stale, reconnecting")
            try:
                await _redis_client.aclose()
            except Exception:
                pass
            _redis_client = None

    # Create new connection with lock to prevent race conditions
    async with _redis_lock:
        # Double-check after acquiring lock
        if _redis_client and await _check_connection_health(_redis_client):
            return _redis_client

        # Create new connection
        _redis_client = await _create_redis_client()
        return _redis_client


async def _run_pipeline(commands_cb):
    """Run a Redis pipeline with automatic reconnection on connection errors."""
    max_retries = 2
    for attempt in range(max_retries):
        client = await get_chat_redis()
        if not client:
            return None
        try:
            pipe = client.pipeline()
            commands_cb(pipe)
            return await pipe.execute()
        except (ConnectionError, TimeoutError, RedisError, OSError) as exc:
            log_warning(
                logger,
                "Chat Redis pipeline connection error",
                attempt=attempt + 1,
                max_retries=max_retries,
                error=str(exc),
            )
            # Reset client to force reconnection on next attempt
            global _redis_client
            async with _redis_lock:
                if _redis_client == client:
                    try:
                        await _redis_client.aclose()
                    except Exception:
                        pass
                    _redis_client = None
            if attempt < max_retries - 1:
                await asyncio.sleep(0.1)  # Brief delay before retry
                continue
            return None
        except Exception as exc:
            log_warning(
                logger, "Chat Redis pipeline error", error=str(exc), exc_info=True
            )
            return None
    return None


async def check_rate_limit(
    namespace: str, identifier: Any, limit: int, window_seconds: int
) -> Optional[bool]:
    """
    Increment rate limit counter for identifier and return True if allowed.
    Returns None if Redis is unavailable so caller can fall back to DB checks.
    """
    key = f"chat:rl:{namespace}:{identifier}"

    def _commands(pipe):
        pipe.incr(key, 1)
        pipe.expire(key, window_seconds)

    result = await _run_pipeline(_commands)
    if result is None:
        return None

    count = result[0]
    return count <= limit


async def check_burst_limit(
    namespace: str, identifier: Any, limit: int, window_seconds: int
) -> Optional[bool]:
    """Wrapper around check_rate_limit for burst windows."""
    return await check_rate_limit(
        f"{namespace}:burst", identifier, limit, window_seconds
    )


async def should_emit_typing_event(
    channel_key: str, user_id: Any, dedup_ms: int = DEFAULT_TYPING_DEDUP_MS
) -> bool:
    """
    Returns True if typing event should be emitted. Stores a short-lived key
    so repeated events within dedup window are suppressed.
    Falls back to True if Redis is unavailable.
    """
    client = await get_chat_redis()
    if not client:
        return True

    redis_key = f"chat:typing:{channel_key}:{user_id}"
    try:
        return await client.set(redis_key, "1", px=dedup_ms, nx=True)
    except (ConnectionError, TimeoutError, RedisError, OSError) as exc:
        logger.warning(f"Chat Redis typing dedup connection error: {exc}")
        # Reset client to force reconnection
        global _redis_client
        async with _redis_lock:
            if _redis_client == client:
                try:
                    await _redis_client.aclose()
                except Exception:
                    pass
                _redis_client = None
        return True
    except Exception as exc:
        logger.warning(f"Chat Redis typing dedup error: {exc}")
        return True


async def enqueue_chat_event(event_type: str, payload: Dict[str, Any]) -> bool:
    """
    Push chat event payload onto Redis queue so a worker can process it.
    Returns False if queueing failed.
    """
    max_retries = 2
    for attempt in range(max_retries):
        client = await get_chat_redis()
        if not client:
            return False

        try:
            entry = json.dumps(
                {
                    "type": event_type,
                    "payload": payload,
                }
            )
            await client.rpush(CHAT_EVENT_QUEUE_KEY, entry)
            return True
        except (ConnectionError, TimeoutError, RedisError, OSError) as exc:
            log_warning(
                logger,
                "Chat Redis connection error during enqueue",
                attempt=attempt + 1,
                max_retries=max_retries,
                error=str(exc),
            )
            # Reset client to force reconnection on next attempt
            global _redis_client
            async with _redis_lock:
                if _redis_client == client:
                    try:
                        await _redis_client.aclose()
                    except Exception:
                        pass
                    _redis_client = None
            if attempt < max_retries - 1:
                await asyncio.sleep(0.1)  # Brief delay before retry
                continue
            log_error(
                logger,
                "Failed to enqueue chat event after max retries",
                event_type=event_type,
                max_retries=max_retries,
                error=str(exc),
            )
            return False
        except Exception as exc:
            log_error(
                logger,
                "Failed to enqueue chat event",
                event_type=event_type,
                error=str(exc),
                exc_info=True,
            )
            return False
    return False


async def clear_typing_event(channel_key: str, user_id: Any) -> None:
    """Remove cached typing flag so next typing event can fire immediately."""
    client = await get_chat_redis()
    if not client:
        return
    redis_key = f"chat:typing:{channel_key}:{user_id}"
    try:
        await client.delete(redis_key)
    except (ConnectionError, TimeoutError, RedisError, OSError) as exc:
        logger.debug(f"Chat Redis typing cleanup connection error: {exc}")
        # Reset client to force reconnection
        global _redis_client
        async with _redis_lock:
            if _redis_client == client:
                try:
                    await _redis_client.aclose()
                except Exception:
                    pass
                _redis_client = None
    except Exception as exc:
        logger.debug(f"Chat Redis typing cleanup failed: {exc}")
