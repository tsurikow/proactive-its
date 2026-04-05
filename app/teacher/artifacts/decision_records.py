from __future__ import annotations

from typing import Any

from app.state.models import AdaptationContext
from app.platform.logging import log_event
from app.teacher.artifacts.artifact_keys import (
    grounding_analysis_fingerprint,
    learner_memory_fingerprint,
    lesson_plan_fingerprint,
    section_understanding_artifact_key,
)
from app.teacher.artifacts.models import (
    GroundingAnalysis,
    LearnerMemorySummary,
    LessonPlanDraft,
    ProgressionPolicyDecision,
)
from app.teacher.models import (
    SectionUnderstandingArtifact,
    TeacherArtifact,
)


class TeacherArtifactRecorder:
    def __init__(self, *, repository: Any, logger: Any):
        self.repository = repository
        self.logger = logger

    async def record_section_understanding_artifact(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        artifact: SectionUnderstandingArtifact,
        decision_source: str,
        fallback_reason: str | None,
    ) -> dict[str, Any]:
        stage_payload = dict(stage or {})
        teacher_artifact = TeacherArtifact(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=int(stage_payload.get("stage_index", -1)),
            section_id=str(stage_payload.get("section_id") or ""),
            module_id=str(stage_payload.get("module_id")) if stage_payload.get("module_id") is not None else None,
            decision_kind="section_understanding",
            artifact_key=section_understanding_artifact_key(
                section_id=artifact.section_id,
                source_hash=artifact.source_hash,
                context_version=artifact.context_version,
            ),
            stage_signal=adaptation_context.stage_signal,
            decision_source=decision_source,
            context_version=artifact.context_version,
            effective_mastery_score=None
            if adaptation_context.current_topic is None
            else adaptation_context.current_topic.effective_mastery_score,
            weak_topic_count=len(adaptation_context.weak_related_topics),
            module_evidence_coverage=adaptation_context.module_summary.get("evidence_coverage_ratio"),
            fallback_reason=fallback_reason,
            decision_payload_json=artifact.model_dump(mode="json"),
        )
        return await self.repository.write_section_understanding(teacher_artifact)

    async def record_learner_memory_summary_decision(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any] | None,
        adaptation_context: AdaptationContext,
        learner_memory_summary: LearnerMemorySummary,
    ) -> dict[str, Any]:
        fingerprint = learner_memory_fingerprint(learner_memory_summary)
        stage_payload = dict(stage or {})
        tutor_decision = TeacherArtifact(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=int(stage_payload.get("stage_index", -1)),
            section_id=str(stage_payload.get("section_id") or ""),
            module_id=str(stage_payload.get("module_id")) if stage_payload.get("module_id") is not None else None,
            decision_kind="learner_memory_summary",
            artifact_key=f"learner_memory_summary:{fingerprint}",
            stage_signal=adaptation_context.stage_signal,
            decision_source="llm",
            context_version=adaptation_context.context_version,
            effective_mastery_score=None
            if adaptation_context.current_topic is None
            else adaptation_context.current_topic.effective_mastery_score,
            weak_topic_count=len(adaptation_context.weak_related_topics),
            module_evidence_coverage=adaptation_context.module_summary.get("evidence_coverage_ratio"),
            decision_payload_json=learner_memory_summary.model_dump(mode="json"),
        )
        return await self.repository.append_teacher_artifact(tutor_decision)

    async def record_grounding_analysis_decision(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        grounding_analysis: GroundingAnalysis,
    ) -> dict[str, Any]:
        fingerprint = grounding_analysis_fingerprint(grounding_analysis)
        tutor_decision = TeacherArtifact(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=int(stage.get("stage_index", -1)),
            section_id=str(stage.get("section_id") or ""),
            module_id=str(stage.get("module_id")) if stage.get("module_id") is not None else None,
            decision_kind="grounding_analysis",
            artifact_key=f"grounding_analysis:{fingerprint}",
            stage_signal=adaptation_context.stage_signal,
            decision_source="llm",
            context_version=adaptation_context.context_version,
            effective_mastery_score=None
            if adaptation_context.current_topic is None
            else adaptation_context.current_topic.effective_mastery_score,
            weak_topic_count=len(adaptation_context.weak_related_topics),
            module_evidence_coverage=adaptation_context.module_summary.get("evidence_coverage_ratio"),
            decision_payload_json=grounding_analysis.model_dump(mode="json"),
        )
        return await self.repository.append_teacher_artifact(tutor_decision)

    async def record_lesson_plan_decision(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        lesson_plan_draft: LessonPlanDraft,
    ) -> dict[str, Any]:
        fingerprint = lesson_plan_fingerprint(lesson_plan_draft)
        tutor_decision = TeacherArtifact(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=int(stage.get("stage_index", -1)),
            section_id=str(stage.get("section_id") or ""),
            module_id=str(stage.get("module_id")) if stage.get("module_id") is not None else None,
            decision_kind="lesson_plan",
            artifact_key=f"lesson_plan:{fingerprint}",
            stage_signal=adaptation_context.stage_signal,
            decision_source="llm",
            context_version=adaptation_context.context_version,
            effective_mastery_score=None
            if adaptation_context.current_topic is None
            else adaptation_context.current_topic.effective_mastery_score,
            weak_topic_count=len(adaptation_context.weak_related_topics),
            module_evidence_coverage=adaptation_context.module_summary.get("evidence_coverage_ratio"),
            decision_payload_json=lesson_plan_draft.model_dump(mode="json"),
        )
        return await self.repository.append_teacher_artifact(tutor_decision)

    async def record_progression_decision(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any] | None,
        adaptation_context: AdaptationContext,
        progression_variant: str,
        decision_source: str,
        fallback_reason: str | None,
        decision: ProgressionPolicyDecision,
    ) -> dict[str, Any]:
        stage_payload = dict(stage or {})
        decision_record = TeacherArtifact(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=int(stage_payload.get("stage_index", -1)),
            section_id=str(stage_payload.get("section_id") or ""),
            module_id=str(stage_payload.get("module_id")) if stage_payload.get("module_id") is not None else None,
            decision_kind="progression",
            artifact_key=progression_variant,
            stage_signal=adaptation_context.stage_signal,
            decision_source=decision_source,
            context_version=decision.context_version,
            effective_mastery_score=None
            if adaptation_context.current_topic is None
            else adaptation_context.current_topic.effective_mastery_score,
            weak_topic_count=len(adaptation_context.weak_related_topics),
            module_evidence_coverage=adaptation_context.module_summary.get("evidence_coverage_ratio"),
            fallback_reason=fallback_reason,
            decision_payload_json=decision.model_dump(mode="json"),
        )
        result = await self.repository.append_teacher_artifact(decision_record)
        log_event(
            self.logger,
            "tutor.progression_recorded",
            learner_id=learner_id,
            template_id=template_id,
            stage_index=decision_record.stage_index,
            artifact_key=progression_variant,
            stage_signal=adaptation_context.stage_signal,
            decision_source=decision_source,
            fallback_reason=fallback_reason,
        )
        return result
