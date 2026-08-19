"""Redis-backed fixed-window rate limiter.

Fail-open by design: if Redis is unreachable the request proceeds and a
warning is logged — availability is preserved at the cost of throttling
(which only matters in a degraded state anyway).
"""
import logging
import time

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class FixedWindowLimiter:
    def __init__(
        self,
        redis: aioredis.Redis | None,
        limit: int,
        window_seconds: int,
        prefix: str,
    ) -> None:
        self._redis = redis
        self.limit = limit
        self.window_seconds = window_seconds
        self.prefix = prefix

    async def check(self, key: str) -> None:
        """Raise HTTPException(429) when the limit for ``key`` is exceeded."""
        if self._redis is None:
            return
        bucket = int(time.time()) // self.window_seconds
        rkey = f"{self.prefix}:{key}:{bucket}"
        try:
            pipe = self._redis.pipeline()
            pipe.incr(rkey)
            pipe.expire(rkey, self.window_seconds + 1)
            count, _ = await pipe.execute()
        except Exception:  # pragma: no cover - redis outage path
            logger.warning("Rate limiter unavailable (redis unreachable): allowing request")
            return

        if count > self.limit:
            retry_after = self.window_seconds - (int(time.time()) % self.window_seconds)
            from fastapi import HTTPException

            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Too many attempts, slow down",
                    "retry_after_seconds": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )
