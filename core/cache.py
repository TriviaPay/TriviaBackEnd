"""Lightweight cache utilities (in-memory TTL).

Used for safe hot-read endpoints (GET-like) to reduce DB load and latency without
introducing a hard dependency on Redis.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable, Dict, Optional, Tuple, TypeVar

T = TypeVar("T")


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self, *, max_keys: int = 10_000):
        self._max_keys = max_keys
        self._lock = Lock()
        self._data: Dict[str, _Entry] = {}

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                return None
            if entry.expires_at <= now:
                self._data.pop(key, None)
                return None
            return entry.value

    def set(self, key: str, value: Any, *, ttl_seconds: float) -> None:
        expires_at = time.time() + float(ttl_seconds)
        with self._lock:
            if len(self._data) >= self._max_keys:
                # Simple eviction: drop an arbitrary key.
                self._data.pop(next(iter(self._data)), None)
            self._data[key] = _Entry(value=value, expires_at=expires_at)

    def get_or_set(self, key: str, *, ttl_seconds: float, factory: Callable[[], T]) -> T:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = factory()
        self.set(key, value, ttl_seconds=ttl_seconds)
        return value


default_cache = TTLCache()

