from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_chat_service, get_tutor_service
from app.api.routes import router
from app.learner.models import MasteryUpdate
from app.platform.config import Settings
from app.tutor.service import TutorService


class TutorSessionFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repo = SimpleNamespace()
        self.learner_state_service = SimpleNamespace(
            record_feedback_update=AsyncMock(),
        )
        self.service = TutorService(
            repository=self.repo,
            learner_service=self.learner_state_service,
            vector_store=SimpleNamespace(),
            lesson_generator=SimpleNamespace(
                settings=Settings(lesson_gen_format_version=6),
                generator_version="g",
                prompt_profile_version="p",
            ),
            llm_client=None,
            book_json_path="data/book.json",
            settings=Settings(),
        )
        self.service.ensure_context = AsyncMock(
            return_value=(
                {"id": "default_calc1"},
                {"learner_id": "learner-1"},
                [],
                {"stage_index": 0, "section_id": "limits", "module_id": "m1"},
            )
        )
        self.service.mastery_map = AsyncMock(return_value={"limits": 0.5})

    async def test_apply_feedback_uses_assessment_delta_for_correct(self) -> None:
        payload = await self.service.apply_feedback(
            learner_id="learner-1",
            interaction_id=101,
            section_id="limits",
            module_id="m1",
            confidence=1,
            assessment={"decision": "correct", "fallback_used": False},
        )
        update = self.learner_state_service.record_feedback_update.await_args.args[0]
        self.assertIsInstance(update, MasteryUpdate)
        self.assertEqual(update.interaction_id, 101)
        self.assertEqual(update.source_kind, "feedback_assessment")
        self.assertEqual(update.assessment_decision, "correct")
        self.assertEqual(update.mastery_before, 0.5)
        self.assertEqual(update.mastery_after, 0.7)
        self.assertEqual(update.status_after, "in_progress")
        self.assertEqual(update.active_template_id, "default_calc1")
        self.assertEqual(update.confidence_submitted, 1)
        self.assertEqual(update.recommended_next_action, None)
        self.assertEqual(update.module_id, "m1")
        self.assertEqual(update.section_id, "limits")
        self.assertEqual(update.learner_id, "learner-1")
        self.assertAlmostEqual(update.mastery_delta, 0.2)
        self.assertFalse(payload["auto_advanced"])

    async def test_apply_feedback_uses_assessment_delta_for_procedural_error(self) -> None:
        await self.service.apply_feedback(
            learner_id="learner-1",
            interaction_id=102,
            section_id="limits",
            module_id="m1",
            confidence=5,
            assessment={
                "decision": "procedural_error",
                "recommended_next_action": "request_step_revision",
                "fallback_used": False,
            },
        )
        update = self.learner_state_service.record_feedback_update.await_args.args[0]
        self.assertEqual(update.source_kind, "feedback_assessment")
        self.assertEqual(update.recommended_next_action, "request_step_revision")
        self.assertEqual(update.mastery_after, 0.4)
        self.assertAlmostEqual(update.mastery_delta, -0.1)

    async def test_apply_feedback_uses_assessment_delta_for_misconception(self) -> None:
        await self.service.apply_feedback(
            learner_id="learner-1",
            interaction_id=103,
            section_id="limits",
            module_id="m1",
            confidence=5,
            assessment={"decision": "misconception", "fallback_used": False},
        )
        update = self.learner_state_service.record_feedback_update.await_args.args[0]
        self.assertEqual(update.source_kind, "feedback_assessment")
        self.assertEqual(update.mastery_after, 0.35)
        self.assertAlmostEqual(update.mastery_delta, -0.15)

    async def test_apply_feedback_falls_back_to_confidence_when_assessment_missing(self) -> None:
        await self.service.apply_feedback(
            learner_id="learner-1",
            interaction_id=104,
            section_id="limits",
            module_id="m1",
            confidence=4,
            assessment=None,
        )
        update = self.learner_state_service.record_feedback_update.await_args.args[0]
        self.assertEqual(update.source_kind, "feedback_confidence")
        self.assertEqual(update.assessment_decision, None)
        self.assertEqual(update.mastery_after, 0.7)

    async def test_apply_feedback_falls_back_to_confidence_when_assessment_used_fallback(self) -> None:
        await self.service.apply_feedback(
            learner_id="learner-1",
            interaction_id=105,
            section_id="limits",
            module_id="m1",
            confidence=2,
            assessment={"decision": "insufficient_evidence", "fallback_used": True},
        )
        update = self.learner_state_service.record_feedback_update.await_args.args[0]
        self.assertEqual(update.source_kind, "feedback_confidence")
        self.assertEqual(update.assessment_decision, "insufficient_evidence")
        self.assertEqual(update.mastery_after, 0.4)

    async def test_apply_feedback_clamps_and_marks_completed_at_threshold(self) -> None:
        self.service.mastery_map = AsyncMock(return_value={"limits": 0.75})
        await self.service.apply_feedback(
            learner_id="learner-1",
            interaction_id=106,
            section_id="limits",
            module_id="m1",
            confidence=1,
            assessment={"decision": "correct", "fallback_used": False},
        )
        update = self.learner_state_service.record_feedback_update.await_args.args[0]
        self.assertEqual(update.status_after, "completed")
        self.assertEqual(update.mastery_after, 0.95)


class FeedbackRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = FastAPI()
        self.app.include_router(router, prefix="/v1")

        self.feedback_service = SimpleNamespace(
            apply_feedback=AsyncMock(
                return_value={
                    "auto_advanced": False,
                    "message": "Feedback saved. Continue when ready.",
                    "current_stage": {"stage_index": 0, "section_id": "limits", "module_id": "m1"},
                }
            )
        )
        self.chat_service = SimpleNamespace(
            get_feedback_context=AsyncMock(),
            record_feedback_confidence=AsyncMock(),
        )
        self.tutor_service = SimpleNamespace(
            ensure_learner=AsyncMock(),
            apply_feedback=self.feedback_service.apply_feedback,
        )

        self.app.dependency_overrides[get_chat_service] = lambda: self.chat_service
        self.app.dependency_overrides[get_tutor_service] = lambda: self.tutor_service
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()

    def test_feedback_route_uses_stored_quiz_assessment(self) -> None:
        self.chat_service.get_feedback_context.return_value = {
            "interaction": {
                "id": 11,
                "learner_id": "learner-1",
                "section_id": "limits",
                "module_id": "m1",
            },
            "assessment": {
                "interaction_id": 11,
                "decision": "correct",
                "fallback_used": False,
            },
        }

        response = self.client.post(
            "/v1/feedback",
            json={"learner_id": "learner-1", "interaction_id": 11, "confidence": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.tutor_service.ensure_learner.assert_awaited_once_with("learner-1")
        self.chat_service.get_feedback_context.assert_awaited_once_with("learner-1", 11)
        self.chat_service.record_feedback_confidence.assert_awaited_once_with(11, 1)
        self.feedback_service.apply_feedback.assert_awaited_once_with(
            "learner-1",
            11,
            "limits",
            "m1",
            1,
            {"interaction_id": 11, "decision": "correct", "fallback_used": False},
        )

    def test_feedback_route_keeps_legacy_path_when_no_assessment_exists(self) -> None:
        self.chat_service.get_feedback_context.return_value = {
            "interaction": {
                "id": 12,
                "learner_id": "learner-1",
                "section_id": "limits",
                "module_id": "m1",
            },
            "assessment": None,
        }

        response = self.client.post(
            "/v1/feedback",
            json={"learner_id": "learner-1", "interaction_id": 12, "confidence": 4},
        )

        self.assertEqual(response.status_code, 200)
        self.feedback_service.apply_feedback.assert_awaited_once_with(
            "learner-1",
            12,
            "limits",
            "m1",
            4,
            None,
        )

    def test_feedback_route_preserves_interaction_ownership_validation(self) -> None:
        self.chat_service.get_feedback_context.return_value = None

        response = self.client.post(
            "/v1/feedback",
            json={"learner_id": "learner-1", "interaction_id": 13, "confidence": 4},
        )

        self.assertEqual(response.status_code, 404)
        self.chat_service.record_feedback_confidence.assert_not_awaited()
        self.feedback_service.apply_feedback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
