from __future__ import annotations

from typing import Any

from app.core.config import Settings, get_settings
from app.rag.embeddings import EmbeddingClient
from app.rag.vector_store import VectorStore


class Retriever:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.embedder = EmbeddingClient(self.settings)
        self.store = VectorStore(self.settings)

    def retrieve(
        self,
        query: str,
        filters: dict[str, str | None] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        qvec = self.embedder.embed_texts([query])[0]
        query_filter = self.store.build_filter(filters or {})
        results = self.store.search(qvec, top_k=self.settings.rag_top_k, query_filter=query_filter)
        reduced = self._diversity_reduce(results, self.settings.rag_final_k)
        debug = {
            "top_k": self.settings.rag_top_k,
            "filtered_by": {k: v for k, v in (filters or {}).items() if v},
            "scores": [{"chunk_id": r["chunk_id"], "score": float(r.get("score", 0.0))} for r in reduced],
        }
        return reduced, debug

    @staticmethod
    def _diversity_reduce(items: list[dict[str, Any]], final_k: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen_sections: set[str] = set()

        for item in items:
            section = item.get("section_id") or ""
            if section and section in seen_sections:
                continue
            if section:
                seen_sections.add(section)
            selected.append(item)
            if len(selected) >= final_k:
                return selected

        for item in items:
            if len(selected) >= final_k:
                break
            if item not in selected:
                selected.append(item)

        return selected
