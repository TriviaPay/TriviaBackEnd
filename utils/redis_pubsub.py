"""
Redis pub/sub utilities for live chat events.
"""

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

import redis.asyncio as redis  # type: ignore

logger = logging.getLogger(__name__)

_redis: Optional[redis.Redis] = None
_redis_initialized = False
_redis_unavailable = False
_redis_last_retry = 0
_redis_retry_interval = 60  # Retry every 60 seconds if unavailable


def get_redis() -> Optional[redis.Redis]:
    """
    Get or create Redis connection singleton. Returns None if Redis is unavailable.
    """
    global _redis, _redis_initialized, _redis_unavailable, _redis_last_retry

    # If Redis was unavailable, allow retry after interval
    if _redis_unavailable:
        current_time = time.time()
        if current_time - _redis_last_retry < _redis_retry_interval:
            return None
        # Reset flag to allow retry
        _redis_unavailable = False
        _redis_initialized = False
        _redis = None
        logger.debug("Retrying Redis connection after unavailability period")

    if _redis is None and not _redis_initialized:
        from config import REDIS_URL

        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            logger.info(f"Redis connection initialized: {REDIS_URL}")
            _redis_initialized = True
            _redis_unavailable = False
        except Exception as e:
            logger.warning(
                f"Redis unavailable: {e}. Real-time updates will be disabled."
            )
            _redis_unavailable = True
            _redis_initialized = True
            _redis_last_retry = time.time()
            return None

    return _redis


def channel_for_session(session_id: int) -> str:
    """Generate Redis channel name for a session."""
    return f"live_chat:session:{session_id}"


async def publish_event(session_id: int, event: dict) -> None:
    """
    Publish an event to the Redis channel for a session.
    Silently fails if Redis is unavailable - core functionality continues.

    Args:
        session_id: Session ID (integer)
        event: Event dictionary to publish (will be JSON-encoded)
    """
    try:
        r = get_redis()
        if r is None:
            # Redis unavailable - log and continue
            logger.debug(
                f"Redis unavailable, skipping publish for session {session_id}"
            )
            return

        channel = channel_for_session(session_id)
        await r.publish(channel, json.dumps(event))
        logger.debug(f"Published event to {channel}: {event.get('type', 'unknown')}")
    except Exception as e:
        logger.error(f"Failed to publish event to session {session_id}: {e}")
        # Don't raise - allow endpoints to continue working without Redis
        # Real-time updates will be unavailable, but core functionality remains
        pass


async def subscribe(session_id: int) -> AsyncIterator[str]:
    """
    Subscribe to Redis channel for a session and yield messages.
    Auto-reconnects on connection drops. Waits if Redis is unavailable.

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
            if r is None:
                # Redis unavailable - wait and retry
                logger.debug(
                    f"Redis unavailable, waiting before retry for session {session_id}"
                )
                await asyncio.sleep(5)
                continue

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


# =================================
# DM-specific pub/sub functions
# =================================


def channel_for_dm_user(user_id: int) -> str:
    """Generate Redis channel name for a user's DM stream."""
    return f"dm:user:{user_id}"


def channel_for_dm_conversation(conversation_id: str) -> str:
    """Generate Redis channel name for a conversation."""
    return f"dm:conversation:{conversation_id}"


async def publish_dm_message(conversation_id: str, user_id: int, event: dict) -> None:
    """
    Publish a DM message event to Redis channels.
    Publishes to both per-user and per-conversation channels.
    Silently fails if Redis is unavailable.

    Args:
        conversation_id: Conversation UUID (string)
        user_id: Recipient user ID (integer)
        event: Event dictionary to publish (will be JSON-encoded)
    """
    try:
        r = get_redis()
        if r is None:
            logger.debug(
                "Redis unavailable, skipping DM publish for conversation "
                f"{conversation_id}"
            )
            return

        # Publish to per-user channel
        user_channel = channel_for_dm_user(user_id)
        await r.publish(user_channel, json.dumps(event))
        logger.debug(
            f"Published DM event to {user_channel}: {event.get('type', 'unknown')}"
        )

        # Publish to per-conversation channel
        conv_channel = channel_for_dm_conversation(conversation_id)
        await r.publish(conv_channel, json.dumps(event))
        logger.debug(
            f"Published DM event to {conv_channel}: {event.get('type', 'unknown')}"
        )
    except Exception as e:
        logger.error(f"Failed to publish DM event: {e}")
        pass


async def subscribe_dm_user(user_id: int) -> AsyncIterator[str]:
    """
    Subscribe to Redis channel for a user's DM stream.
    Auto-reconnects on connection drops. Waits if Redis is unavailable.

    Args:
        user_id: User ID (integer)

    Yields:
        JSON strings from Redis pub/sub
    """
    channel = channel_for_dm_user(user_id)
    psub = None

    while True:
        try:
            r = get_redis()
            if r is None:
                logger.debug(
                    f"Redis unavailable, waiting before retry for user {user_id}"
                )
                await asyncio.sleep(5)
                continue

            psub = r.pubsub()
            await psub.subscribe(channel)
            logger.debug(f"Subscribed to DM user channel: {channel}")

            async for msg in psub.listen():
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                yield msg["data"]

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in DM user subscription for user {user_id}: {e}")
            await asyncio.sleep(1)
            continue
        finally:
            if psub:
                try:
                    await psub.unsubscribe(channel)
                    await psub.close()
                    logger.debug(f"Unsubscribed from DM user channel: {channel}")
                except Exception:
                    pass
                psub = None

            try:
                import sys

                exc_type = sys.exc_info()[0]
                if exc_type == asyncio.CancelledError:
                    break
            except Exception:
                pass


async def subscribe_dm_conversation(conversation_id: str) -> AsyncIterator[str]:
    """
    Subscribe to Redis channel for a conversation.
    Auto-reconnects on connection drops. Waits if Redis is unavailable.

    Args:
        conversation_id: Conversation UUID (string)

    Yields:
        JSON strings from Redis pub/sub
    """
    channel = channel_for_dm_conversation(conversation_id)
    psub = None

    while True:
        try:
            r = get_redis()
            if r is None:
                logger.debug(
                    "Redis unavailable, waiting before retry for conversation "
                    f"{conversation_id}"
                )
                await asyncio.sleep(5)
                continue

            psub = r.pubsub()
            await psub.subscribe(channel)
            logger.debug(f"Subscribed to DM conversation channel: {channel}")

            async for msg in psub.listen():
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                yield msg["data"]

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(
                f"Error in DM conversation subscription for {conversation_id}: {e}"
            )
            await asyncio.sleep(1)
            continue
        finally:
            if psub:
                try:
                    await psub.unsubscribe(channel)
                    await psub.close()
                    logger.debug(
                        f"Unsubscribed from DM conversation channel: {channel}"
                    )
                except Exception:
                    pass
                psub = None

            try:
                import sys

                exc_type = sys.exc_info()[0]
                if exc_type == asyncio.CancelledError:
                    break
            except Exception:
                pass


# =================================
# Group-specific pub/sub functions
# =================================


def channel_for_group(group_id: str) -> str:
    """Generate Redis channel name for a group."""
    return f"grp:{group_id}"


async def publish_group_message(group_id: str, event: dict) -> None:
    """
    Publish a group message event to Redis channel.
    Silently fails if Redis is unavailable.

    Args:
        group_id: Group UUID (string)
        event: Event dictionary to publish (will be JSON-encoded)
    """
    try:
        r = get_redis()
        if r is None:
            logger.debug(
                f"Redis unavailable, skipping group publish for group {group_id}"
            )
            return

        channel = channel_for_group(group_id)
        await r.publish(channel, json.dumps(event))
        logger.debug(
            f"Published group event to {channel}: {event.get('type', 'unknown')}"
        )
    except Exception as e:
        logger.error(f"Failed to publish group event: {e}")
        pass


async def subscribe_group(group_id: str) -> AsyncIterator[str]:
    """
    Subscribe to Redis channel for a group.
    Auto-reconnects on connection drops. Waits if Redis is unavailable.

    Args:
        group_id: Group UUID (string)

    Yields:
        JSON strings from Redis pub/sub
    """
    channel = channel_for_group(group_id)
    psub = None

    while True:
        try:
            r = get_redis()
            if r is None:
                logger.debug(
                    f"Redis unavailable, waiting before retry for group {group_id}"
                )
                await asyncio.sleep(5)
                continue

            psub = r.pubsub()
            await psub.subscribe(channel)
            logger.debug(f"Subscribed to group channel: {channel}")

            async for msg in psub.listen():
                if msg is None:
                    continue
                if msg.get("type") != "message":
                    continue
                yield msg["data"]

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in group subscription for {group_id}: {e}")
            await asyncio.sleep(1)
            continue
        finally:
            if psub:
                try:
                    await psub.unsubscribe(channel)
                    await psub.close()
                    logger.debug(f"Unsubscribed from group channel: {channel}")
                except Exception:
                    pass
                psub = None

            try:
                import sys

                exc_type = sys.exc_info()[0]
                if exc_type == asyncio.CancelledError:
                    break
            except Exception:
                pass
