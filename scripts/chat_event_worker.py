import asyncio
import json
import logging
import signal
from typing import Any, Dict, Optional

from routers.global_chat import (
    publish_to_pusher_global,
    send_push_for_global_chat_sync
)
from routers.private_chat import (
    publish_to_pusher_private,
    send_push_if_needed_sync
)
from routers.trivia_live_chat import (
    publish_to_pusher_trivia_live,
    send_push_for_trivia_live_chat_sync
)
from utils.chat_redis import get_chat_redis, CHAT_EVENT_QUEUE_KEY

logger = logging.getLogger("chat_event_worker")
logging.basicConfig(level=logging.INFO)


async def _run_blocking(func, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: func(*args))


async def handle_global_message(payload: Dict[str, Any]) -> None:
    if "pusher_args" in payload:
        await _run_blocking(publish_to_pusher_global, **payload["pusher_args"])
    if "push_args" in payload:
        await _run_blocking(send_push_for_global_chat_sync, **payload["push_args"])


async def handle_private_message(payload: Dict[str, Any]) -> None:
    if "pusher_args" in payload:
        await _run_blocking(publish_to_pusher_private, **payload["pusher_args"])
    if "push_args" in payload:
        await _run_blocking(send_push_if_needed_sync, **payload["push_args"])


async def handle_trivia_message(payload: Dict[str, Any]) -> None:
    if "pusher_args" in payload:
        await _run_blocking(publish_to_pusher_trivia_live, **payload["pusher_args"])
    if "push_args" in payload:
        await _run_blocking(send_push_for_trivia_live_chat_sync, **payload["push_args"])


EVENT_HANDLERS = {
    "global_message": handle_global_message,
    "private_message": handle_private_message,
    "trivia_message": handle_trivia_message,
}


async def worker_loop(stop_event: asyncio.Event):
    redis = await get_chat_redis()
    if not redis:
        raise RuntimeError("Unable to initialize Redis client for chat worker")
    
    logger.info("Chat event worker started. Listening for events...")
    while not stop_event.is_set():
        try:
            item = await redis.blpop(CHAT_EVENT_QUEUE_KEY, timeout=5)
            if not item:
                continue
            _, raw_event = item
            try:
                event_data = json.loads(raw_event)
            except json.JSONDecodeError:
                logger.warning("Discarded malformed chat event: %s", raw_event)
                continue
            
            event_type = event_data.get("type")
            payload = event_data.get("payload", {})
            handler = EVENT_HANDLERS.get(event_type)
            if not handler:
                logger.warning("No handler for chat event type '%s'", event_type)
                continue
            
            await handler(payload)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.exception("Error processing chat event: %s", exc)
            await asyncio.sleep(1)
    
    logger.info("Chat event worker shutting down")


def main():
    stop_event = asyncio.Event()
    
    def _handle_signal(signum, frame):
        logger.info("Received signal %s, stopping worker...", signum)
        stop_event.set()
    
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    
    asyncio.run(worker_loop(stop_event))


if __name__ == "__main__":
    main()
