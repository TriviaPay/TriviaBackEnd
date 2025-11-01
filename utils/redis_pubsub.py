"""
Redis pub/sub utilities for live chat events.
"""
import json
import logging
import asyncio
from typing import AsyncIterator, Optional
import redis.asyncio as redis

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    """Get or create Redis connection singleton."""
    global _redis
    if _redis is None:
        from config import REDIS_URL
        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            logger.info(f"Redis connection initialized: {REDIS_URL}")
        except Exception as e:
            logger.error(f"Failed to initialize Redis connection: {e}")
            raise
    return _redis


def channel_for_session(session_id: int) -> str:
    """Generate Redis channel name for a session."""
    return f"live_chat:session:{session_id}"


async def publish_event(session_id: int, event: dict) -> None:
    """
    Publish an event to the Redis channel for a session.
    
    Args:
        session_id: Session ID (integer)
        event: Event dictionary to publish (will be JSON-encoded)
    """
    try:
        r = get_redis()
        channel = channel_for_session(session_id)
        await r.publish(channel, json.dumps(event))
        logger.debug(f"Published event to {channel}: {event.get('type', 'unknown')}")
    except Exception as e:
        logger.error(f"Failed to publish event to session {session_id}: {e}")
        raise


async def subscribe(session_id: int) -> AsyncIterator[str]:
    """
    Subscribe to Redis channel for a session and yield messages.
    Auto-reconnects on connection drops.
    
    Args:
        session_id: Session ID (integer)
        
    Yields:
        JSON strings from Redis pub/sub
    """
    channel = channel_for_session(session_id)
    psub = None
    
    while True:
        try:
            r = get_redis()
            psub = r.pubsub()
            await psub.subscribe(channel)
            logger.debug(f"Subscribed to Redis channel: {channel}")
            
            async for msg in psub.listen():
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                # msg["data"] is already a JSON string (decode_responses=True)
                yield msg["data"]
                
        except asyncio.CancelledError:
            # Clean shutdown
            break
        except Exception as e:
            logger.error(f"Error in Redis subscription for session {session_id}: {e}")
            # Backoff and reconnect
            await asyncio.sleep(1)
            continue
        finally:
            # Cleanup
            if psub:
                try:
                    await psub.unsubscribe(channel)
                    await psub.close()
                    logger.debug(f"Unsubscribed from Redis channel: {channel}")
                except Exception:
                    pass
                psub = None
            
            # If we broke out of the loop due to CancelledError, don't reconnect
            # This allows graceful shutdown
            try:
                import sys
                exc_type = sys.exc_info()[0]
                if exc_type == asyncio.CancelledError:
                    break
            except Exception:
                pass

