from __future__ import annotations

import asyncio
import unittest

from app.chat.assessment import QuizAssessmentService
from app.chat.models import (
    AssessmentDecision,
    AssessmentReasoningSummary,
    AssessmentStructuredPayload,
    RecommendedNextAction,
)
from app.platform.config import Settings


class QuizAssessmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = QuizAssessmentService(
            Settings(openrouter_model="openai/gpt-5-mini", assessment_model="openai/gpt-5-mini"),
            llm_client=None,
        )

    def test_build_fallback_is_deterministic(self) -> None:
        result = self.service.build_fallback(
            learner_response="Maybe it is the opposite function.",
            module_id="module-1",
            section_id="section-1",
            reason="structured_assessment_failed",
        )
        self.assertEqual(result.decision, AssessmentDecision.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.recommended_next_action, RecommendedNextAction.ASK_FOR_CLARIFICATION)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.section_id, "section-1")
        self.assertEqual(result.module_id, "module-1")
        self.assertEqual(result.cited_chunk_ids, [])

    def test_map_citations_rejects_unknown_labels(self) -> None:
        mapped = self.service._map_citations(
            ["S2"],
            {"S1": "chunk-1"},
            decision=AssessmentDecision.CORRECT,
        )
        self.assertEqual(mapped, ["chunk-1"])

    def test_assess_returns_fallback_for_non_quiz_mode(self) -> None:
        result = asyncio.run(
            self.service.assess(
                learner_response="What is a derivative?",
                mode="tutor",
                chunks=[{"chunk_id": "chunk-1", "title": "Derivative", "content_text": "Derivative evidence"}],
                context={"module_id": None, "section_id": None},
                tutor_answer="A derivative measures rate of change.",
            )
        )
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.decision, AssessmentDecision.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.schema_version, self.service.schema_version)

    def test_validate_semantics_rejects_grounded_decision_without_citations(self) -> None:
        payload = AssessmentStructuredPayload(
            decision=AssessmentDecision.CORRECT,
            confidence=0.9,
            learner_rationale="That is correct.",
            reasoning_summary=AssessmentReasoningSummary(
                evidence_basis="Matches the evidence.",
                key_issue=None,
                strength_signals=["correct definition"],
                risk_flags=[],
            ),
            recommended_next_action=RecommendedNextAction.AFFIRM_AND_ADVANCE,
            citations=[],
        )
        with self.assertRaises(RuntimeError):
            self.service._validate_semantics(payload=payload, cited_chunk_ids=[])

    def test_validate_semantics_rejects_wrong_action_for_off_topic(self) -> None:
        payload = AssessmentStructuredPayload(
            decision=AssessmentDecision.OFF_TOPIC,
            confidence=0.9,
            learner_rationale="That response is unrelated.",
            reasoning_summary=AssessmentReasoningSummary(
                evidence_basis="No overlap with the topic.",
                key_issue="off topic",
                strength_signals=[],
                risk_flags=["off_topic"],
            ),
            recommended_next_action=RecommendedNextAction.AFFIRM_AND_ADVANCE,
            citations=[],
        )
        with self.assertRaises(RuntimeError):
            self.service._validate_semantics(payload=payload, cited_chunk_ids=[])

    def test_normalize_recommended_action_uses_decision_default(self) -> None:
        action = self.service._normalize_recommended_next_action(
            "Provide more examples",
            decision=AssessmentDecision.PARTIALLY_CORRECT.value,
        )
        self.assertEqual(action, RecommendedNextAction.REINFORCE_KEY_POINT)

    def test_normalize_payload_repairs_procedural_error_action_mismatch(self) -> None:
        payload = AssessmentStructuredPayload(
            decision=AssessmentDecision.PROCEDURAL_ERROR,
            confidence=0.85,
            learner_rationale="The learner skipped the inner derivative.",
            reasoning_summary=AssessmentReasoningSummary(
                evidence_basis="The answer omits the chain rule multiplier.",
                key_issue="missing inner derivative",
                strength_signals=["recognized the outer derivative"],
                risk_flags=["step_missing"],
            ),
            recommended_next_action=RecommendedNextAction.ASK_FOR_CLARIFICATION,
            citations=["S1"],
        )
        normalized, events = self.service._normalize_payload(payload)
        self.assertEqual(normalized.recommended_next_action, RecommendedNextAction.REQUEST_STEP_REVISION)
        self.assertEqual(
            events,
            ["recommended_next_action:procedural_error->request_step_revision"],
        )

    def test_normalize_payload_repairs_misconception_action_mismatch(self) -> None:
        payload = AssessmentStructuredPayload(
            decision=AssessmentDecision.MISCONCEPTION,
            confidence=0.85,
            learner_rationale="The learner thinks all definite integrals are positive.",
            reasoning_summary=AssessmentReasoningSummary(
                evidence_basis="The answer contradicts signed area.",
                key_issue="false conceptual belief",
                strength_signals=[],
                risk_flags=["conceptual_error"],
            ),
            recommended_next_action=RecommendedNextAction.AFFIRM_AND_ADVANCE,
            citations=["S1"],
        )
        normalized, events = self.service._normalize_payload(payload)
        self.assertEqual(normalized.recommended_next_action, RecommendedNextAction.CORRECT_MISCONCEPTION)
        self.assertEqual(
            events,
            ["recommended_next_action:misconception->correct_misconception"],
        )

    def test_normalize_payload_repairs_partially_correct_action_mismatch(self) -> None:
        payload = AssessmentStructuredPayload(
            decision=AssessmentDecision.PARTIALLY_CORRECT,
            confidence=0.65,
            learner_rationale="The learner has the core idea but missed a qualifier.",
            reasoning_summary=AssessmentReasoningSummary(
                evidence_basis="The answer captures slope but not point-specificity.",
                key_issue="missing qualifier",
                strength_signals=["correct core concept"],
                risk_flags=["incomplete"],
            ),
            recommended_next_action=RecommendedNextAction.REDIRECT_TO_TOPIC,
            citations=["S1"],
        )
        normalized, events = self.service._normalize_payload(payload)
        self.assertEqual(normalized.recommended_next_action, RecommendedNextAction.REINFORCE_KEY_POINT)
        self.assertEqual(
            events,
            ["recommended_next_action:partially_correct->reinforce_key_point"],
        )

    def test_coerce_fallback_payload_normalizes_missing_confidence_and_string_lists(self) -> None:
        payload = self.service._coerce_fallback_payload(
            {
                "decision": "correct",
                "learner_rationale": "The answer matches the definition.",
                "reasoning_summary": {
                    "evidence_basis": "Matches S1.",
                    "key_issue": None,
                    "strength_signals": "Correctly states the two-sided limit idea.",
                    "risk_flags": "None.",
                },
                "recommended_next_action": "affirm_and_advance",
            }
        )
        self.assertEqual(payload["confidence"], 0.9)
        self.assertEqual(payload["reasoning_summary"]["strength_signals"], ["Correctly states the two-sided limit idea."])
        self.assertEqual(payload["reasoning_summary"]["risk_flags"], [])


if __name__ == "__main__":
    unittest.main()
