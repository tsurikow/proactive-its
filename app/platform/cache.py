"""Redis cache wrapper with graceful degradation."""

from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class RedisCache:
    """Thin async Redis wrapper. All methods swallow connection errors so callers
    never need to handle Redis outages — they just get cache misses."""

    def __init__(self, url: str, default_ttl: int = 86400) -> None:
        self._url = url
        self._default_ttl = default_ttl
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        try:
            self._client = aioredis.from_url(
                self._url,
                decode_responses=False,
                socket_connect_timeout=3,
                socket_timeout=2,
            )
            await self._client.ping()
            logger.info("redis.connected", extra={"url": self._url})
        except Exception:
            logger.warning("redis.connect_failed", extra={"url": self._url})
            self._client = None

    @property
    def available(self) -> bool:
        return self._client is not None

    async def get(self, key: str) -> bytes | None:
        if self._client is None:
            return None
        try:
            return await self._client.get(key)
        except Exception:
            return None

    async def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        if self._client is None:
            return
        try:
            await self._client.set(key, value, ex=ttl or self._default_ttl)
        except Exception:
            pass

    async def publish(self, channel: str, message: bytes | str) -> None:
        if self._client is None:
            return
        try:
            await self._client.publish(channel, message)
        except Exception:
            pass

    async def subscribe(self, channel: str) -> aioredis.client.PubSub | None:
        if self._client is None:
            return None
        try:
            pubsub = self._client.pubsub()
            await pubsub.subscribe(channel)
            return pubsub
        except Exception:
            return None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def pipeline(self) -> Any:
        if self._client is None:
            return None
        return self._client.pipeline()


__all__ = ["RedisCache"]
