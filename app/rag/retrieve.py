from __future__ import annotations

import logging
import re
import time
from collections import Counter
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client.http import models as qm

from app.core.config import Settings, get_settings
from app.infra.embeddings import EmbeddingClient
from app.infra.qdrant_store import VectorStore
from app.rag.utils import chunk_text, clean_chunk_text

logger = logging.getLogger(__name__)

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


class _EmbeddingAdapter(Embeddings):
    def __init__(self, embedder: EmbeddingClient):
        self._embedder = embedder

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.embed_texts(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embedder.embed_texts([text])[0]


class DenseRetriever:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.embedder = EmbeddingClient(self.settings)
        self.store = VectorStore(self.settings)
        self._adapter = _EmbeddingAdapter(self.embedder)
        self._force_direct = False
        self._lc_store = QdrantVectorStore(
            client=self.store.client,
            collection_name=self.settings.qdrant_collection,
            embedding=self._adapter,
            content_payload_key="content_text",
            metadata_payload_key="metadata",
        )

    def retrieve_and_rerank(
        self,
        query: str,
        *,
        filters: dict[str, str | None] | None = None,
        context: dict[str, str | None] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        active_filters = filters or {}
        query_filter = self.store.build_filter(active_filters)

        t_embed = time.perf_counter()
        query_vector = self._adapter.embed_query(query)
        embed_ms = (time.perf_counter() - t_embed) * 1000.0

        t_search = time.perf_counter()
        candidates = self._search_candidates(
            query=query,
            query_vector=query_vector,
            query_filter=query_filter,
            top_k=max(self.settings.rag_top_k_fetch, self.settings.rag_final_k),
        )
        search_ms = (time.perf_counter() - t_search) * 1000.0

        t_rerank = time.perf_counter()
        chunks, rerank_meta = self._rerank_candidates(
            query,
            candidates,
            context=context or {},
        )
        rerank_ms = (time.perf_counter() - t_rerank) * 1000.0

        return chunks, {
            "embed_ms": round(embed_ms, 1),
            "search_ms": round(search_ms, 1),
            "rerank_ms": round(rerank_ms, 1),
            "query_intent": rerank_meta["query_intent"],
            "best_term_overlap": rerank_meta["best_term_overlap"],
            "filtered_by": {k: v for k, v in active_filters.items() if v},
        }

    def _search_candidates(
        self,
        *,
        query: str,
        query_vector: list[float],
        query_filter: qm.Filter | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not self._force_direct:
            mapped = self._search_with_langchain(
                query=query,
                query_vector=query_vector,
                query_filter=query_filter,
                top_k=top_k,
            )
            if mapped and self._has_canonical_payload(mapped):
                return mapped
            if mapped:
                logger.info(
                    "LangChain metadata payload is missing canonical keys; using direct Qdrant mode."
                )
            self._force_direct = True
        return self.store.search(
            query_vector=query_vector,
            top_k=top_k,
            query_filter=query_filter,
            collection_name=self.settings.qdrant_collection,
        )

    def _search_with_langchain(
        self,
        *,
        query: str,
        query_vector: list[float],
        query_filter: qm.Filter | None,
        top_k: int,
    ) -> list[dict[str, Any]]:
        try:
            results = self._lc_store.similarity_search_with_score_by_vector(
                embedding=query_vector,
                k=top_k,
                filter=query_filter,
            )
        except Exception:
            results = self._lc_store.similarity_search_with_score(
                query=query,
                k=top_k,
                filter=query_filter,
            )
        return [self._map_doc(doc, score) for doc, score in results]

    @staticmethod
    def _map_doc(doc: Document, score: float) -> dict[str, Any]:
        metadata = dict(doc.metadata or {})
        nested = metadata.get("metadata")
        if isinstance(nested, dict):
            merged = dict(nested)
            for key, value in metadata.items():
                if key != "metadata":
                    merged.setdefault(key, value)
            metadata = merged
        payload = dict(metadata)
        if "content_text" not in payload:
            payload["content_text"] = doc.page_content or chunk_text(payload)
        if getattr(doc, "id", None) is not None and "chunk_id" not in payload:
            payload["chunk_id"] = str(doc.id)
        payload["score"] = float(score)
        return payload

    @staticmethod
    def _has_canonical_payload(rows: list[dict[str, Any]]) -> bool:
        return any(row.get("chunk_id") and row.get("doc_id") for row in rows)

    def _rerank_candidates(
        self,
        query: str,
        candidates: list[dict[str, Any]],
        *,
        context: dict[str, str | None],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        terms = _query_terms(query)
        intent = _query_intent(query)

        rescored: list[dict[str, Any]] = []
        overlaps: list[float] = []
        for row in candidates:
            updated = dict(row)
            updated["score"] = self._boost_score(updated, terms=terms, intent=intent, context=context)
            rescored.append(updated)
            overlaps.append(_term_overlap(terms, clean_chunk_text(chunk_text(updated))))

        rescored.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        reduced = _diversity_reduce(rescored, max(1, self.settings.rag_final_k))
        return reduced, {
            "query_intent": intent,
            "best_term_overlap": round(max(overlaps, default=0.0), 4),
        }

    def _boost_score(
        self,
        row: dict[str, Any],
        *,
        terms: list[str],
        intent: str,
        context: dict[str, str | None],
    ) -> float:
        score = float(row.get("score", 0.0))
        title = str(row.get("title") or "").lower()
        subsection = str(row.get("subsection_title") or "").lower()
        chunk_type = str(row.get("chunk_type") or "").lower()
        text = clean_chunk_text(chunk_text(row)).lower()
        combined = f"{title}\n{subsection}\n{text}"

        overlap = _term_overlap(terms, combined)
        score += 0.24 * overlap
        if overlap > 0.55:
            score += 0.14

        if intent == "definition":
            if chunk_type in {"definition", "theorem"}:
                score += 0.42
            if "definition" in title or "definition" in subsection:
                score += 0.28
        elif intent == "explain" and chunk_type in {"concept", "example", "proof"}:
            score += 0.1

        section_id = context.get("section_id")
        module_id = context.get("module_id")
        if section_id and str(row.get("section_id", "")) == section_id:
            score += self.settings.rag_context_section_boost
        elif module_id and str(row.get("module_id", "")) == module_id:
            score += self.settings.rag_context_module_boost

        return score


def _query_terms(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for token in re.findall(r"[a-zA-Z0-9]{3,}", query.lower()):
        if token in STOPWORDS or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _query_intent(query: str) -> str:
    low = query.lower()
    if re.search(r"\b(define|definition|what is|meaning)\b", low):
        return "definition"
    if re.search(r"\b(explain|why|how)\b", low):
        return "explain"
    return "generic"


def _term_overlap(terms: list[str], text: str) -> float:
    if not terms:
        return 0.0
    low = text.lower()
    hits = sum(1 for term in terms if term in low)
    return hits / max(len(terms), 1)


def _diversity_reduce(rows: list[dict[str, Any]], final_k: int) -> list[dict[str, Any]]:
    if len(rows) <= final_k:
        return rows

    per_section: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    for row in rows:
        section_key = str(row.get("section_id") or row.get("doc_id") or "")
        if section_key and per_section[section_key] >= 3:
            continue
        selected.append(row)
        if section_key:
            per_section[section_key] += 1
        if len(selected) >= final_k:
            break

    if len(selected) < final_k:
        for row in rows:
            if row not in selected:
                selected.append(row)
            if len(selected) >= final_k:
                break
    return selected[:final_k]
