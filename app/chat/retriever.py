from __future__ import annotations

import math
import time
from typing import Any

from qdrant_client.http import models as qm

from app.chat.utils import chunk_text
from app.platform.config import Settings, get_settings
from app.platform.embeddings import AsyncEmbeddingClient
from app.platform.vector_store import AsyncVectorStore


class DenseRetriever:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        embedder: AsyncEmbeddingClient | None = None,
        store: AsyncVectorStore | None = None,
    ):
        self.settings = settings or get_settings()
        self.embedder = embedder or AsyncEmbeddingClient(self.settings)
        self.store = store or AsyncVectorStore(self.settings)

    async def retrieve(
        self,
        query: str,
        *,
        filters: dict[str, str | None] | None = None,
        context: dict[str, str | None] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        _ = context
        active_filters = filters or {}
        query_filter = self.store.build_filter(active_filters)

        t_embed = time.perf_counter()
        query_vector = await self.embedder.embed_query(query)
        embed_ms = (time.perf_counter() - t_embed) * 1000.0

        t_search = time.perf_counter()
        candidates = await self.store.query_points(
            query_vector,
            top_k=self.settings.rag_top_k_fetch,
            query_filter=query_filter,
            collection_name=self.settings.qdrant_collection,
        )
        search_ms = (time.perf_counter() - t_search) * 1000.0

        t_rerank = time.perf_counter()
        selected = self._select_with_mmr(query_vector, candidates)
        rerank_ms = (time.perf_counter() - t_rerank) * 1000.0

        chunks = [self._map_point(point) for point in selected]
        return chunks, {
            "embed_ms": round(embed_ms, 1),
            "search_ms": round(search_ms, 1),
            "rerank_ms": round(rerank_ms, 1),
            "filtered_by": {k: v for k, v in active_filters.items() if v},
            "top_score": float(candidates[0].score) if candidates else 0.0,
            "retrieval_mode": "dense_mmr",
        }

    def _select_with_mmr(
        self,
        query_vector: list[float],
        candidates: list[qm.ScoredPoint],
    ) -> list[qm.ScoredPoint]:
        if len(candidates) <= self.settings.rag_final_k:
            return candidates

        candidate_vectors: list[list[float] | None] = [self._point_vector(point) for point in candidates]
        if any(vector is None for vector in candidate_vectors):
            return candidates[: self.settings.rag_final_k]

        selected: list[int] = []
        remaining = list(range(len(candidates)))
        lambda_mult = float(self.settings.rag_mmr_lambda_mult)

        while remaining and len(selected) < self.settings.rag_final_k:
            best_idx = remaining[0]
            best_score = float("-inf")
            for idx in remaining:
                vector = candidate_vectors[idx]
                if vector is None:
                    continue
                relevance = _cosine_similarity(query_vector, vector)
                diversity_penalty = 0.0
                if selected:
                    diversity_penalty = max(
                        _cosine_similarity(vector, candidate_vectors[selected_idx] or [])
                        for selected_idx in selected
                    )
                score = (lambda_mult * relevance) - ((1.0 - lambda_mult) * diversity_penalty)
                if score > best_score:
                    best_score = score
                    best_idx = idx
            selected.append(best_idx)
            remaining.remove(best_idx)
        return [candidates[idx] for idx in selected]

    @staticmethod
    def _point_vector(point: qm.ScoredPoint) -> list[float] | None:
        vector = point.vector
        if vector is None:
            return None
        if isinstance(vector, list):
            return [float(value) for value in vector]
        if isinstance(vector, dict):
            unnamed = vector.get("")
            if isinstance(unnamed, list):
                return [float(value) for value in unnamed]
            first = next(iter(vector.values()), None)
            if isinstance(first, list):
                return [float(value) for value in first]
        return None

    @staticmethod
    def _map_point(point: qm.ScoredPoint) -> dict[str, Any]:
        payload = dict(point.payload or {})
        if "content_text" not in payload:
            payload["content_text"] = chunk_text(payload)
        if point.id is not None and "chunk_id" not in payload:
            payload["chunk_id"] = str(point.id)
        payload["score"] = float(point.score)
        return payload


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)
