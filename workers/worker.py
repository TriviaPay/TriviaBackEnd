from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict

import redis  # type: ignore

from core.config import REDIS_URL
from core.logging import configure_logging

from .handlers import handle_task

logger = logging.getLogger("worker")


def main() -> None:
    configure_logging(
        environment=os.getenv("ENVIRONMENT", "development"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
    queue = os.getenv("WORKER_QUEUE", "tasks")
    poll_timeout = int(os.getenv("WORKER_POLL_TIMEOUT_SECONDS", "5"))
    r = redis.Redis.from_url(REDIS_URL)
    logger.info("Worker started | queue=%s", queue)

    while True:
        item = r.blpop(queue, timeout=poll_timeout)
        if not item:
            continue
        _queue_name, raw = item
        try:
            msg: Dict[str, Any] = json.loads(raw)
            name = msg.get("name")
            payload = msg.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            handle_task(str(name), payload)
        except Exception as exc:
            logger.exception("Worker task failed: %s", exc)
            time.sleep(0.25)


if __name__ == "__main__":
    main()

