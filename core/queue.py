"""Minimal Redis-backed task queue (optional).

This is intentionally tiny: a Redis list + JSON payloads. It avoids adding Celery/RQ
dependencies while still enabling background offloading.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import redis  # type: ignore

from core.config import REDIS_URL


DEFAULT_QUEUE = "tasks"


def enqueue_task(*, name: str, payload: Optional[Dict[str, Any]] = None, queue: str = DEFAULT_QUEUE) -> None:
    r = redis.Redis.from_url(REDIS_URL)
    body = {"name": name, "payload": payload or {}}
    r.rpush(queue, json.dumps(body))

