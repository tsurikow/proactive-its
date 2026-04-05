"""
Pending task resolution — selects and tracks the current task for a section.

Only stateless logic: select a task from section understanding, check whether
it's been resolved via session event history.
"""

from __future__ import annotations

from typing import Any

from app.state.repositories.session_repository import SessionStateRepository
from app.teacher.models import (
    CheckpointEvaluation,
    CheckpointEvaluationStatus,
    PendingTeacherTask,
    PendingTeacherTaskKind,
    SectionUnderstandingArtifact,
    TeacherAction,
    TeacherActionType,
)


class PendingTaskRuntime:

    def __init__(
        self,
        *,
        session_repository: SessionStateRepository,
    ) -> None:
        self.session_repository = session_repository

    @staticmethod
    def _select_pending_task(
        *,
        section_understanding: SectionUnderstandingArtifact | None,
    ) -> PendingTeacherTask | None:
        if section_understanding is None:
            return None
        if section_understanding.explicit_checkpoints:
            candidate = section_understanding.explicit_checkpoints[0]
            answer_check_context = next(
                (
                    item
                    for item in section_understanding.answer_check_contexts
                    if item.item_ref == candidate.checkpoint_ref
                ),
                None,
            )
            return PendingTeacherTask(
                task_kind=PendingTeacherTaskKind.CHECKPOINT_QUESTION,
                section_id=section_understanding.section_id,
                prompt_excerpt=candidate.prompt_excerpt,
                item_ref=candidate.checkpoint_ref,
                hidden_answer_ref=candidate.hidden_answer_ref,
                answer_check_context=answer_check_context,
            )
        if section_understanding.explicit_exercises:
            candidate = section_understanding.explicit_exercises[0]
            answer_check_context = next(
                (
                    item
                    for item in section_understanding.answer_check_contexts
                    if item.item_ref == candidate.exercise_ref
                ),
                None,
            )
            return PendingTeacherTask(
                task_kind=PendingTeacherTaskKind.SECTION_EXERCISE,
                section_id=section_understanding.section_id,
                prompt_excerpt=candidate.prompt_excerpt,
                item_ref=candidate.exercise_ref,
                hidden_answer_ref=candidate.hidden_answer_ref,
                answer_check_context=answer_check_context,
            )
        return None

    async def resolve_pending_task(
        self,
        *,
        learner_id: str,
        current_stage: dict[str, object] | None,
        section_understanding: SectionUnderstandingArtifact | None,
    ) -> PendingTeacherTask | None:
        base_task = self._select_pending_task(section_understanding=section_understanding)
        if base_task is None or current_stage is None:
            return None
        rows = await self.session_repository.list_recent_teacher_session_events(learner_id, limit=20)
        section_id = str(current_stage.get("section_id") or "")
        scoped_rows = [row for row in rows if str(row.get("section_id") or "") == section_id]
        attempt_count = 0
        resolved = False
        for row in scoped_rows:
            payload = dict(row.get("event_payload_json") or {})
            pending_payload = dict(payload.get("pending_task") or {})
            if pending_payload and str(pending_payload.get("item_ref") or "") != base_task.item_ref:
                continue
            evaluation_payload = payload.get("checkpoint_evaluation")
            if not isinstance(evaluation_payload, dict):
                continue
            status = str(evaluation_payload.get("status") or "")
            if status == CheckpointEvaluationStatus.CORRECT.value:
                resolved = True
                break
            if status in {
                CheckpointEvaluationStatus.PARTIAL.value,
                CheckpointEvaluationStatus.INCORRECT.value,
                CheckpointEvaluationStatus.UNRESOLVED.value,
            }:
                attempt_count += 1
        task = base_task.model_copy(update={"attempt_count": attempt_count, "resolved": resolved})
        return None if task.resolved else task

    @staticmethod
    def build_task_teacher_action(
        task: PendingTeacherTask,
        *,
        current_stage: dict[str, object] | None,
    ) -> TeacherAction:
        if task.task_kind == PendingTeacherTaskKind.CHECKPOINT_QUESTION:
            return TeacherAction(
                action_type=TeacherActionType.ASK_SECTION_QUESTION,
                rationale="Pause on the current checkpoint before moving on.",
                section_id=None if current_stage is None else str(current_stage.get("section_id") or ""),
                module_id=None
                if current_stage is None or current_stage.get("module_id") is None
                else str(current_stage.get("module_id")),
                question_prompt=task.prompt_excerpt,
                hidden_answer_ref=task.hidden_answer_ref,
                requires_learner_reply=True,
                allows_move_on=True,
            )
        return TeacherAction(
            action_type=TeacherActionType.ASSIGN_SECTION_EXERCISE,
            rationale="Pause on the current source-backed exercise before moving on.",
            section_id=None if current_stage is None else str(current_stage.get("section_id") or ""),
            module_id=None
            if current_stage is None or current_stage.get("module_id") is None
            else str(current_stage.get("module_id")),
            exercise_ref=task.item_ref,
            hidden_answer_ref=task.hidden_answer_ref,
            requires_learner_reply=True,
            allows_move_on=True,
        )

    @staticmethod
    def fallback_checkpoint_evaluation(
        *,
        task: PendingTeacherTask,
    ) -> CheckpointEvaluation:
        exercise_ref = task.item_ref if task.task_kind == PendingTeacherTaskKind.SECTION_EXERCISE else None
        if task.answer_check_context is None or not task.answer_check_context.can_verify:
            return CheckpointEvaluation(
                status=CheckpointEvaluationStatus.UNRESOLVED,
                section_id=task.section_id,
                exercise_ref=exercise_ref,
                evaluator_source="fallback",
                hidden_answer_used=False,
                missing_or_wrong_piece="No source-backed verification basis is available for this task.",
                rationale="No source-backed answer-check context is available for reliable verification.",
                teacher_feedback_brief="I cannot verify that answer reliably from the source, so explain your reasoning or move on anyway.",
                confidence=0.0,
            )
        return CheckpointEvaluation(
            status=CheckpointEvaluationStatus.UNRESOLVED,
            section_id=task.section_id,
            exercise_ref=exercise_ref,
            evaluator_source="fallback",
            hidden_answer_used=True,
            missing_or_wrong_piece="The answer is still too unclear or underspecified for a reliable source-backed check right now.",
            rationale="Structured answer checking was unavailable, so the answer remains unresolved.",
            teacher_feedback_brief="I could not complete a reliable check just now. Try again with a clearer explanation, or move on anyway.",
            confidence=0.0,
        )


__all__ = ["PendingTaskRuntime"]
