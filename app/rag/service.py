from __future__ import annotations

import time
from typing import Any

from app.core.config import Settings
from app.rag.generate import INSUFFICIENT_EVIDENCE, AnswerGenerator
from app.rag.retrieve import DenseRetriever
from app.rag.rewrite import QueryRewriteService
from app.rag.utils import chunk_text, clean_chunk_text


class RAGService:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        retriever: DenseRetriever | None = None,
        generator: AnswerGenerator | None = None,
        rewriter: QueryRewriteService | None = None,
    ):
        self.retriever = retriever or DenseRetriever(settings)
        self.generator = generator or AnswerGenerator(settings)
        self.settings = settings or self.retriever.settings
        self.rewriter = rewriter or QueryRewriteService(self.settings)

    async def answer(
        self,
        message: str,
        mode: str,
        filters: dict[str, str | None],
        context: dict[str, str | None] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            chunks, retrieval = await self.retriever.retrieve(
                message,
                filters=filters,
                context=context,
            )
        except Exception as exc:
            raise RuntimeError("Retrieval failed. Check embedding and Qdrant configuration.") from exc

        top_score, evidence_chars, weak_evidence = _retrieval_quality(chunks, retrieval, self.settings)
        debug = _build_debug_payload(
            settings=self.settings,
            retrieval=retrieval,
            chunks=chunks,
            top_score=top_score,
            evidence_chars=evidence_chars,
            weak_evidence=weak_evidence,
            total_ms=(time.perf_counter() - started) * 1000.0,
        )

        if weak_evidence and self.settings.rag_query_rewrite_enabled:
            rewrite_started = time.perf_counter()
            rewrite_attempted = False
            rewrite_query = None
            rewrite_accepted = False
            try:
                rewrite_query = await self.rewriter.rewrite(message)
                rewrite_attempted = bool(rewrite_query)
            except Exception:
                rewrite_query = None
            debug["rewrite_attempted"] = rewrite_attempted
            debug["rewrite_reason"] = "weak_initial_retrieval" if rewrite_attempted else None
            if rewrite_query:
                debug["rewrite_query"] = rewrite_query
                try:
                    rewritten_chunks, rewritten_retrieval = await self.retriever.retrieve(
                        rewrite_query,
                        filters=filters,
                        context=context,
                    )
                except Exception:
                    rewritten_chunks, rewritten_retrieval = [], None
                rewrite_ms = (time.perf_counter() - rewrite_started) * 1000.0
                debug["timings_ms"]["rewrite_ms"] = round(rewrite_ms, 1)
                if rewritten_retrieval is not None:
                    (
                        rewritten_top_score,
                        rewritten_evidence_chars,
                        rewritten_weak_evidence,
                    ) = _retrieval_quality(rewritten_chunks, rewritten_retrieval, self.settings)
                    rewrite_accepted = _should_accept_rewrite(
                        original_chunks=chunks,
                        original_top_score=top_score,
                        original_evidence_chars=evidence_chars,
                        rewritten_chunks=rewritten_chunks,
                        rewritten_top_score=rewritten_top_score,
                        rewritten_evidence_chars=rewritten_evidence_chars,
                        rewritten_weak_evidence=rewritten_weak_evidence,
                    )
                    debug["rewrite_accepted"] = rewrite_accepted
                    if rewrite_accepted:
                        chunks = rewritten_chunks
                        retrieval = rewritten_retrieval
                        top_score = rewritten_top_score
                        evidence_chars = rewritten_evidence_chars
                        weak_evidence = rewritten_weak_evidence
                        debug = _build_debug_payload(
                            settings=self.settings,
                            retrieval=retrieval,
                            chunks=chunks,
                            top_score=top_score,
                            evidence_chars=evidence_chars,
                            weak_evidence=weak_evidence,
                            total_ms=(time.perf_counter() - started) * 1000.0,
                        )
                        debug["rewrite_attempted"] = True
                        debug["rewrite_query"] = rewrite_query
                        debug["rewrite_accepted"] = True
                        debug["rewrite_reason"] = "weak_initial_retrieval"
                        debug["timings_ms"]["rewrite_ms"] = round(rewrite_ms, 1)
                else:
                    debug["rewrite_accepted"] = False
            else:
                debug["rewrite_accepted"] = False

        debug["timings_ms"]["total_ms"] = round((time.perf_counter() - started) * 1000.0, 1)

        if weak_evidence:
            return {
                "answer_md": INSUFFICIENT_EVIDENCE,
                "citations": [],
                "debug": debug,
                "chunks": chunks,
            }

        try:
            answer_md, citation_ids, citation_fallback_used = await self.generator.generate(
                question=message,
                chunks=chunks,
                mode=mode,
            )
        except Exception as exc:
            raise RuntimeError("Generation failed. Check LLM provider configuration.") from exc
        debug["citation_fallback_used"] = citation_fallback_used
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


def _build_debug_payload(
    *,
    settings: Settings,
    retrieval: dict[str, Any],
    chunks: list[dict[str, Any]],
    top_score: float,
    evidence_chars: int,
    weak_evidence: bool,
    total_ms: float,
) -> dict[str, Any]:
    return {
        "top_k": settings.rag_top_k_fetch,
        "filtered_by": retrieval["filtered_by"],
        "scores": [
            {"chunk_id": str(item.get("chunk_id", "")), "score": float(item.get("score", 0.0))}
            for item in chunks
            if item.get("chunk_id")
        ],
        "top_score": top_score,
        "evidence_chars": evidence_chars,
        "weak_evidence": weak_evidence,
        "retrieval_mode": str(retrieval["retrieval_mode"]),
        "citation_fallback_used": False,
        "rewrite_attempted": False,
        "rewrite_query": None,
        "rewrite_accepted": False,
        "rewrite_reason": None,
        "timings_ms": {
            "embed_ms": float(retrieval["embed_ms"]),
            "search_ms": float(retrieval["search_ms"]),
            "rerank_ms": float(retrieval["rerank_ms"]),
            "total_ms": round(total_ms, 1),
        },
    }


def _retrieval_quality(
    chunks: list[dict[str, Any]],
    retrieval: dict[str, Any],
    settings: Settings,
) -> tuple[float, int, bool]:
    top_score = float(retrieval.get("top_score", 0.0))
    evidence_chars = _evidence_chars(chunks)
    weak_evidence = _is_weak_evidence(
        has_chunks=bool(chunks),
        evidence_chars=evidence_chars,
        top_score=top_score,
        settings=settings,
    )
    return top_score, evidence_chars, weak_evidence


def _should_accept_rewrite(
    *,
    original_chunks: list[dict[str, Any]],
    original_top_score: float,
    original_evidence_chars: int,
    rewritten_chunks: list[dict[str, Any]],
    rewritten_top_score: float,
    rewritten_evidence_chars: int,
    rewritten_weak_evidence: bool,
) -> bool:
    if rewritten_weak_evidence:
        return False
    return (
        rewritten_top_score > original_top_score
        or rewritten_evidence_chars > original_evidence_chars
        or len(rewritten_chunks) > len(original_chunks)
    )


def _evidence_chars(chunks: list[dict[str, Any]]) -> int:
    return sum(len(clean_chunk_text(chunk_text(chunk))) for chunk in chunks)


def _is_weak_evidence(
    *,
    has_chunks: bool,
    evidence_chars: int,
    top_score: float,
    settings: Settings,
) -> bool:
    if not has_chunks:
        return True
    return evidence_chars < settings.rag_min_evidence_chars or top_score < settings.rag_min_score


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
