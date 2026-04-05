"""
Feedback processing — records mastery updates and generates follow-up messages.

Handles the confidence/assessment feedback from the learner after an interaction.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, NativeOutput

from app.platform.ai import run_native_agent
from app.state.services.learner_service import LearnerService
from app.state.repositories.session_repository import SessionStateRepository
from app.state.models import MasteryUpdate
from app.state.services.service import TeacherStateService
from app.platform.config import Settings, get_settings
from app.platform.logging import log_event
from app.state.stage_state import public_stage
from app.teacher.planning.message_builders import feedback_acknowledgement_message, feedback_followup_prompt
from app.teacher.repository import TeacherRepository

logger = logging.getLogger(__name__)


class FeedbackFollowupOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acknowledgement_focus: str = Field(
        description="Short description of what learner signal or next-step emphasis the acknowledgement should highlight.",
    )
    teacher_message: str = Field(
        description="Short learner-facing acknowledgement after feedback is recorded.",
    )


FEEDBACK_FOLLOWUP_AGENT: Agent[Any, FeedbackFollowupOutput] = Agent(
    None,
    output_type=NativeOutput(FeedbackFollowupOutput, strict=True),
    system_prompt=(
        "You are a concise adaptive teacher writing short feedback follow-ups. "
        "Keep messages brief, encouraging, and focused on what comes next."
    ),
    retries=1,
    defer_model_check=True,
)


class TeacherFeedbackRuntime:
    prompt_temperature = 0.3
    timeout_seconds = 8.0

    def __init__(
        self,
        *,
        repository: TeacherRepository,
        session_repository: SessionStateRepository,
        learner_service: LearnerService,
        state_service: TeacherStateService,
        settings: Settings | None = None,
    ):
        self.repository = repository
        self.session_repository = session_repository
        self.learner_service = learner_service
        self.state_service = state_service
        self.settings = settings or get_settings()

    async def apply_feedback(
        self,
        learner_id: str,
        interaction_id: int,
        section_id: str | None,
        module_id: str | None,
        confidence: int,
        assessment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template, state, targets, stage, adaptation_context = await self.state_service.ensure_context(learner_id)
        current_section_id = str(section_id or (stage or {}).get("section_id") or "")
        current_module_id = module_id or (stage or {}).get("module_id")
        new_mastery = None
        assessment_decision = str((assessment or {}).get("decision") or "") or None
        if current_section_id:
            mastery_map = await self.state_service.mastery_map(learner_id)
            current_mastery = mastery_map.get(current_section_id, 0.0)
            mastery_delta, update_source, assessment_decision, assessment_ignored_due_to_fallback = self._feedback_delta(
                confidence=confidence,
                assessment=assessment,
            )
            new_mastery = self._clamp(current_mastery + mastery_delta)
            status = "completed" if new_mastery >= 0.8 else "in_progress"
            await self.learner_service.record_feedback_update(
                MasteryUpdate(
                    learner_id=learner_id,
                    section_id=current_section_id,
                    module_id=current_module_id,
                    interaction_id=interaction_id,
                    source_kind=self._source_kind_for_update_source(update_source),
                    assessment_decision=str((assessment or {}).get("decision") or "") or None,
                    recommended_next_action=str((assessment or {}).get("recommended_next_action") or "") or None,
                    confidence_submitted=confidence,
                    mastery_delta=mastery_delta,
                    mastery_before=current_mastery,
                    mastery_after=new_mastery,
                    status_after=status,
                    active_template_id=str(template["id"]),
                )
            )
            log_event(
                logger,
                "feedback.applied",
                learner_id=learner_id,
                interaction_id=interaction_id,
                section_id=current_section_id,
                module_id=current_module_id,
                update_source=update_source,
                confidence=confidence,
                assessment_decision=assessment_decision,
                assessment_ignored_due_to_fallback=assessment_ignored_due_to_fallback,
                mastery_delta=mastery_delta,
                previous_mastery=round(current_mastery, 4),
                new_mastery=round(new_mastery, 4),
                status=status,
            )

        fallback_message = feedback_acknowledgement_message(
            confidence=confidence,
            assessment_decision=assessment_decision,
        )
        message = await self._feedback_followup_message(
            current_stage=stage,
            assessment_decision=assessment_decision,
            post_feedback_mastery=new_mastery,
            fallback_message=fallback_message,
        )
        if stage is not None:
            await self.learner_service.refresh_projection(learner_id, section_id=str(stage["section_id"]))
        return {
            "auto_advanced": False,
            "message": message,
            "current_stage": public_stage(stage),
        }

    async def _feedback_followup_message(
        self,
        *,
        current_stage: dict[str, Any] | None,
        assessment_decision: str | None,
        post_feedback_mastery: float | None,
        fallback_message: str,
    ) -> str:
        prompt = feedback_followup_prompt(
            current_stage=current_stage,
            assessment_decision=assessment_decision,
            post_feedback_mastery=post_feedback_mastery,
            applied_progression_variant="continue",
            target_stage=None,
            learner_teaching_brief=None,
            next_step_summary=fallback_message,
        )
        try:
            payload = await run_native_agent(
                FEEDBACK_FOLLOWUP_AGENT,
                settings=self.settings,
                prompt=prompt,
                model_name=self.settings.teacher_feedback_model or self.settings.openrouter_model,
                temperature=self.prompt_temperature,
                timeout_seconds=self.timeout_seconds,
                extra_body={"provider": {"require_parameters": True}},
            )
            message = str(payload.teacher_message or "").strip()
        except Exception:
            message = ""
        return message or fallback_message

    @staticmethod
    def _confidence_delta(confidence: int) -> float:
        if confidence >= 4:
            return 0.20
        if confidence == 3:
            return 0.05
        return -0.10

    @staticmethod
    def _assessment_delta(decision: str) -> float:
        if decision == "correct":
            return 0.20
        if decision == "partially_correct":
            return 0.08
        if decision == "misconception":
            return -0.15
        if decision == "procedural_error":
            return -0.10
        if decision in {"off_topic", "insufficient_evidence"}:
            return -0.05
        return -0.05

    @classmethod
    def _feedback_delta(cls, *, confidence: int, assessment: dict[str, Any] | None) -> tuple[float, str, str | None, bool]:
        if assessment and not bool(assessment.get("fallback_used")):
            decision = str(assessment.get("decision") or "").strip()
            if decision:
                return cls._assessment_delta(decision), "assessment", decision, False
        return cls._confidence_delta(confidence), "confidence", str((assessment or {}).get("decision") or "") or None, bool((assessment or {}).get("fallback_used"))

    @staticmethod
    def _source_kind_for_update_source(update_source: str) -> str:
        if update_source == "assessment":
            return "feedback_assessment"
        return "feedback_confidence"

    @staticmethod
    def _clamp(value: float) -> float:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
        return value


__all__ = ["TeacherFeedbackRuntime"]
