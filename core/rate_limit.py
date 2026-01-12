"""Rate limiting helpers (Redis preferred, in-memory fallback)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque, Dict, Optional, Tuple

import redis  # type: ignore

from core.config import REDIS_URL


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int


class RateLimiter:
    def __init__(self):
        self._lock = Lock()
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, *, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        limit = int(limit)
        window_seconds = int(window_seconds)
        if limit <= 0 or window_seconds <= 0:
            return RateLimitResult(allowed=True, retry_after_seconds=0)

        # Try Redis first.
        try:
            r = redis.Redis.from_url(REDIS_URL)
            # Atomic counter with TTL.
            pipe = r.pipeline()
            pipe.incr(key, 1)
            pipe.ttl(key)
            current, ttl = pipe.execute()
            if ttl == -1:
                r.expire(key, window_seconds)
                ttl = window_seconds
            if int(current) <= limit:
                return RateLimitResult(allowed=True, retry_after_seconds=0)
            retry_after = int(ttl if ttl and ttl > 0 else window_seconds)
            return RateLimitResult(allowed=False, retry_after_seconds=max(1, retry_after))
        except Exception:
            pass

        # In-memory sliding window fallback.
        now = time.time()
        with self._lock:
            bucket = self._buckets[key]
            cutoff = now - window_seconds
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) < limit:
                bucket.append(now)
                return RateLimitResult(allowed=True, retry_after_seconds=0)
            retry_after = int((bucket[0] + window_seconds) - now)
            return RateLimitResult(allowed=False, retry_after_seconds=max(1, retry_after))


default_rate_limiter = RateLimiter()

