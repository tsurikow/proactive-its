from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.platform.rag.answer_generator import INSUFFICIENT_EVIDENCE, AnswerGenerator
from app.platform.chat.utils import chunk_text, clean_chunk_text
from app.platform.config import Settings
from app.platform.logging import log_event
from app.platform.rag.query_rewrite import QueryRewriteService
from app.platform.rag.retriever import DenseRetriever
from app.teacher.artifacts.models import GroundingAnalysis

logger = logging.getLogger(__name__)

QUERY_TERM_RE = re.compile(r"[a-z]{4,}")
LATEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
LATEX_TERM_MAP: dict[str, str] = {
    "\\int": "integral",
    "\\iint": "integral",
    "\\iiint": "integral",
    "\\oint": "integral",
    "\\lim": "limit",
    "\\frac": "fraction",
    "\\dfrac": "fraction",
    "\\sum": "summation",
    "\\prod": "product",
    "\\partial": "partial derivative",
    "\\nabla": "gradient",
    "\\sqrt": "root",
    "\\sin": "sine",
    "\\cos": "cosine",
    "\\tan": "tangent",
    "\\cot": "cotangent",
    "\\sec": "secant",
    "\\csc": "cosecant",
    "\\arcsin": "arcsine",
    "\\arccos": "arccosine",
    "\\arctan": "arctangent",
    "\\log": "logarithm",
    "\\ln": "logarithm",
    "\\exp": "exponential",
    "\\infty": "infinity",
    "\\theta": "theta",
    "\\alpha": "alpha",
    "\\beta": "beta",
    "\\gamma": "gamma",
    "\\delta": "delta",
    "\\epsilon": "epsilon",
    "\\pi": "pi",
    "\\sigma": "sigma",
    "\\lambda": "lambda",
    "\\vec": "vector",
    "\\det": "determinant",
    "\\dim": "dimension",
    "\\max": "maximum",
    "\\min": "minimum",
    "\\sup": "supremum",
    "\\inf": "infimum",
}
NON_ANCHOR_TERMS = {
    "about",
    "could",
    "does",
    "explain",
    "into",
    "know",
    "mean",
    "means",
    "tell",
    "than",
    "that",
    "what",
    "when",
    "where",
    "which",
    "why",
    "with",
    "work",
    "works",
    "would",
}
ANCHOR_TEXT_LIMIT = 800
REWRITE_MIN_TOP_SCORE_GAIN = 0.1


class GroundedAnswerRuntime:
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
        filters: dict[str, str | None],
        context: dict[str, str | None] | None = None,
        teacher_surface_instruction: str | None = None,
        teacher_policy_brief: str | None = None,
        grounding_analysis: GroundingAnalysis | None = None,
    ) -> dict[str, Any]:
        retrieval_result = await self.evaluate_retrieval(
            message=message,
            filters=filters,
            context=context,
        )
        return await self.answer_from_retrieval(
            message=message,
            retrieval_result=retrieval_result,
            teacher_surface_instruction=teacher_surface_instruction,
            teacher_policy_brief=teacher_policy_brief,
            grounding_analysis=grounding_analysis,
        )

    async def answer_from_retrieval(
        self,
        *,
        message: str,
        retrieval_result: dict[str, Any],
        teacher_surface_instruction: str | None = None,
        teacher_policy_brief: str | None = None,
        grounding_analysis: GroundingAnalysis | None = None,
    ) -> dict[str, Any]:
        chunks = retrieval_result["chunks"]
        debug = retrieval_result["debug"]
        top_score = float(retrieval_result["top_score"])
        weak_evidence = bool(retrieval_result["weak_evidence"])
        generation_started = None

        if weak_evidence:
            log_event(
                logger,
                "rag.answer_completed",
                mode="teacher_chat",
                chunk_count=len(chunks),
                top_score=top_score,
                weak_evidence=weak_evidence,
                weak_evidence_reason=debug.get("weak_evidence_reason"),
                offtopic_suspected=bool(debug.get("offtopic_suspected")),
                rewrite_attempted=bool(debug.get("rewrite_attempted")),
                rewrite_accepted=bool(debug.get("rewrite_accepted")),
                citation_count=0,
                answer_chars=len(INSUFFICIENT_EVIDENCE),
                timings_ms=debug.get("timings_ms"),
            )
            return {
                "answer_md": INSUFFICIENT_EVIDENCE,
                "citations": [],
                "debug": debug,
                "chunks": chunks,
            }

        try:
            generation_started = time.perf_counter()
            answer_md, citation_ids, citation_fallback_used = await self.generator.generate(
                question=message,
                chunks=chunks,
                teacher_surface_instruction=teacher_surface_instruction,
                teacher_policy_brief=teacher_policy_brief,
                grounding_analysis=grounding_analysis,
            )
        except Exception as exc:
            raise RuntimeError("Generation failed. Check LLM provider configuration.") from exc
        debug["citation_fallback_used"] = citation_fallback_used
        if answer_md == INSUFFICIENT_EVIDENCE:
            if generation_started is not None:
                debug["timings_ms"]["generation_ms"] = round(
                    (time.perf_counter() - generation_started) * 1000.0, 1
                )
            log_event(
                logger,
                "rag.answer_completed",
                mode="teacher_chat",
                chunk_count=len(chunks),
                top_score=top_score,
                weak_evidence=True,
                weak_evidence_reason=debug.get("weak_evidence_reason"),
                offtopic_suspected=bool(debug.get("offtopic_suspected")),
                rewrite_attempted=bool(debug.get("rewrite_attempted")),
                rewrite_accepted=bool(debug.get("rewrite_accepted")),
                citation_count=0,
                answer_chars=len(answer_md),
                timings_ms=debug.get("timings_ms"),
            )
            return {
                "answer_md": answer_md,
                "citations": [],
                "debug": debug,
                "chunks": chunks,
            }
        _validate_citations(citation_ids, chunks)
        citations = _build_citation_payload(citation_ids, chunks)
        if generation_started is not None:
            debug["timings_ms"]["generation_ms"] = round(
                (time.perf_counter() - generation_started) * 1000.0, 1
            )
        log_event(
            logger,
            "rag.answer_completed",
            mode="teacher_chat",
            chunk_count=len(chunks),
            top_score=top_score,
            weak_evidence=bool(debug.get("weak_evidence")),
            weak_evidence_reason=debug.get("weak_evidence_reason"),
            offtopic_suspected=bool(debug.get("offtopic_suspected")),
            rewrite_attempted=bool(debug.get("rewrite_attempted")),
            rewrite_accepted=bool(debug.get("rewrite_accepted")),
            citation_count=len(citations),
            answer_chars=len(answer_md),
            timings_ms=debug.get("timings_ms"),
        )
        return {
            "answer_md": answer_md,
            "citations": citations,
            "debug": debug,
            "chunks": chunks,
        }

    async def evaluate_retrieval(
        self,
        *,
        message: str,
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

        quality = _retrieval_quality(
            message=message,
            chunks=chunks,
            retrieval=retrieval,
            settings=self.settings,
        )
        top_score = quality["top_score"]
        evidence_chars = quality["evidence_chars"]
        weak_evidence = quality["weak_evidence"]
        debug = _build_debug_payload(
            settings=self.settings,
            retrieval=retrieval,
            chunks=chunks,
            quality=quality,
            total_ms=(time.perf_counter() - started) * 1000.0,
        )

        if weak_evidence and self.settings.rag_query_rewrite_enabled:
            rewrite_started = time.perf_counter()
            rewrite_attempted = False
            rewrite_query = None
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
                    rewritten_quality = _retrieval_quality(
                        message=message,
                        chunks=rewritten_chunks,
                        retrieval=rewritten_retrieval,
                        settings=self.settings,
                    )
                    rewritten_top_score = rewritten_quality["top_score"]
                    rewritten_evidence_chars = rewritten_quality["evidence_chars"]
                    rewritten_weak_evidence = rewritten_quality["weak_evidence"]
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
                            quality=rewritten_quality,
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
        return {
            "chunks": chunks,
            "retrieval": retrieval,
            "debug": debug,
            "top_score": top_score,
            "evidence_chars": evidence_chars,
            "weak_evidence": weak_evidence,
        }


def _build_debug_payload(
    *,
    settings: Settings,
    retrieval: dict[str, Any],
    chunks: list[dict[str, Any]],
    quality: dict[str, Any],
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
        "top_score": quality["top_score"],
        "evidence_chars": quality["evidence_chars"],
        "weak_evidence": quality["weak_evidence"],
        "eligible_query_term_count": quality["eligible_query_term_count"],
        "matched_query_term_count": quality["matched_query_term_count"],
        "matched_query_terms": list(quality["matched_query_terms"]),
        "query_overlap_ratio": quality["query_overlap_ratio"],
        "offtopic_suspected": quality["offtopic_suspected"],
        "weak_evidence_reason": quality["weak_evidence_reason"],
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
    *,
    message: str,
    chunks: list[dict[str, Any]],
    retrieval: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    top_score = float(retrieval.get("top_score", 0.0))
    evidence_chars = _evidence_chars(chunks)
    overlap = _query_overlap_metrics(message=message, chunks=chunks)
    weak_evidence, weak_reason, offtopic_suspected = _weak_evidence_decision(
        has_chunks=bool(chunks),
        evidence_chars=evidence_chars,
        top_score=top_score,
        eligible_query_term_count=overlap["eligible_query_term_count"],
        matched_query_term_count=overlap["matched_query_term_count"],
        settings=settings,
    )
    return {
        "top_score": top_score,
        "evidence_chars": evidence_chars,
        "weak_evidence": weak_evidence,
        "eligible_query_term_count": overlap["eligible_query_term_count"],
        "matched_query_term_count": overlap["matched_query_term_count"],
        "matched_query_terms": overlap["matched_query_terms"],
        "query_overlap_ratio": overlap["query_overlap_ratio"],
        "offtopic_suspected": offtopic_suspected,
        "weak_evidence_reason": weak_reason,
    }


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
    if rewritten_top_score - original_top_score < REWRITE_MIN_TOP_SCORE_GAIN:
        return False
    return True


def _evidence_chars(chunks: list[dict[str, Any]]) -> int:
    return sum(len(clean_chunk_text(chunk_text(chunk))) for chunk in chunks)


def _weak_evidence_decision(
    *,
    has_chunks: bool,
    evidence_chars: int,
    top_score: float,
    eligible_query_term_count: int,
    matched_query_term_count: int,
    settings: Settings,
) -> tuple[bool, str, bool]:
    if not has_chunks:
        return True, "no_chunks", False

    numeric_weak = evidence_chars < settings.rag_min_evidence_chars or top_score < settings.rag_min_score
    if numeric_weak:
        if evidence_chars < settings.rag_min_evidence_chars and top_score < settings.rag_min_score:
            return True, "insufficient_evidence_chars_and_low_top_score", False
        if evidence_chars < settings.rag_min_evidence_chars:
            return True, "insufficient_evidence_chars", False
        return True, "low_top_score", False

    offtopic_suspected = (
        eligible_query_term_count >= settings.rag_offtopic_min_query_terms
        and matched_query_term_count == 0
        and top_score < settings.rag_offtopic_score_ceiling
    )
    if offtopic_suspected:
        return True, "offtopic_zero_overlap", True
    return False, "ok", False


def _query_overlap_metrics(*, message: str, chunks: list[dict[str, Any]]) -> dict[str, Any]:
    query_terms = _extract_eligible_query_terms(message)
    if not query_terms:
        return {
            "eligible_query_term_count": 0,
            "matched_query_term_count": 0,
            "matched_query_terms": (),
            "query_overlap_ratio": 0.0,
        }
    anchor_terms = _extract_anchor_terms(chunks)
    matched_terms = tuple(sorted(term for term in query_terms if term in anchor_terms))
    return {
        "eligible_query_term_count": len(query_terms),
        "matched_query_term_count": len(matched_terms),
        "matched_query_terms": matched_terms,
        "query_overlap_ratio": round(len(matched_terms) / len(query_terms), 4),
    }


def _expand_latex_terms(text: str) -> str:
    """Replace LaTeX commands with English equivalents before stripping remaining commands."""
    lowered = text.lower()
    for cmd, word in LATEX_TERM_MAP.items():
        lowered = lowered.replace(cmd, f" {word} ")
    return LATEX_COMMAND_RE.sub(" ", lowered)


def _extract_eligible_query_terms(message: str) -> tuple[str, ...]:
    normalized = _expand_latex_terms(str(message or ""))
    terms = {
        token
        for token in QUERY_TERM_RE.findall(normalized)
        if token not in NON_ANCHOR_TERMS
    }
    return tuple(sorted(terms))


def _extract_anchor_terms(chunks: list[dict[str, Any]]) -> set[str]:
    anchor_terms: set[str] = set()
    for chunk in chunks:
        title = str(chunk.get("title") or "")
        content = clean_chunk_text(chunk_text(chunk))[:ANCHOR_TEXT_LIMIT]
        anchor_terms.update(_extract_anchor_terms_from_text(title))
        anchor_terms.update(_extract_anchor_terms_from_text(content))
    return anchor_terms


def _extract_anchor_terms_from_text(text: str) -> set[str]:
    normalized = _expand_latex_terms(str(text or ""))
    return {
        token
        for token in QUERY_TERM_RE.findall(normalized)
        if token not in NON_ANCHOR_TERMS
    }


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
