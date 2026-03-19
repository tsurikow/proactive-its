from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.chat.answer_generator import INSUFFICIENT_EVIDENCE, AnswerGenerator
from app.chat.assessment import QuizAssessmentService
from app.chat.query_rewrite import QueryRewriteService
from app.chat.repository import InteractionRepository
from app.chat.retriever import DenseRetriever
from app.chat.utils import chunk_text, clean_chunk_text
from app.platform.config import Settings
from app.platform.logging import log_event
from app.tutor.repository import TutorRepository

logger = logging.getLogger(__name__)

QUERY_TERM_RE = re.compile(r"[a-z]{4,}")
LATEX_COMMAND_RE = re.compile(r"\\[a-zA-Z]+")
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
        retrieval_result = await self.evaluate_retrieval(
            message=message,
            filters=filters,
            context=context,
        )
        chunks = retrieval_result["chunks"]
        debug = retrieval_result["debug"]
        top_score = float(retrieval_result["top_score"])
        weak_evidence = bool(retrieval_result["weak_evidence"])
        generation_started = None

        if weak_evidence:
            log_event(
                logger,
                "rag.answer_completed",
                mode=mode,
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
                mode=mode,
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
                mode=mode,
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
            mode=mode,
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


class ChatService:
    def __init__(
        self,
        *,
        chat_repository: InteractionRepository,
        tutor_repository: TutorRepository,
        rag_service: RAGService,
        assessment_service: QuizAssessmentService,
        settings: Settings,
    ):
        self.chat_repository = chat_repository
        self.tutor_repository = tutor_repository
        self.rag_service = rag_service
        self.assessment_service = assessment_service
        self.settings = settings

    async def chat(self, request: Any) -> dict[str, Any]:
        await self.tutor_repository.ensure_learner(request.learner_id)
        session_id = await self.chat_repository.get_or_create_session(request.learner_id)

        module_id = request.context.current_module_id
        section_id = request.context.current_section_id
        filters = {
            "module_id": None,
            "section_id": None,
            "doc_type": "section",
        }

        try:
            rag_result = await self.rag_service.answer(
                message=request.message,
                mode=request.mode,
                filters=filters,
                context={
                    "module_id": module_id,
                    "section_id": section_id,
                },
            )
        except RuntimeError as exc:
            raise RuntimeError(str(exc)) from exc

        interaction_id = await self.chat_repository.create_interaction_with_sources(
            learner_id=request.learner_id,
            session_id=session_id,
            message=request.message,
            answer=rag_result["answer_md"],
            module_id=module_id,
            section_id=section_id,
            sources=[
                {
                    "chunk_id": chunk["chunk_id"],
                    "score": chunk.get("score"),
                    "rank": idx,
                }
                for idx, chunk in enumerate(rag_result["chunks"])
            ],
        )

        if request.mode == "quiz":
            await self._persist_quiz_assessment(
                interaction_id=interaction_id,
                learner_id=request.learner_id,
                session_id=session_id,
                module_id=module_id,
                section_id=section_id,
                learner_response=request.message,
                tutor_answer=rag_result["answer_md"],
                chunks=rag_result["chunks"],
            )

        retrieval_debug = rag_result["debug"] if self.settings.enable_retrieval_debug else None
        return {
            "interaction_id": interaction_id,
            "answer_md": rag_result["answer_md"],
            "citations": rag_result["citations"],
            "retrieval_debug": retrieval_debug,
        }

    async def get_feedback_context(self, learner_id: str, interaction_id: int) -> dict[str, Any] | None:
        interaction = await self.chat_repository.get_interaction(interaction_id)
        if not interaction or interaction["learner_id"] != learner_id:
            return None
        assessment = await self.chat_repository.get_interaction_assessment(interaction_id)
        return {
            "interaction": interaction,
            "assessment": assessment,
        }

    async def record_feedback_confidence(self, interaction_id: int, confidence: int) -> None:
        await self.chat_repository.update_interaction_confidence(interaction_id, confidence)

    async def _persist_quiz_assessment(
        self,
        *,
        interaction_id: int,
        learner_id: str,
        session_id: int,
        module_id: str | None,
        section_id: str | None,
        learner_response: str,
        tutor_answer: str,
        chunks: list[dict[str, Any]],
    ) -> None:
        assessment_context = {
            "module_id": module_id,
            "section_id": section_id,
        }
        try:
            assessment_result = await self.assessment_service.assess(
                learner_response=learner_response,
                mode="quiz",
                chunks=[] if tutor_answer == INSUFFICIENT_EVIDENCE else chunks,
                context=assessment_context,
                tutor_answer=tutor_answer,
            )
        except Exception as exc:
            log_event(
                logger,
                "assessment.completed",
                decision="insufficient_evidence",
                fallback_used=True,
                learner_id=learner_id,
                section_id=section_id,
                module_id=module_id,
                reason="assessment_service_exception",
                error=str(exc),
            )
            assessment_result = self.assessment_service.build_fallback(
                learner_response=learner_response,
                module_id=module_id,
                section_id=section_id,
                reason="assessment_service_exception",
            )
        try:
            await self.chat_repository.upsert_interaction_assessment(
                interaction_id=interaction_id,
                learner_id=learner_id,
                session_id=session_id,
                module_id=module_id,
                section_id=section_id,
                result=assessment_result,
            )
        except Exception as exc:
            log_event(
                logger,
                "assessment.persistence_failed",
                interaction_id=interaction_id,
                learner_id=learner_id,
                section_id=section_id,
                module_id=module_id,
                error=str(exc),
            )


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


def _extract_eligible_query_terms(message: str) -> tuple[str, ...]:
    normalized = LATEX_COMMAND_RE.sub(" ", str(message or "").lower())
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
    normalized = LATEX_COMMAND_RE.sub(" ", str(text or "").lower())
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
