from __future__ import annotations

import logging
import time as _time
from collections import deque
from collections.abc import Hashable
from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Bucket:
    attempts: deque[float] = field(default_factory=deque)


class AuthRateLimiter:
    """In-memory sliding-window rate limiter. Works as a single-instance fallback."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, Hashable], _Bucket] = {}
        self._lock = Lock()

    def check(self, scope: str, key: Hashable, *, limit: int, window_seconds: int) -> None:
        now = monotonic()
        with self._lock:
            bucket = self._buckets.setdefault((scope, key), _Bucket())
            self._trim(bucket, now, window_seconds)
            if len(bucket.attempts) >= limit:
                raise ValueError("rate_limited")

    def hit(self, scope: str, key: Hashable, *, window_seconds: int) -> None:
        now = monotonic()
        with self._lock:
            bucket = self._buckets.setdefault((scope, key), _Bucket())
            self._trim(bucket, now, window_seconds)
            bucket.attempts.append(now)

    def clear(self, scope: str, key: Hashable) -> None:
        with self._lock:
            self._buckets.pop((scope, key), None)

    @staticmethod
    def _trim(bucket: _Bucket, now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        while bucket.attempts and bucket.attempts[0] <= cutoff:
            bucket.attempts.popleft()


class RedisRateLimiter(AuthRateLimiter):
    """Distributed sliding-window rate limiter backed by Redis sorted sets.

    Falls back to in-memory limiter (parent class) when Redis is unavailable.
    Uses a synchronous Redis client since check/hit are called from sync context.
    """

    def __init__(self, redis_url: str) -> None:
        super().__init__()
        self._sync_client = None
        try:
            import redis

            self._sync_client = redis.from_url(
                redis_url, decode_responses=False, socket_connect_timeout=2, socket_timeout=1,
            )
            self._sync_client.ping()
        except Exception:
            logger.warning("redis_rate_limiter.connect_failed, falling back to in-memory")
            self._sync_client = None

    def _redis_key(self, scope: str, key: Hashable) -> str:
        return f"ratelimit:{scope}:{key}"

    def check(self, scope: str, key: Hashable, *, limit: int, window_seconds: int) -> None:
        if self._sync_client is None:
            return super().check(scope, key, limit=limit, window_seconds=window_seconds)
        try:
            rkey = self._redis_key(scope, key)
            now = _time.time()
            cutoff = now - window_seconds
            pipe = self._sync_client.pipeline()
            pipe.zremrangebyscore(rkey, 0, cutoff)
            pipe.zcard(rkey)
            results = pipe.execute()
            count = results[1]
            if count >= limit:
                raise ValueError("rate_limited")
        except ValueError:
            raise
        except Exception:
            return super().check(scope, key, limit=limit, window_seconds=window_seconds)

    def hit(self, scope: str, key: Hashable, *, window_seconds: int) -> None:
        if self._sync_client is None:
            return super().hit(scope, key, window_seconds=window_seconds)
        try:
            rkey = self._redis_key(scope, key)
            now = _time.time()
            cutoff = now - window_seconds
            pipe = self._sync_client.pipeline()
            pipe.zremrangebyscore(rkey, 0, cutoff)
            pipe.zadd(rkey, {str(now): now})
            pipe.expire(rkey, window_seconds + 1)
            pipe.execute()
        except Exception:
            return super().hit(scope, key, window_seconds=window_seconds)

    def clear(self, scope: str, key: Hashable) -> None:
        super().clear(scope, key)
        if self._sync_client is not None:
            try:
                self._sync_client.delete(self._redis_key(scope, key))
            except Exception:
                pass
