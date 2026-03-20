"""Redis-based rate limiter for FastAPI endpoints.

Usage as a dependency::

    from app.middleware.rate_limit import RateLimit

    @router.post("/verify")
    async def verify(
        user = Depends(get_current_user),
        _rl = Depends(RateLimit(prefix="iap_verify", max_requests=10, window_seconds=60)),
    ):
        ...
"""

from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status

from core.config import REDIS_URL

logger = logging.getLogger(__name__)

# Shared connection pool — created once, reused across all requests.
_pool: Optional[aioredis.ConnectionPool] = None


def _get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)
    return _pool


class RateLimit:
    """FastAPI dependency that enforces per-user rate limiting via Redis."""

    def __init__(
        self,
        prefix: str,
        max_requests: int,
        window_seconds: int,
        *,
        use_ip_fallback: bool = True,
    ):
        self.prefix = prefix
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.use_ip_fallback = use_ip_fallback

    async def __call__(self, request: Request) -> None:
        # Determine rate limit key
        user = getattr(request.state, "user", None)
        if user and hasattr(user, "account_id"):
            key_id = f"user:{user.account_id}"
        elif self.use_ip_fallback:
            key_id = f"ip:{request.client.host}" if request.client else "ip:unknown"
        else:
            # No user and no fallback — skip rate limiting
            return

        key = f"ratelimit:{self.prefix}:{key_id}"
        window = self.window_seconds

        try:
            r = aioredis.Redis(connection_pool=_get_pool())
            current = await r.incr(key)
            if current == 1:
                await r.expire(key, window)

            if current > self.max_requests:
                ttl = await r.ttl(key)
                logger.warning(
                    "Rate limit exceeded: key=%s count=%d limit=%d",
                    key, current, self.max_requests,
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded. Try again in {ttl} seconds.",
                    headers={"Retry-After": str(max(ttl, 1))},
                )
        except HTTPException:
            raise
        except Exception as exc:
            # If Redis is down, allow the request through (fail-open)
            logger.error("Rate limiter Redis error: %s", exc)
