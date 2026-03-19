from __future__ import annotations

import math
from typing import Any, Iterable

from openai import AsyncOpenAI, BadRequestError, OpenAI

from app.platform.config import Settings, get_settings


class _EmbeddingClientBase:
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


class SyncEmbeddingClient(_EmbeddingClientBase):
    def __init__(self, settings: Settings | None = None):
        super().__init__(settings)
        self._client: OpenAI | None = None
        api_key = self.settings.embedding_api_key
        if api_key or self._allows_keyless_local_base():
            self._client = OpenAI(
                api_key=api_key or "ollama",
                base_url=self.settings.embedding_base_url,
            )

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
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
            vectors.extend(self._embed_batch(batch))
        if len(texts_list) == 1:
            self._put_query_cache(texts_list[0], vectors[0])
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            raise RuntimeError("Embeddings provider is not configured.")
        try:
            request_kwargs: dict[str, Any] = {
                "model": self.settings.embedding_model,
                "input": texts,
            }
            extra_body = self._embedding_extra_body()
            if extra_body:
                request_kwargs["extra_body"] = extra_body
            response = self._client.embeddings.create(**request_kwargs)
            return [list(item.embedding) for item in response.data]
        except BadRequestError as exc:
            if not self._is_context_error(exc):
                raise
            return [self._embed_text_no_truncation(text) for text in texts]

    def _embed_text_no_truncation(self, text: str) -> list[float]:
        if self._client is None:
            raise RuntimeError("Embeddings provider is not configured.")
        try:
            request_kwargs: dict[str, Any] = {
                "model": self.settings.embedding_model,
                "input": [text],
            }
            extra_body = self._embedding_extra_body()
            if extra_body:
                request_kwargs["extra_body"] = extra_body
            response = self._client.embeddings.create(**request_kwargs)
            return list(response.data[0].embedding)
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
            left_vec = self._embed_text_no_truncation(left)
            right_vec = self._embed_text_no_truncation(right)
            return self._merge_vectors(
                [
                    (left_vec, max(1, split_at)),
                    (right_vec, max(1, len(words) - split_at)),
                ]
            )


class AsyncEmbeddingClient(_EmbeddingClientBase):
    def __init__(self, settings: Settings | None = None):
        super().__init__(settings)
        self._client: AsyncOpenAI | None = None
        api_key = self.settings.embedding_api_key
        if api_key or self._allows_keyless_local_base():
            self._client = AsyncOpenAI(
                api_key=api_key or "ollama",
                base_url=self.settings.embedding_base_url,
            )

    async def embed_query(self, text: str) -> list[float]:
        cached = self._query_cache.get(text)
        if cached is not None:
            return cached
        vectors = await self.embed_texts([text])
        self._put_query_cache(text, vectors[0])
        return vectors[0]

    async def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
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
            vectors.extend(await self._embed_batch(batch))
        if len(texts_list) == 1:
            self._put_query_cache(texts_list[0], vectors[0])
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            raise RuntimeError("Embeddings provider is not configured.")
        try:
            request_kwargs: dict[str, Any] = {
                "model": self.settings.embedding_model,
                "input": texts,
            }
            extra_body = self._embedding_extra_body()
            if extra_body:
                request_kwargs["extra_body"] = extra_body
            response = await self._client.embeddings.create(**request_kwargs)
            return [list(item.embedding) for item in response.data]
        except BadRequestError as exc:
            if not self._is_context_error(exc):
                raise
            return [await self._embed_text_no_truncation(text) for text in texts]

    async def _embed_text_no_truncation(self, text: str) -> list[float]:
        if self._client is None:
            raise RuntimeError("Embeddings provider is not configured.")
        try:
            request_kwargs: dict[str, Any] = {
                "model": self.settings.embedding_model,
                "input": [text],
            }
            extra_body = self._embedding_extra_body()
            if extra_body:
                request_kwargs["extra_body"] = extra_body
            response = await self._client.embeddings.create(**request_kwargs)
            return list(response.data[0].embedding)
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
            left_vec = await self._embed_text_no_truncation(left)
            right_vec = await self._embed_text_no_truncation(right)
            return self._merge_vectors(
                [
                    (left_vec, max(1, split_at)),
                    (right_vec, max(1, len(words) - split_at)),
                ]
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()


EmbeddingClient = SyncEmbeddingClient
