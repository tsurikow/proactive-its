from __future__ import annotations

import time
from typing import Any

from app.core.config import Settings
from app.rag.generate import INSUFFICIENT_EVIDENCE, AnswerGenerator
from app.rag.retrieve import DenseRetriever
from app.rag.utils import chunk_text, clean_chunk_text


class RAGService:
    def __init__(self, settings: Settings | None = None):
        self.retriever = DenseRetriever(settings)
        self.generator = AnswerGenerator(settings)
        self.settings = self.retriever.settings

    def answer(
        self,
        message: str,
        mode: str,
        filters: dict[str, str | None],
        context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            chunks, retrieval = self.retriever.retrieve_and_rerank(
                message,
                filters=filters,
                context=context,
            )
        except Exception as exc:
            raise RuntimeError("Retrieval failed. Check embedding and Qdrant configuration.") from exc

        top_score = float(chunks[0].get("score", 0.0)) if chunks else 0.0
        evidence_chars = _evidence_chars(chunks)
        weak_evidence = _is_weak_evidence(
            has_chunks=bool(chunks),
            evidence_chars=evidence_chars,
            top_score=top_score,
            best_term_overlap=float(retrieval["best_term_overlap"]),
            settings=self.settings,
        )
        debug = {
            "top_k": self.settings.rag_top_k_fetch,
            "filtered_by": retrieval["filtered_by"],
            "scores": [
                {"chunk_id": str(item.get("chunk_id", "")), "score": float(item.get("score", 0.0))}
                for item in chunks
                if item.get("chunk_id")
            ],
            "top_score": top_score,
            "best_term_overlap": float(retrieval["best_term_overlap"]),
            "evidence_chars": evidence_chars,
            "weak_evidence": weak_evidence,
            "query_intent": str(retrieval["query_intent"]),
            "retrieval_mode": "dense",
            "timings_ms": {
                "embed_ms": float(retrieval["embed_ms"]),
                "search_ms": float(retrieval["search_ms"]),
                "rerank_ms": float(retrieval["rerank_ms"]),
                "total_ms": round((time.perf_counter() - started) * 1000.0, 1),
            },
        }
        if weak_evidence:
            return {
                "answer_md": INSUFFICIENT_EVIDENCE,
                "citations": [],
                "debug": debug,
                "chunks": chunks,
            }

        try:
            answer_md, citation_ids = self.generator.generate(
                question=message,
                chunks=chunks,
                mode=mode,
            )
        except Exception as exc:
            raise RuntimeError("Generation failed. Check LLM provider configuration.") from exc
        if answer_md == INSUFFICIENT_EVIDENCE:
            return {
                "answer_md": answer_md,
                "citations": [],
                "debug": debug,
                "chunks": chunks,
            }
        _validate_citations(citation_ids, chunks)
        citations = _build_citation_payload(citation_ids, chunks)
        return {
            "answer_md": answer_md,
            "citations": citations,
            "debug": debug,
            "chunks": chunks,
        }


def _evidence_chars(chunks: list[dict[str, Any]]) -> int:
    return sum(len(clean_chunk_text(chunk_text(chunk))) for chunk in chunks)


def _is_weak_evidence(
    *,
    has_chunks: bool,
    evidence_chars: int,
    top_score: float,
    best_term_overlap: float,
    settings: Settings,
) -> bool:
    if not has_chunks:
        return True
    return evidence_chars < settings.rag_min_evidence_chars or (
        top_score < settings.rag_min_score and best_term_overlap < settings.rag_min_term_overlap
    )


def _validate_citations(citation_ids: list[str], chunks: list[dict[str, Any]]) -> None:
    allowed = {str(chunk.get("chunk_id")) for chunk in chunks if chunk.get("chunk_id")}
    invalid = [citation_id for citation_id in citation_ids if citation_id not in allowed]
    if invalid:
        raise RuntimeError(f"Invalid citations not found in retrieval set: {invalid}")


def _build_citation_payload(
    citation_ids: list[str],
    chunks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_id = {str(chunk.get("chunk_id")): chunk for chunk in chunks if chunk.get("chunk_id")}
    payload: list[dict[str, Any]] = []
    for citation_id in citation_ids:
        chunk = by_id.get(citation_id)
        if not chunk:
            continue
        payload.append(
            {
                "chunk_id": citation_id,
                "doc_id": str(chunk.get("doc_id", "")),
                "title": str(chunk.get("title", "")),
                "breadcrumb": list(chunk.get("breadcrumb") or []),
                "quote": _quote_snippet(chunk_text(chunk)),
            }
        )
    return payload


def _quote_snippet(text: str, max_len: int = 260) -> str:
    clean = clean_chunk_text(text)
    if len(clean) <= max_len:
        return clean
    return clean[:max_len].rstrip() + "..."
