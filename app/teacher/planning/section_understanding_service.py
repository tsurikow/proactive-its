from __future__ import annotations

import logging
from typing import Any

from app.content.indexing.section_source import CONTENT_NOT_READY, build_teacher_source_context, resolve_stage_source
from app.platform.vector_store import AsyncVectorStore
from app.state.models import AdaptationContext
from app.teacher.artifacts.artifact_keys import section_understanding_artifact_key
from app.teacher.artifacts.decision_records import TeacherArtifactRecorder
from app.teacher.engine import TeacherEngine
from app.teacher.models import (
    AnswerCheckContext,
    CheckpointCandidate,
    ExerciseCandidate,
    SectionSemanticType,
    SectionUnderstandingArtifact,
    StageSourcePayload,
    TeacherActionType,
    is_forbidden_control_char_error,
)
from app.teacher.planning.section_coverage import (
    classify_section_family,
    collect_section_issue_flags,
    normalize_section_understanding_artifact,
)
from app.teacher.repository import TeacherRepository
from app.teacher.schemas import SectionUnderstanding as SectionUnderstandingSGR

logger = logging.getLogger(__name__)


class SectionUnderstandingService:
    context_version = "section_understanding_v7"

    def __init__(
        self,
        *,
        repository: TeacherRepository,
        vector_store: AsyncVectorStore,
        engine: TeacherEngine,
    ) -> None:
        self.repository = repository
        self.vector_store = vector_store
        self.engine = engine
        self.decision_recorder = TeacherArtifactRecorder(repository=repository, logger=None)

    async def resolve_stage_source_payload(self, stage: dict[str, Any]) -> StageSourcePayload:
        source = await resolve_stage_source(self.vector_store, stage)
        return StageSourcePayload(
            section_id=source.section_id,
            parent_doc_id=source.parent_doc_id,
            title=source.title or (None if stage.get("title") is None else str(stage.get("title") or "") or None),
            breadcrumb=list(source.breadcrumb)
            or [str(item).strip() for item in stage.get("breadcrumb") or [] if str(item).strip()],
            source_markdown=source.source_markdown,
            source_hash=source.source_hash,
        )

    @classmethod
    def fallback_section_understanding(
        cls,
        current_stage: dict[str, Any] | None,
        source: StageSourcePayload | None = None,
    ) -> SectionUnderstandingArtifact | None:
        if current_stage is None:
            return None
        title = str(current_stage.get("title") or "").strip() or None
        breadcrumb = [str(item).strip() for item in current_stage.get("breadcrumb") or [] if str(item).strip()]
        return SectionUnderstandingArtifact(
            section_id=str(current_stage.get("section_id") or ""),
            source_hash=(str(getattr(source, "source_hash", "") or "") or f"fallback:{str(current_stage.get('section_id') or '')}"),
            parent_doc_id=None if source is None else source.parent_doc_id,
            title=None if source is None else (source.title or title),
            breadcrumb=breadcrumb if source is None or not source.breadcrumb else list(source.breadcrumb),
            pedagogical_role=SectionSemanticType.CORE_CONCEPT,
            teaching_intent="Use the current source as a general teaching anchor because structured section understanding is unavailable.",
            should_dwell=True,
            supports_generated_question=False,
            explicit_exercises=[],
            explicit_checkpoints=[],
            answer_check_contexts=[],
            recommended_actions=[TeacherActionType.TEACH_SECTION, TeacherActionType.CLARIFY_STUDENT_QUESTION],
            rationale="Fallback section understanding generated without book-specific semantic heuristics.",
            context_version=cls.context_version,
        )

    async def get_or_create_section_understanding(
        self,
        *,
        learner_id: str,
        template_id: str,
        current_stage: dict[str, Any] | None,
        adaptation_context: AdaptationContext | None,
    ) -> tuple[SectionUnderstandingArtifact | None, None]:
        artifact, _semantics, _diagnostics = await self.get_or_create_section_understanding_with_diagnostics(
            learner_id=learner_id,
            template_id=template_id,
            current_stage=current_stage,
            adaptation_context=adaptation_context,
        )
        return artifact, None

    async def get_or_create_section_understanding_with_diagnostics(
        self,
        *,
        learner_id: str,
        template_id: str,
        current_stage: dict[str, Any] | None,
        adaptation_context: AdaptationContext | None,
    ) -> tuple[SectionUnderstandingArtifact | None, None, dict[str, Any]]:
        if current_stage is None or adaptation_context is None:
            if current_stage is None:
                return None, None, self._empty_diagnostics(current_stage, source="missing_stage")
            return self._fallback_result(current_stage, fallback_reason="missing_adaptation_context")
        try:
            source = await self.resolve_stage_source_payload(current_stage)
        except ValueError as exc:
            return self._fallback_result(
                current_stage,
                fallback_reason="section_source_invalid_control_chars"
                if is_forbidden_control_char_error(exc)
                else "resolve_stage_source_exception",
            )
        except RuntimeError as exc:
            if str(exc) == CONTENT_NOT_READY:
                raise
            return self._fallback_result(current_stage, fallback_reason="resolve_stage_source_failed")
        except Exception:
            return self._fallback_result(current_stage, fallback_reason="resolve_stage_source_exception")

        artifact_key = section_understanding_artifact_key(
            section_id=source.section_id,
            source_hash=source.source_hash,
            context_version=self.context_version,
        )
        cached = await self.repository.get_section_understanding(
            learner_id=learner_id,
            template_id=template_id,
            stage_index=int(current_stage.get("stage_index", -1)),
            section_id=source.section_id,
            artifact_key=artifact_key,
            context_version=self.context_version,
        )
        if cached is not None:
            try:
                raw_artifact = SectionUnderstandingArtifact.model_validate(dict(cached.get("decision_payload_json") or {}))
            except ValueError:
                raw_artifact = None
            else:
                artifact = self.normalize_section_understanding_artifact(raw_artifact, current_stage=current_stage)
                diagnostics = self._section_understanding_diagnostics(
                    raw_artifact=raw_artifact,
                    normalized_artifact=artifact,
                    current_stage=current_stage,
                    decision_source=str(cached.get("decision_source") or "cached_unknown"),
                    fallback_reason=None if cached.get("fallback_reason") is None else str(cached.get("fallback_reason")),
                    cache_hit=True,
                )
                return artifact, None, diagnostics

        fallback_artifact = self.fallback_section_understanding(current_stage, source)
        if fallback_artifact is None:
            return None, None, {
                "source": "fallback",
                "fallback_used": True,
                "cache_hit": False,
                "section_family": classify_section_family(None, current_stage),
                "raw_issue_flags": [],
                "final_issue_flags": [],
            }
        source_context = build_teacher_source_context(
            source_markdown=source.source_markdown,
            title=source.title,
            breadcrumb=list(source.breadcrumb),
        )
        try:
            sgr_result = await self.engine.understand_section(
                section_id=source.section_id,
                title=source.title,
                breadcrumb=list(source.breadcrumb),
                source_markdown=source.source_markdown,
                contains_explicit_tasks=source_context.contains_explicit_tasks,
                contains_solution_like_content=source_context.contains_solution_like_content,
                contains_review_like_content=source_context.contains_review_like_content,
            )
            artifact = self._sgr_to_artifact(sgr_result, source=source)
            artifact_source = "llm"
            fallback_reason = None
        except Exception:
            logger.warning("SGR section understanding failed, using fallback", exc_info=True)
            artifact = fallback_artifact
            artifact_source = "fallback"
            fallback_reason = "sgr_call_failed"
        normalized_artifact = self.normalize_section_understanding_artifact(artifact, current_stage=current_stage)
        if artifact_source == "llm" and adaptation_context is not None:
            await self.decision_recorder.record_section_understanding_artifact(
                learner_id=learner_id,
                template_id=template_id,
                stage=current_stage,
                adaptation_context=adaptation_context,
                artifact=normalized_artifact,
                decision_source=artifact_source,
                fallback_reason=fallback_reason,
            )
        diagnostics = self._section_understanding_diagnostics(
            raw_artifact=artifact,
            normalized_artifact=normalized_artifact,
            current_stage=current_stage,
            decision_source=artifact_source,
            fallback_reason=fallback_reason,
            cache_hit=False,
        )
        return normalized_artifact, None, diagnostics

    @staticmethod
    def _sgr_to_artifact(
        sgr: SectionUnderstandingSGR,
        *,
        source: StageSourcePayload,
    ) -> SectionUnderstandingArtifact:
        """Convert SGR schema output to domain artifact."""
        return SectionUnderstandingArtifact(
            section_id=source.section_id,
            source_hash=source.source_hash,
            parent_doc_id=source.parent_doc_id,
            title=source.title,
            breadcrumb=list(source.breadcrumb),
            pedagogical_role=sgr.pedagogical_role,
            teaching_intent=sgr.teaching_intent,
            should_dwell=sgr.should_dwell,
            supports_generated_question=sgr.supports_generated_question,
            explicit_exercises=[
                ExerciseCandidate(
                    exercise_ref=e.exercise_ref,
                    prompt_excerpt=e.prompt_excerpt,
                    hidden_answer_ref=e.hidden_answer_ref,
                    requires_answer_check=e.requires_answer_check,
                )
                for e in sgr.explicit_exercises
            ],
            explicit_checkpoints=[
                CheckpointCandidate(
                    checkpoint_ref=c.checkpoint_ref,
                    prompt_excerpt=c.prompt_excerpt,
                    hidden_answer_ref=c.hidden_answer_ref,
                    requires_answer_check=c.requires_answer_check,
                )
                for c in sgr.explicit_checkpoints
            ],
            answer_check_contexts=[
                AnswerCheckContext(
                    item_ref=a.item_ref,
                    item_type=a.item_type,
                    hidden_answer_ref=a.hidden_answer_ref,
                    answer_source_excerpt=a.answer_source_excerpt,
                    rubric_brief=a.rubric_brief,
                    can_verify=a.can_verify,
                )
                for a in sgr.answer_check_contexts
            ],
            recommended_actions=list(sgr.recommended_actions),
            rationale=sgr.rationale,
            context_version=SectionUnderstandingService.context_version,
        )

    @staticmethod
    def _empty_diagnostics(current_stage: dict[str, Any] | None, *, source: str = "fallback") -> dict[str, Any]:
        return {
            "source": source,
            "fallback_used": True,
            "cache_hit": False,
            "section_family": classify_section_family(None, current_stage),
            "raw_issue_flags": [],
            "final_issue_flags": [],
        }

    def _fallback_result(
        self,
        current_stage: dict[str, Any] | None,
        *,
        fallback_reason: str,
    ) -> tuple[SectionUnderstandingArtifact | None, None, dict[str, Any]]:
        fallback_artifact = self.fallback_section_understanding(current_stage)
        if fallback_artifact is None:
            return None, None, self._empty_diagnostics(current_stage)
        diagnostics = self._section_understanding_diagnostics(
            raw_artifact=fallback_artifact,
            normalized_artifact=fallback_artifact,
            current_stage=current_stage,
            decision_source="fallback",
            fallback_reason=fallback_reason,
            cache_hit=False,
        )
        return fallback_artifact, None, diagnostics

    @staticmethod
    def normalize_section_understanding_artifact(
        artifact: SectionUnderstandingArtifact,
        *,
        current_stage: dict[str, Any] | None,
    ) -> SectionUnderstandingArtifact:
        return normalize_section_understanding_artifact(artifact, current_stage=current_stage)

    @staticmethod
    def _section_understanding_diagnostics(
        *,
        raw_artifact: SectionUnderstandingArtifact,
        normalized_artifact: SectionUnderstandingArtifact,
        current_stage: dict[str, Any] | None,
        decision_source: str,
        fallback_reason: str | None,
        cache_hit: bool,
    ) -> dict[str, Any]:
        fallback_used = decision_source != "llm" or bool(str(fallback_reason or "").strip())
        raw_diagnostics = collect_section_issue_flags(
            raw_artifact,
            current_stage=current_stage,
            fallback_used=fallback_used,
        )
        final_diagnostics = collect_section_issue_flags(
            normalized_artifact,
            current_stage=current_stage,
            fallback_used=fallback_used,
        )
        return {
            "source": decision_source,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "cache_hit": cache_hit,
            "section_family": final_diagnostics.section_family,
            "raw_section_family": raw_diagnostics.section_family,
            "raw_issue_flags": list(raw_diagnostics.issue_flags),
            "final_issue_flags": list(final_diagnostics.issue_flags),
        }


__all__ = ["SectionUnderstandingService"]
