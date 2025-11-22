import asyncio
import json
import logging
from typing import Any, Dict, Optional

import redis.asyncio as redis

from config import REDIS_URL

logger = logging.getLogger(__name__)

CHAT_EVENT_QUEUE_KEY = "chat:event_queue"
DEFAULT_TYPING_DEDUP_MS = 1500

_redis_client: Optional[redis.Redis] = None
_redis_lock = asyncio.Lock()


async def get_chat_redis() -> Optional[redis.Redis]:
    """Create or return cached Redis connection for chat features."""
    global _redis_client

    if _redis_client:
        return _redis_client

    async with _redis_lock:
        if _redis_client:
            return _redis_client
        try:
            _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
            logger.info("Chat Redis client initialized")
        except Exception as exc:
            logger.error(f"Failed to initialize chat Redis client: {exc}")
            _redis_client = None
    return _redis_client


async def _run_pipeline(commands_cb):
    client = await get_chat_redis()
    if not client:
        return None
    try:
        pipe = client.pipeline()
        commands_cb(pipe)
        return await pipe.execute()
    except Exception as exc:
        logger.warning(f"Chat Redis pipeline error: {exc}")
        return None


async def check_rate_limit(
    namespace: str,
    identifier: Any,
    limit: int,
    window_seconds: int
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
    namespace: str,
    identifier: Any,
    limit: int,
    window_seconds: int
) -> Optional[bool]:
    """Wrapper around check_rate_limit for burst windows."""
    return await check_rate_limit(f"{namespace}:burst", identifier, limit, window_seconds)


async def should_emit_typing_event(
    channel_key: str,
    user_id: Any,
    dedup_ms: int = DEFAULT_TYPING_DEDUP_MS
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
    except Exception as exc:
        logger.warning(f"Chat Redis typing dedup error: {exc}")
        return True


async def enqueue_chat_event(event_type: str, payload: Dict[str, Any]) -> bool:
    """
    Push chat event payload onto Redis queue so a worker can process it.
    Returns False if queueing failed.
    """
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
    except Exception as exc:
        logger.error(f"Failed to enqueue chat event: {exc}")
        return False


async def clear_typing_event(channel_key: str, user_id: Any) -> None:
    """Remove cached typing flag so next typing event can fire immediately."""
    client = await get_chat_redis()
    if not client:
        return
    redis_key = f"chat:typing:{channel_key}:{user_id}"
    try:
        await client.delete(redis_key)
    except Exception as exc:
        logger.debug(f"Chat Redis typing cleanup failed: {exc}")
