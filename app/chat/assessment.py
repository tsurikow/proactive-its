from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.chat.models import (
    AssessmentDecision,
    AssessmentReasoningSummary,
    AssessmentResult,
    AssessmentStructuredPayload,
    RecommendedNextAction,
)
from app.chat.utils import chunk_text, clean_chunk_text
from app.platform.config import Settings, get_settings
from app.platform.logging import log_event

logger = logging.getLogger(__name__)

VALID_ACTIONS_BY_DECISION = {
    AssessmentDecision.CORRECT: {
        RecommendedNextAction.AFFIRM_AND_ADVANCE,
        RecommendedNextAction.REINFORCE_KEY_POINT,
    },
    AssessmentDecision.PARTIALLY_CORRECT: {
        RecommendedNextAction.REINFORCE_KEY_POINT,
        RecommendedNextAction.ASK_FOR_CLARIFICATION,
    },
    AssessmentDecision.MISCONCEPTION: {
        RecommendedNextAction.CORRECT_MISCONCEPTION,
        RecommendedNextAction.REINFORCE_KEY_POINT,
    },
    AssessmentDecision.PROCEDURAL_ERROR: {
        RecommendedNextAction.REQUEST_STEP_REVISION,
        RecommendedNextAction.REINFORCE_KEY_POINT,
    },
    AssessmentDecision.OFF_TOPIC: {
        RecommendedNextAction.REDIRECT_TO_TOPIC,
        RecommendedNextAction.ASK_FOR_CLARIFICATION,
    },
    AssessmentDecision.INSUFFICIENT_EVIDENCE: {
        RecommendedNextAction.ASK_FOR_CLARIFICATION,
        RecommendedNextAction.REINFORCE_KEY_POINT,
    },
}
SOURCE_LABEL_RE = re.compile(r"\bS\d+\b", re.IGNORECASE)


class QuizAssessmentService:
    prompt_profile_version = "quiz_assessment_prompt_v1"
    schema_version = "assessment_result_v1"
    assessment_temperature = 0.0
    context_char_limit = 900
    max_retries = 2

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        llm_client: AsyncOpenAI | None = None,
    ):
        self.settings = settings or get_settings()
        self._client = llm_client

    async def assess(
        self,
        *,
        learner_response: str,
        mode: str,
        chunks: list[dict[str, Any]],
        context: dict[str, str | None] | None = None,
        tutor_answer: str | None = None,
        prompt_context: str | None = None,
    ) -> AssessmentResult:
        context = context or {}
        module_id = context.get("module_id")
        section_id = context.get("section_id")
        fallback = self.build_fallback(
            learner_response=learner_response,
            module_id=module_id,
            section_id=section_id,
            reason="assessment_unavailable",
        )

        if mode != "quiz":
            return fallback
        if not chunks:
            return self.build_fallback(
                learner_response=learner_response,
                module_id=module_id,
                section_id=section_id,
                reason="no_retrieved_chunks",
            )
        if self._client is None:
            return fallback

        context_block, source_map = self._build_context_block(chunks)
        prompt_text = self._build_prompt(
            learner_response=learner_response,
            prompt_context=prompt_context,
            tutor_answer=tutor_answer,
            module_id=module_id,
            section_id=section_id,
            context_block=context_block,
        )
        try:
            payload = await self._invoke(prompt_text=prompt_text)
            payload, normalization_events = self._normalize_payload(payload)
            cited_chunk_ids = self._map_citations(
                payload.citations,
                source_map,
                decision=payload.decision,
            )
            self._validate_semantics(payload=payload, cited_chunk_ids=cited_chunk_ids)
        except Exception as exc:
            fallback_reason = self._fallback_reason_from_exception(exc)
            log_event(
                logger,
                "assessment.completed",
                decision=AssessmentDecision.INSUFFICIENT_EVIDENCE.value,
                fallback_used=True,
                section_id=section_id,
                module_id=module_id,
                reason=fallback_reason,
                error=str(exc),
                normalization_event_count=0,
            )
            return self.build_fallback(
                learner_response=learner_response,
                module_id=module_id,
                section_id=section_id,
                reason=fallback_reason,
            )

        result = AssessmentResult(
            decision=payload.decision,
            confidence=payload.confidence,
            learner_rationale=self._clean_text(payload.learner_rationale, 500),
            reasoning_summary=AssessmentReasoningSummary.model_validate(
                payload.reasoning_summary.model_dump()
            ),
            recommended_next_action=payload.recommended_next_action,
            section_id=section_id,
            module_id=module_id,
            cited_chunk_ids=cited_chunk_ids,
            assessment_model=self.settings.assessment_model or self.settings.openrouter_model,
            schema_version=self.schema_version,
            fallback_used=False,
            normalization_events=normalization_events,
        )
        log_event(
            logger,
            "assessment.completed",
            decision=result.decision.value,
            confidence=result.confidence,
            fallback_used=False,
            section_id=section_id,
            module_id=module_id,
            cited_chunk_count=len(result.cited_chunk_ids),
            recommended_next_action=result.recommended_next_action.value,
            normalization_event_count=len(result.normalization_events),
            normalization_events=list(result.normalization_events),
        )
        return result

    def build_fallback(
        self,
        *,
        learner_response: str,
        module_id: str | None,
        section_id: str | None,
        reason: str,
    ) -> AssessmentResult:
        _ = learner_response
        return AssessmentResult(
            decision=AssessmentDecision.INSUFFICIENT_EVIDENCE,
            confidence=0.0,
            learner_rationale=(
                "I could not reliably assess this response yet. Please restate your reasoning in one or two sentences."
            ),
            reasoning_summary=AssessmentReasoningSummary(
                evidence_basis="Fallback assessment used because the structured assessment was unavailable or invalid.",
                key_issue=self._clean_text(reason.replace("_", " "), 240),
                strength_signals=[],
                risk_flags=["fallback_used"],
            ),
            recommended_next_action=RecommendedNextAction.ASK_FOR_CLARIFICATION,
            section_id=section_id,
            module_id=module_id,
            cited_chunk_ids=[],
            assessment_model=self.settings.assessment_model or self.settings.openrouter_model,
            schema_version=self.schema_version,
            fallback_used=True,
            fallback_reason=self._clean_text(reason.replace("_", " "), 120),
        )

    async def _invoke(self, *, prompt_text: str) -> AssessmentStructuredPayload:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict pedagogical assessor. "
                    "Assess a learner response using only the provided topic evidence. "
                    "Return only structured JSON matching the schema. "
                    "Do not invent evidence. If the response is unrelated, choose off_topic. "
                    "If the response cannot be judged from the evidence, choose insufficient_evidence. "
                    "Distinguish misconception from procedural_error carefully: misconception means the learner states a conceptually false belief; "
                    "procedural_error means the learner applies a wrong step or method while attempting the topic."
                ),
            },
            {"role": "user", "content": prompt_text},
        ]
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._invoke_text_fallback(messages=messages)
            except (APIConnectionError, APITimeoutError, APIStatusError) as exc:
                last_exc = exc
                if not self._should_retry(exc=exc, attempt=attempt):
                    raise
                await asyncio.sleep(0.6 * attempt)
        assert last_exc is not None
        raise last_exc

    async def _invoke_text_fallback(self, *, messages: list[dict[str, str]]) -> AssessmentStructuredPayload:
        completion = await asyncio.wait_for(
            self._client.chat.completions.create(
                model=self.settings.assessment_model or self.settings.openrouter_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "The provider could not complete strict schema mode. "
                            "Return one raw JSON object only, with no markdown fences, no prose, and no extra text. "
                            "Use exactly the allowed decision enum values. "
                            "Use exactly the allowed recommended_next_action enum values. "
                            "Return reasoning_summary as an object with keys evidence_basis, key_issue, strength_signals, and risk_flags. "
                            "For citations, use only source labels such as S1."
                        ),
                    },
                    *messages,
                ],
                temperature=self.assessment_temperature,
                timeout=self.settings.assessment_timeout_seconds,
            ),
            timeout=self.settings.assessment_timeout_seconds,
        )
        message = completion.choices[0].message if completion.choices else None
        if message is None:
            raise RuntimeError("Assessment model returned no message in text fallback mode")
        payload_text = self._extract_message_text(message.content)
        if not payload_text:
            raise RuntimeError("Assessment model returned empty text fallback content")
        payload_text = re.sub(r"```(?:json)?", "", payload_text, flags=re.IGNORECASE).strip()
        try:
            raw_payload = json.loads(payload_text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("Assessment model returned invalid fallback JSON") from exc
        try:
            return AssessmentStructuredPayload.model_validate(self._coerce_fallback_payload(raw_payload))
        except ValueError as exc:
            raise RuntimeError("Assessment model returned invalid normalized fallback JSON") from exc

    def _build_context_block(
        self,
        chunks: list[dict[str, Any]],
    ) -> tuple[str, dict[str, str]]:
        blocks: list[str] = []
        source_map: dict[str, str] = {}
        for chunk in chunks:
            chunk_id = str(chunk.get("chunk_id") or "")
            if not chunk_id:
                continue
            label = f"S{len(source_map) + 1}"
            source_map[label] = chunk_id
            title = self._clean_text(str(chunk.get("title") or "Untitled"), 160)
            excerpt = self._prompt_excerpt(clean_chunk_text(chunk_text(chunk)))
            blocks.append(
                f"[SOURCE {label}]\n"
                f"Title: {title}\n"
                f"Text:\n{excerpt}"
            )
        return "\n\n".join(blocks), source_map

    def _build_prompt(
        self,
        *,
        learner_response: str,
        prompt_context: str | None,
        tutor_answer: str | None,
        module_id: str | None,
        section_id: str | None,
        context_block: str,
    ) -> str:
        return (
            "Assess the learner response against the current topic evidence.\n\n"
            "Decision options:\n"
            "- correct\n"
            "- partially_correct\n"
            "- misconception\n"
            "- procedural_error\n"
            "- off_topic\n"
            "- insufficient_evidence\n\n"
            "Recommended next action options:\n"
            "- affirm_and_advance\n"
            "- reinforce_key_point\n"
            "- correct_misconception\n"
            "- request_step_revision\n"
            "- ask_for_clarification\n"
            "- redirect_to_topic\n\n"
            "Rules:\n"
            "- Base the assessment only on the learner response and the provided evidence.\n"
            "- Keep learner_rationale brief and directly usable by the tutor.\n"
            "- Cite only source labels from the evidence block.\n"
            "- Use empty citations when evidence is insufficient or the response is off-topic.\n\n"
            "Decision guidance:\n"
            "- correct: materially correct and aligned with the evidence\n"
            "- partially_correct: partly right but missing an important qualifier or detail\n"
            "- misconception: expresses a false conceptual belief\n"
            "- procedural_error: uses the wrong method or omits a required step, even if the learner names the right concept\n"
            "- off_topic: unrelated to the prompt/topic\n"
            "- insufficient_evidence: too vague, hedged, or underspecified to judge reliably\n\n"
            "Boundary cases:\n"
            "- If the learner says 'I think', 'kind of', or gives only a fuzzy paraphrase without the needed concept detail, prefer insufficient_evidence.\n"
            "- If the learner applies a recognizable concept but misses a required operation or intermediate derivative step, prefer procedural_error over misconception.\n\n"
            f"Module ID: {module_id or 'unknown'}\n"
            f"Section ID: {section_id or 'unknown'}\n"
            f"Prompt context: {self._clean_text(prompt_context or 'none', 400)}\n"
            f"Tutor answer/context: {self._clean_text(tutor_answer or 'none', 700)}\n"
            f"Learner response: {self._clean_text(learner_response, 700)}\n\n"
            f"Evidence:\n{context_block}"
        )

    @staticmethod
    def _validate_semantics(
        *,
        payload: AssessmentStructuredPayload,
        cited_chunk_ids: list[str],
    ) -> None:
        valid_actions = VALID_ACTIONS_BY_DECISION[payload.decision]
        if payload.recommended_next_action not in valid_actions:
            raise RuntimeError(
                "Assessment recommended_next_action is inconsistent with decision"
            )
        if payload.decision in {
            AssessmentDecision.OFF_TOPIC,
            AssessmentDecision.INSUFFICIENT_EVIDENCE,
        } and cited_chunk_ids:
            raise RuntimeError("Assessment citations must be empty for off-topic or insufficient-evidence decisions")
        if payload.decision not in {
            AssessmentDecision.OFF_TOPIC,
            AssessmentDecision.INSUFFICIENT_EVIDENCE,
        } and not cited_chunk_ids:
            raise RuntimeError("Assessment citations must reference evidence for grounded decisions")

    @staticmethod
    def _normalize_payload(
        payload: AssessmentStructuredPayload,
    ) -> tuple[AssessmentStructuredPayload, list[str]]:
        valid_actions = VALID_ACTIONS_BY_DECISION[payload.decision]
        if payload.recommended_next_action in valid_actions:
            return payload, []
        normalized_action = QuizAssessmentService._canonical_action_for_decision(payload.decision.value)
        return (
            payload.model_copy(update={"recommended_next_action": normalized_action}),
            [f"recommended_next_action:{payload.decision.value}->{normalized_action.value}"],
        )

    @staticmethod
    def _should_retry(*, exc: Exception, attempt: int) -> bool:
        if attempt >= QuizAssessmentService.max_retries:
            return False
        if isinstance(exc, APIStatusError):
            return exc.status_code is not None and int(exc.status_code) >= 500
        return isinstance(exc, (APIConnectionError, APITimeoutError))

    @staticmethod
    def _fallback_reason_from_exception(exc: Exception) -> str:
        if isinstance(exc, APIStatusError):
            return f"provider_status_{exc.status_code or 'unknown'}"
        if isinstance(exc, APIConnectionError):
            return "provider_connection_error"
        if isinstance(exc, APITimeoutError):
            return "provider_timeout"
        message = str(exc).lower()
        if "recommended_next_action" in message:
            return "semantic_action_mismatch"
        if "citations must be empty" in message:
            return "semantic_unexpected_citations"
        if "citations must reference evidence" in message:
            return "semantic_missing_citations"
        return "structured_assessment_failed"

    @staticmethod
    def _map_citations(
        labels: list[str],
        source_map: dict[str, str],
        *,
        decision: AssessmentDecision,
    ) -> list[str]:
        chunk_ids: list[str] = []
        seen: set[str] = set()
        for label in labels:
            candidate_labels = SOURCE_LABEL_RE.findall(str(label))
            if not candidate_labels and str(label) in source_map:
                candidate_labels = [str(label)]
            for candidate in candidate_labels:
                chunk_id = source_map.get(candidate)
                if chunk_id is None:
                    chunk_id = source_map.get(candidate.upper())
                if chunk_id is None:
                    continue
                if chunk_id not in seen:
                    seen.add(chunk_id)
                    chunk_ids.append(chunk_id)
        if not chunk_ids and decision not in {
            AssessmentDecision.OFF_TOPIC,
            AssessmentDecision.INSUFFICIENT_EVIDENCE,
        }:
            first_chunk_id = next(iter(source_map.values()), None)
            if first_chunk_id:
                return [first_chunk_id]
        return chunk_ids

    @staticmethod
    def _coerce_fallback_payload(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise RuntimeError("Assessment model returned non-object fallback JSON")
        payload = dict(raw)
        decision = str(payload.get("decision") or "").strip().lower()
        payload["decision"] = decision
        confidence = payload.get("confidence")
        if confidence is None:
            payload["confidence"] = QuizAssessmentService._default_confidence_for_decision(decision)
        elif isinstance(confidence, str):
            payload["confidence"] = float(confidence)
        reasoning_summary = payload.get("reasoning_summary")
        if isinstance(reasoning_summary, str):
            payload["reasoning_summary"] = {
                "evidence_basis": QuizAssessmentService._clean_text(reasoning_summary, 280),
                "key_issue": None,
                "strength_signals": [],
                "risk_flags": [],
            }
        elif isinstance(reasoning_summary, dict):
            normalized_summary = dict(reasoning_summary)
            for field in ("strength_signals", "risk_flags"):
                value = normalized_summary.get(field)
                if value is None:
                    normalized_summary[field] = []
                elif isinstance(value, str):
                    cleaned = QuizAssessmentService._clean_text(value, 180)
                    normalized_summary[field] = [] if cleaned.lower() in {"", "none", "none."} else [cleaned]
                elif isinstance(value, list):
                    normalized_summary[field] = [QuizAssessmentService._clean_text(str(item), 180) for item in value]
            payload["reasoning_summary"] = normalized_summary
        recommended = str(payload.get("recommended_next_action") or "").strip().lower()
        if recommended not in {item.value for item in RecommendedNextAction}:
            payload["recommended_next_action"] = QuizAssessmentService._normalize_recommended_next_action(
                recommended,
                decision=decision,
            ).value
        citations = payload.get("citations")
        if citations is None:
            payload["citations"] = []
        elif not isinstance(citations, list):
            payload["citations"] = [str(citations)]
        else:
            payload["citations"] = [str(item) for item in citations if str(item).strip()]
        return payload

    @staticmethod
    def _default_confidence_for_decision(decision: str) -> float:
        if decision == AssessmentDecision.CORRECT.value:
            return 0.9
        if decision == AssessmentDecision.PARTIALLY_CORRECT.value:
            return 0.65
        if decision in {
            AssessmentDecision.MISCONCEPTION.value,
            AssessmentDecision.PROCEDURAL_ERROR.value,
            AssessmentDecision.OFF_TOPIC.value,
        }:
            return 0.85
        return 0.35

    @staticmethod
    def _normalize_recommended_next_action(
        text: str,
        *,
        decision: str,
    ) -> RecommendedNextAction:
        normalized = str(text or "").lower()
        if any(term in normalized for term in ("advance", "move on", "next topic")):
            return RecommendedNextAction.AFFIRM_AND_ADVANCE
        if any(term in normalized for term in ("misconception", "correct belief", "address belief")):
            return RecommendedNextAction.CORRECT_MISCONCEPTION
        if any(term in normalized for term in ("step", "show work", "revise", "try again", "method")):
            return RecommendedNextAction.REQUEST_STEP_REVISION
        if any(term in normalized for term in ("redirect", "stay on topic", "back to topic")):
            return RecommendedNextAction.REDIRECT_TO_TOPIC
        if any(term in normalized for term in ("clarify", "more detail", "restate", "explain more")):
            return RecommendedNextAction.ASK_FOR_CLARIFICATION
        return QuizAssessmentService._canonical_action_for_decision(decision)

    @staticmethod
    def _canonical_action_for_decision(decision: str) -> RecommendedNextAction:
        if decision == AssessmentDecision.CORRECT.value:
            return RecommendedNextAction.AFFIRM_AND_ADVANCE
        if decision == AssessmentDecision.MISCONCEPTION.value:
            return RecommendedNextAction.CORRECT_MISCONCEPTION
        if decision == AssessmentDecision.PROCEDURAL_ERROR.value:
            return RecommendedNextAction.REQUEST_STEP_REVISION
        if decision == AssessmentDecision.OFF_TOPIC.value:
            return RecommendedNextAction.REDIRECT_TO_TOPIC
        if decision == AssessmentDecision.PARTIALLY_CORRECT.value:
            return RecommendedNextAction.REINFORCE_KEY_POINT
        return RecommendedNextAction.ASK_FOR_CLARIFICATION

    @staticmethod
    def _extract_message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                if item.get("type") in {"text", "output_text"} and isinstance(item.get("text"), str):
                    parts.append(str(item["text"]))
                continue
            text = getattr(item, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts).strip()

    def _prompt_excerpt(self, text: str) -> str:
        if len(text) <= self.context_char_limit:
            return text
        return text[: self.context_char_limit].rstrip() + "..."

    @staticmethod
    def _clean_text(text: str, max_len: int) -> str:
        cleaned = str(text or "").replace("\x00", " ").strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if len(cleaned) <= max_len:
            return cleaned
        return cleaned[:max_len].rstrip() + "..."
