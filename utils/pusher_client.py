import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

import pusher

from config import (
    PUSHER_APP_ID,
    PUSHER_CLUSTER,
    PUSHER_ENABLED,
    PUSHER_KEY,
    PUSHER_SECRET,
)

logger = logging.getLogger(__name__)

_pusher_client: Optional[pusher.Pusher] = None
_executor = ThreadPoolExecutor(max_workers=5)


def get_pusher_client() -> Optional[pusher.Pusher]:
    """Get or create Pusher client instance"""
    global _pusher_client

    if not PUSHER_ENABLED:
        return None

    if _pusher_client is None:
        try:
            if not all([PUSHER_APP_ID, PUSHER_KEY, PUSHER_SECRET]):
                logger.warning("Pusher credentials not fully configured")
                return None

            _pusher_client = pusher.Pusher(
                app_id=PUSHER_APP_ID,
                key=PUSHER_KEY,
                secret=PUSHER_SECRET,
                cluster=PUSHER_CLUSTER,
                ssl=True,
            )
            logger.info("Pusher client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Pusher: {e}")
            return None

    return _pusher_client


async def publish_chat_message_async(
    channel: str, event: str, data: Dict[str, Any]
) -> bool:
    """
    Publish message to Pusher channel asynchronously.
    Uses thread pool to avoid blocking the event loop.
    """
    client = get_pusher_client()
    if not client:
        logger.debug("Pusher not available, message not published")
        return False

    def _publish():
        try:
            client.trigger(channel, event, data)
            return True
        except Exception as e:
            logger.error(f"Failed to publish to Pusher channel {channel}: {e}")
            return False

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_executor, _publish)
        return result
    except Exception as e:
        logger.error(f"Error in async Pusher publish: {e}")
        return False


def publish_chat_message_sync(channel: str, event: str, data: Dict[str, Any]) -> bool:
    """
    Synchronous version for use in background tasks.
    Background tasks run in a separate thread, so sync is fine.
    """
    client = get_pusher_client()
    if not client:
        logger.debug("Pusher not available, message not published")
        return False

    try:
        client.trigger(channel, event, data)
        return True
    except Exception as e:
        logger.error(f"Failed to publish to Pusher channel {channel}: {e}")
        return False
