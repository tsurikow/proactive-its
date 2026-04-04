from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from typing import Any, Iterable

import msgpack
from openai import AsyncOpenAI, BadRequestError

from app.platform.cache import RedisCache
from app.platform.config import Settings, get_settings

logger = logging.getLogger(__name__)


class _EmbeddingClientBase:
    empty_response_retry_attempts = 2
    empty_response_retry_delay_seconds = 0.5
    single_text_empty_response_retry_attempts = 5
    single_text_empty_response_retry_delay_seconds = 1.0

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._query_cache: dict[str, list[float]] = {}
        self._query_cache_order: list[str] = []
        self._query_cache_max = 512

    def _allows_keyless_local_base(self) -> bool:
        base = (self.settings.embedding_base_url or "").lower()
        return any(host in base for host in ("localhost", "127.0.0.1", "host.docker.internal"))

    def _put_query_cache(self, text: str, vector: list[float]) -> None:
        if text in self._query_cache:
            self._query_cache[text] = vector
            return
        self._query_cache[text] = vector
        self._query_cache_order.append(text)
        while len(self._query_cache_order) > self._query_cache_max:
            evicted = self._query_cache_order.pop(0)
            self._query_cache.pop(evicted, None)

    def _embedding_extra_body(self) -> dict[str, Any] | None:
        num_ctx = self.settings.embedding_num_ctx
        if num_ctx is None:
            return None
        base_url = (self.settings.embedding_base_url or "").lower()
        if "11434" not in base_url and "ollama" not in base_url:
            return None
        return {"options": {"num_ctx": int(num_ctx)}}

    @staticmethod
    def _is_context_error(exc: BadRequestError) -> bool:
        msg = str(exc).lower()
        return "context length" in msg or "input length exceeds" in msg

    @staticmethod
    def _merge_vectors(parts: list[tuple[list[float], int]]) -> list[float]:
        if not parts:
            raise RuntimeError("Cannot merge empty embedding vector parts")
        dim = len(parts[0][0])
        merged = [0.0] * dim
        total_weight = 0
        for vector, weight in parts:
            total_weight += weight
            for idx in range(dim):
                merged[idx] += vector[idx] * weight
        if total_weight == 0:
            total_weight = 1
        merged = [value / total_weight for value in merged]
        norm = math.sqrt(sum(value * value for value in merged)) or 1.0
        return [value / norm for value in merged]

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, int(len(text.split()) * 1.25))

    @staticmethod
    def _response_vectors(response: Any) -> list[list[float]]:
        data = getattr(response, "data", None) or []
        vectors = [list(item.embedding) for item in data if getattr(item, "embedding", None)]
        if not vectors:
            raise ValueError("No embedding data received")
        return vectors

    @staticmethod
    def _text_lengths(texts: list[str]) -> list[int]:
        return [len(text) for text in texts]

    @staticmethod
    def _item_labels(texts: list[str], item_labels: list[str] | None = None) -> list[str]:
        if item_labels is None:
            return [f"item[{idx}]" for idx in range(len(texts))]
        return item_labels


class AsyncEmbeddingClient(_EmbeddingClientBase):
    def __init__(self, settings: Settings | None = None, redis_cache: RedisCache | None = None):
        super().__init__(settings)
        self._client: AsyncOpenAI | None = None
        self._redis_cache = redis_cache
        api_key = self.settings.embedding_api_key
        if api_key or self._allows_keyless_local_base():
            self._client = AsyncOpenAI(
                api_key=api_key or "ollama",
                base_url=self.settings.embedding_base_url,
            )

    def _redis_key(self, text: str) -> str:
        h = hashlib.sha256(text.encode()).hexdigest()[:16]
        return f"emb:{self.settings.embedding_model}:{h}"

    async def _get_from_redis(self, text: str) -> list[float] | None:
        if self._redis_cache is None or not self._redis_cache.available:
            return None
        raw = await self._redis_cache.get(self._redis_key(text))
        if raw is None:
            return None
        try:
            return list(msgpack.unpackb(raw, raw=False))
        except Exception:
            return None

    async def _put_to_redis(self, text: str, vector: list[float]) -> None:
        if self._redis_cache is None or not self._redis_cache.available:
            return
        try:
            await self._redis_cache.set(self._redis_key(text), msgpack.packb(vector))
        except Exception:
            pass

    async def embed_query(self, text: str) -> list[float]:
        # L1: in-memory
        cached = self._query_cache.get(text)
        if cached is not None:
            return cached
        # L2: Redis
        redis_cached = await self._get_from_redis(text)
        if redis_cached is not None:
            self._put_query_cache(text, redis_cached)
            return redis_cached
        # Compute
        vectors = await self.embed_texts([text])
        self._put_query_cache(text, vectors[0])
        await self._put_to_redis(text, vectors[0])
        return vectors[0]

    async def embed_texts(
        self,
        texts: Iterable[str],
        *,
        batch_label: str | None = None,
        item_labels: list[str] | None = None,
    ) -> list[list[float]]:
        texts_list = list(texts)
        if not texts_list:
            return []
        if self._client is None:
            raise RuntimeError(
                "Embeddings provider is not configured. Set EMBEDDING_BASE_URL/EMBEDDING_MODEL "
                "and provide EMBEDDING_API_KEY when required."
            )
        if len(texts_list) == 1:
            cached = self._query_cache.get(texts_list[0])
            if cached is not None:
                return [cached]

        batch_size = max(1, int(self.settings.embedding_batch_size))
        vectors: list[list[float]] = []
        for i in range(0, len(texts_list), batch_size):
            batch = texts_list[i : i + batch_size]
            batch_item_labels = None if item_labels is None else item_labels[i : i + batch_size]
            vectors.extend(
                await self._embed_batch(
                    batch,
                    batch_label=batch_label,
                    item_labels=batch_item_labels,
                )
            )
        if len(texts_list) == 1:
            self._put_query_cache(texts_list[0], vectors[0])
        return vectors

    async def _embed_batch(
        self,
        texts: list[str],
        *,
        batch_label: str | None = None,
        item_labels: list[str] | None = None,
    ) -> list[list[float]]:
        if self._client is None:
            raise RuntimeError("Embeddings provider is not configured.")
        try:
            return await self._embed_batch_with_empty_retry(texts)
        except BadRequestError as exc:
            if not self._is_context_error(exc):
                raise
            logger.warning(
                "Embedding batch hit context fallback: batch_label=%s batch_size=%d text_lengths=%s",
                batch_label,
                len(texts),
                self._text_lengths(texts),
            )
            labels = self._item_labels(texts, item_labels)
            return [
                await self._embed_text_no_truncation(
                    text,
                    item_label=label,
                    batch_label=batch_label,
                )
                for text, label in zip(texts, labels, strict=True)
            ]
        except ValueError as exc:
            logger.warning(
                "Embedding batch failed after retries: batch_label=%s batch_size=%d text_lengths=%s error=%s",
                batch_label,
                len(texts),
                self._text_lengths(texts),
                exc,
            )
            labels = self._item_labels(texts, item_labels)
            return [
                await self._embed_text_no_truncation(
                    text,
                    item_label=label,
                    batch_label=batch_label,
                )
                for text, label in zip(texts, labels, strict=True)
            ]

    async def _embed_batch_with_empty_retry(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            raise RuntimeError("Embeddings provider is not configured.")
        last_exc: ValueError | None = None
        for attempt in range(self.empty_response_retry_attempts + 1):
            try:
                request_kwargs: dict[str, Any] = {
                    "model": self.settings.embedding_model,
                    "input": texts,
                }
                extra_body = self._embedding_extra_body()
                if extra_body:
                    request_kwargs["extra_body"] = extra_body
                response = await self._client.embeddings.create(**request_kwargs)
                vectors = self._response_vectors(response)
                if len(vectors) != len(texts):
                    raise ValueError(
                        f"Expected {len(texts)} embedding vectors, received {len(vectors)}."
                    )
                return vectors
            except ValueError as exc:
                last_exc = exc
                if attempt >= self.empty_response_retry_attempts:
                    break
                await asyncio.sleep(self.empty_response_retry_delay_seconds * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        raise ValueError("No embedding data received")

    async def _embed_text_no_truncation(
        self,
        text: str,
        *,
        item_label: str | None = None,
        batch_label: str | None = None,
    ) -> list[float]:
        if self._client is None:
            raise RuntimeError("Embeddings provider is not configured.")
        try:
            last_exc: ValueError | None = None
            for attempt in range(self.single_text_empty_response_retry_attempts + 1):
                try:
                    return (await self._embed_batch_with_empty_retry([text]))[0]
                except ValueError as exc:
                    last_exc = exc
                    if attempt >= self.single_text_empty_response_retry_attempts:
                        logger.error(
                            "Embedding single-text failed after retries: batch_label=%s item_label=%s text_length=%d error=%s",
                            batch_label,
                            item_label,
                            len(text),
                            exc,
                        )
                        raise
                    await asyncio.sleep(
                        self.single_text_empty_response_retry_delay_seconds * (attempt + 1)
                    )
            if last_exc is not None:
                raise last_exc
            raise ValueError("No embedding data received")
        except BadRequestError as exc:
            if not self._is_context_error(exc):
                raise
            words = text.split()
            if len(words) < 2:
                raise RuntimeError(
                    "Embedding input exceeds model context length and cannot be split further."
                ) from exc
            split_at = len(words) // 2
            left = " ".join(words[:split_at]).strip()
            right = " ".join(words[split_at:]).strip()
            if not left or not right:
                raise RuntimeError(
                    "Embedding input exceeds model context length and split produced empty text."
                ) from exc
            left_vec = await self._embed_text_no_truncation(
                left,
                item_label=f"{item_label}:left" if item_label else None,
                batch_label=batch_label,
            )
            right_vec = await self._embed_text_no_truncation(
                right,
                item_label=f"{item_label}:right" if item_label else None,
                batch_label=batch_label,
            )
            return self._merge_vectors(
                [
                    (left_vec, max(1, split_at)),
                    (right_vec, max(1, len(words) - split_at)),
                ]
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


