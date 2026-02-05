from __future__ import annotations

import hashlib
import math
from typing import Iterable

from openai import BadRequestError, OpenAI

from app.core.config import Settings, get_settings


class EmbeddingClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: OpenAI | None = None
        if self.settings.embedding_api_key:
            self._client = OpenAI(
                api_key=self.settings.embedding_api_key,
                base_url=self.settings.embedding_base_url,
            )

    @property
    def is_fake(self) -> bool:
        return self._client is None

    def embed_texts(self, texts: Iterable[str]) -> list[list[float]]:
        texts_list = list(texts)
        if not texts_list:
            return []
        if self._client is None:
            return [self._fake_embedding(text) for text in texts_list]

        clipped = [self._clip_to_token_budget(text) for text in texts_list]
        batch_size = max(1, int(self.settings.embedding_batch_size))
        vectors: list[list[float]] = []
        for i in range(0, len(clipped), batch_size):
            batch = clipped[i : i + batch_size]
            vectors.extend(self._embed_batch_with_fallback(batch))
        return vectors

    def _embed_batch_with_fallback(self, texts: list[str]) -> list[list[float]]:
        if self._client is None:
            return [self._fake_embedding(text) for text in texts]
        try:
            response = self._client.embeddings.create(model=self.settings.embedding_model, input=texts)
            return [list(item.embedding) for item in response.data]
        except BadRequestError as exc:
            if not self._is_context_error(exc):
                raise
            return [self._embed_single_with_backoff(text) for text in texts]

    def _embed_single_with_backoff(self, text: str) -> list[float]:
        if self._client is None:
            return self._fake_embedding(text)
        candidate = text
        for _ in range(6):
            try:
                response = self._client.embeddings.create(
                    model=self.settings.embedding_model,
                    input=[candidate],
                )
                return list(response.data[0].embedding)
            except BadRequestError as exc:
                if not self._is_context_error(exc):
                    raise
                words = candidate.split()
                if len(words) <= 64:
                    raise
                candidate = " ".join(words[: int(len(words) * 0.75)])
        raise RuntimeError("Failed to embed text after repeated context-length truncation attempts")

    @staticmethod
    def _is_context_error(exc: BadRequestError) -> bool:
        msg = str(exc).lower()
        return "context length" in msg or "input length exceeds" in msg

    def _clip_to_token_budget(self, text: str) -> str:
        max_tokens = max(64, int(self.settings.embedding_max_input_tokens))
        words = text.split()
        max_words = max(64, int(max_tokens / 1.25))
        if len(words) <= max_words:
            return text
        return " ".join(words[:max_words])

    def _fake_embedding(self, text: str) -> list[float]:
        dim = self.settings.fake_embedding_dim
        values: list[float] = []
        seed = text.encode("utf-8")
        idx = 0
        while len(values) < dim:
            digest = hashlib.sha256(seed + idx.to_bytes(4, "big")).digest()
            idx += 1
            for i in range(0, len(digest), 4):
                if len(values) >= dim:
                    break
                chunk = digest[i : i + 4]
                num = int.from_bytes(chunk, "big", signed=False)
                values.append((num % 2000) / 1000.0 - 1.0)
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]
