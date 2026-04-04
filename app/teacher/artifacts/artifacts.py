from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.content.indexing.section_source import resolve_stage_source
from app.state.services.learner_service import LearnerService
from app.state.models import AdaptationContext
from app.platform.config import Settings, get_settings
from app.platform.logging import log_event
from app.platform.vector_store import AsyncVectorStore
from app.teacher.artifacts.artifact_keys import (
    learner_memory_fingerprint,
    lesson_adaptation_brief,
    lesson_cache_artifact_key,
    lesson_plan_fingerprint,
)
from app.teacher.artifacts.decision_records import TeacherArtifactRecorder
from app.teacher.artifacts.lesson_generation import SectionLessonGenerator
from app.teacher.artifacts.models import LessonPlanDraft, LearnerMemorySummary
from app.teacher.models import (
    StageSourcePayload,
    TeacherArtifactReference,
    TeacherArtifactContext,
    TeacherMessageResult,
)
from app.teacher.repository import TeacherRepository
from app.state.stage_state import bind_parent_doc_id

logger = logging.getLogger(__name__)

LEARNER_MEMORY_FRESHNESS_WINDOW = timedelta(hours=8)


def _lesson_instruction(
    *,
    stage: dict[str, Any],
    adaptation_context: AdaptationContext,
    lesson_plan_draft: LessonPlanDraft | None,
) -> str:
    title = str(stage.get("title") or stage.get("section_id") or "the current section")
    instruction = (
        f"Teach {title} as one coherent lesson. "
        f"Stay grounded in the section source and calibrate for stage signal {adaptation_context.stage_signal}."
    )
    if lesson_plan_draft is not None:
        instruction += (
            f" Aim for: {lesson_plan_draft.lesson_objective}. "
            f"Emphasize: {lesson_plan_draft.support_emphasis}."
        )
        if lesson_plan_draft.progression_note:
            instruction += f" Progression note: {lesson_plan_draft.progression_note}."
    return instruction


def _lesson_render_signature(
    *,
    stage: dict[str, Any],
    adaptation_context: AdaptationContext,
    lesson_instruction: str,
    planner_fingerprint: str,
    learner_memory_fingerprint: str,
) -> str:
    raw = json.dumps(
        {
            "section_id": str(stage.get("section_id") or ""),
            "stage_signal": adaptation_context.stage_signal,
            "context_version": adaptation_context.context_version,
            "lesson_instruction": lesson_instruction,
            "planner_fingerprint": planner_fingerprint,
            "learner_memory_fingerprint": learner_memory_fingerprint,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


class TeacherArtifactRuntime:
    def __init__(
        self,
        *,
        repository: TeacherRepository,
        learner_service: LearnerService,
        vector_store: AsyncVectorStore,
        lesson_generator: SectionLessonGenerator,
        policy_engine: Any | None = None,
        settings: Settings | None = None,
    ):
        self.repository = repository
        self.learner_service = learner_service
        self.vector_store = vector_store
        self.policy_engine = policy_engine
        self.lesson_generator = lesson_generator
        self.settings = settings or get_settings()
        self.decision_recorder = TeacherArtifactRecorder(
            repository=repository,
            logger=logger,
        )

    async def _resolve_stage_source_payload(self, stage: dict[str, Any]) -> StageSourcePayload:
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

    @staticmethod
    def _artifact_reference(row: dict[str, Any]) -> TeacherArtifactReference:
        return TeacherArtifactReference(
            id=None if row.get("id") is None else int(row.get("id")),
            decision_kind=str(row.get("decision_kind") or ""),
            artifact_key=str(row.get("artifact_key") or ""),
            stage_index=int(row.get("stage_index", -1)),
            section_id=str(row.get("section_id") or ""),
            module_id=None if row.get("module_id") is None else str(row.get("module_id")),
            context_version=None if row.get("context_version") is None else str(row.get("context_version")),
            stage_signal=None if row.get("stage_signal") is None else str(row.get("stage_signal")),
            created_at=row.get("created_at"),
        )

    async def _recent_artifact_bundle(
        self, learner_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
        recent = await self.repository.list_recent_teacher_artifacts(learner_id, limit=6, decision_kinds=["learner_memory_summary", "progression", "grounding_analysis", "lesson_plan"])
        return recent, [
            self._artifact_reference(item).model_dump(mode="json")
            for item in recent
            if item.get("decision_kind")
        ], next(
            (item for item in recent if str(item.get("decision_kind") or "") == "learner_memory_summary"),
            None,
        )

    async def _persist_artifact_if_llm(
        self,
        *,
        decision_kind: str,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        message: TeacherMessageResult | None,
        source: str | None,
        persist_decision: bool,
    ) -> dict[str, Any] | None:
        if message is None or source != "llm" or not persist_decision:
            return None
        if decision_kind == "learner_memory_summary":
            return await self.decision_recorder.record_learner_memory_summary_decision(
                learner_id=learner_id,
                template_id=template_id,
                stage=stage,
                adaptation_context=adaptation_context,
                learner_memory_summary=message,
            )
        return await self.decision_recorder.record_lesson_plan_decision(
            learner_id=learner_id,
            template_id=template_id,
            stage=stage,
            adaptation_context=adaptation_context,
            lesson_plan_draft=message,
        ) if decision_kind == "lesson_plan" else None

    async def _build_artifact_context(
        self,
        *,
        kind: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        learner_id: str | None = None,
        learner_memory_summary: LearnerMemorySummary | None = None,
        source: StageSourcePayload | None = None,
    ) -> TeacherArtifactContext:
        if kind == "learner_memory":
            if learner_id is None:
                raise ValueError("learner_id_required_for_learner_memory_context")
            source_payload = await self.learner_service.build_learner_model_source(
                learner_id,
                current_stage=stage,
                adaptation_context=adaptation_context,
            )
            decisions = await self.repository.list_recent_teacher_artifacts(
                learner_id,
                limit=6,
                decision_kinds=["progression", "learner_memory_summary"],
            )
            latest_progression_variant = next(
                (str(item.get("artifact_key") or "") or None for item in decisions if item.get("decision_kind") == "progression"),
                None,
            )
            current_stage_index = int(stage.get("stage_index", -1))
            rows = [item for item in decisions if int(item.get("stage_index", -999)) == current_stage_index] or decisions
            return TeacherArtifactContext(
                current_stage=dict(stage),
                adaptation_context=adaptation_context,
                grounding_summary={
                    "mode": None,
                    "applied_progression_variant": None,
                    "assessment_decision": None,
                    "target_stage_title": "",
                    "profile": dict(source_payload.get("profile") or {}),
                    "mastery_signals": dict(source_payload.get("mastery_signals") or {}),
                },
                recent_evidence_summary=dict(source_payload.get("recent_evidence_summary") or {}),
                recent_decision_summary={
                    "count": len(rows),
                    "latest_progression_variant": latest_progression_variant,
                    "recent_decision_kinds": [str(item.get("decision_kind") or "") for item in rows[:4]],
                    "recent_artifact_keys": [str(item.get("artifact_key") or "") for item in rows[:4]],
                },
            )
        if source is None:
            raise ValueError("source_required_for_lesson_plan_context")
        source_lines = str(source.source_markdown or "").splitlines()
        headings = [line.lstrip("#").strip() for line in source_lines if line.lstrip().startswith("#")][:6]
        source_excerpt = " ".join(line.strip() for line in source_lines if line.strip())[:1200]
        return TeacherArtifactContext(
            current_stage=dict(stage),
            adaptation_context=adaptation_context,
            learner_memory_summary=learner_memory_summary,
            grounding_summary={
                "title": str(stage.get("title") or ""),
                "breadcrumb": list(stage.get("breadcrumb") or []),
                "parent_doc_id": source.parent_doc_id,
                "source_hash": source.source_hash,
                "block_outline": headings,
                "source_excerpt": source_excerpt,
            },
        )

    async def _run_artifact_specialist(
        self,
        *,
        kind: str,
        task: TeacherArtifactContext,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        persist_decision: bool,
        use_agentic: bool,
    ) -> tuple[TeacherMessageResult | None, str | None, dict[str, Any] | None]:
        runner = (
            self.policy_engine.synthesize_learner_memory
            if kind == "learner_memory_summary"
            else self.policy_engine.plan_lesson
        )
        message, source, _fallback_reason = await runner(task, use_agentic=use_agentic)
        persisted_row = await self._persist_artifact_if_llm(
            decision_kind=kind,
            learner_id=learner_id,
            template_id=template_id,
            stage=stage,
            adaptation_context=adaptation_context,
            message=message,
            source=source,
            persist_decision=persist_decision,
        )
        return message, source, persisted_row

    @staticmethod
    def _learner_memory_refresh_reason(
        *,
        artifact_row: dict[str, Any] | None,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
    ) -> str | None:
        if artifact_row is None:
            return "no_recent_artifact"
        if int(artifact_row.get("stage_index", -999)) != int(stage.get("stage_index", -1)):
            return "stage_mismatch"
        if str(artifact_row.get("section_id") or "") != str(stage.get("section_id") or ""):
            return "section_mismatch"
        if str(artifact_row.get("context_version") or "") != str(adaptation_context.context_version or ""):
            return "context_version_changed"
        if str(artifact_row.get("stage_signal") or "") != str(adaptation_context.stage_signal or ""):
            return "stage_signal_changed"
        created_at = artifact_row.get("created_at")
        if not isinstance(created_at, datetime):
            return "missing_created_at"
        created_at_utc = created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)
        if datetime.now(UTC) - created_at_utc > LEARNER_MEMORY_FRESHNESS_WINDOW:
            return "artifact_stale"
        return None

    async def get_or_synthesize_learner_memory_bundle(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        persist_decision: bool = False,
        use_agentic: bool = True,
    ) -> dict[str, Any]:
        _recent, recent_refs, latest_memory_row = await self._recent_artifact_bundle(learner_id)
        refresh_reason = self._learner_memory_refresh_reason(
            artifact_row=latest_memory_row,
            stage=stage,
            adaptation_context=adaptation_context,
        )
        if latest_memory_row is not None and refresh_reason is None:
            payload = latest_memory_row.get("decision_payload_json") or {}
            try:
                learner_memory_summary = TeacherMessageResult.model_validate(payload)
                return {
                    "learner_memory_summary": learner_memory_summary,
                    "learner_memory_status": "reused_fresh",
                    "learner_memory_refresh_reason": None,
                    "learner_memory_artifact": self._artifact_reference(latest_memory_row).model_dump(mode="json"),
                    "recent_teacher_artifacts": recent_refs,
                }
            except Exception:
                refresh_reason = "invalid_payload"

        learner_model_task = await self._build_artifact_context(
            kind="learner_memory",
            learner_id=learner_id,
            stage=stage,
            adaptation_context=adaptation_context,
        )
        learner_memory_summary, _summary_source, persisted_row = await self._run_artifact_specialist(
            kind="learner_memory_summary",
            task=learner_model_task,
            learner_id=learner_id,
            template_id=template_id,
            stage=stage,
            adaptation_context=adaptation_context,
            persist_decision=persist_decision,
            use_agentic=use_agentic,
        )
        return {
            "learner_memory_summary": learner_memory_summary,
            "learner_memory_status": "recomputed" if learner_memory_summary is not None else "missing",
            "learner_memory_refresh_reason": refresh_reason or "no_reusable_artifact",
            "learner_memory_artifact": None
            if persisted_row is None
            else self._artifact_reference(persisted_row).model_dump(mode="json"),
            "recent_teacher_artifacts": recent_refs,
        }

    def _is_valid_cache(self, cache: dict[str, Any] | None, source_hash: str) -> bool:
        if not cache:
            return False
        lesson_json = dict(cache.get("lesson_json") or {})
        if int(lesson_json.get("format_version", 0)) < int(self.lesson_generator.settings.lesson_gen_format_version):
            return False
        if lesson_json.get("generator_version") != self.lesson_generator.generator_version:
            return False
        if lesson_json.get("prompt_profile_version") != self.lesson_generator.prompt_profile_version:
            return False
        return str(lesson_json.get("source_hash", "")) == str(source_hash)

    async def get_or_generate_lesson(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        persist_decision: bool = True,
        use_agentic: bool = True,
        use_learner_model: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        lesson, stage_with_parent, _meta = await self.get_or_generate_lesson_with_meta(
            learner_id=learner_id,
            template_id=template_id,
            stage=stage,
            adaptation_context=adaptation_context,
            persist_decision=persist_decision,
            use_agentic=use_agentic,
            use_learner_model=use_learner_model,
        )
        return lesson, stage_with_parent

    async def get_or_generate_lesson_with_meta(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage: dict[str, Any],
        adaptation_context: AdaptationContext,
        persist_decision: bool = True,
        use_agentic: bool = True,
        use_learner_model: bool | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        source_payload = await self._resolve_stage_source_payload(stage)
        learner_memory_summary: LearnerMemorySummary | None = None
        use_learner_model = bool(use_learner_model) if use_learner_model is not None else True
        if use_learner_model:
            learner_memory_bundle = await self.get_or_synthesize_learner_memory_bundle(
                learner_id=learner_id,
                template_id=template_id,
                stage=stage,
                adaptation_context=adaptation_context,
                persist_decision=persist_decision,
                use_agentic=use_agentic,
            )
            learner_memory_summary = learner_memory_bundle.get("learner_memory_summary")

        lesson_task = await self._build_artifact_context(
            kind="lesson_plan",
            stage=stage,
            adaptation_context=adaptation_context,
            learner_memory_summary=learner_memory_summary,
            source=source_payload,
        )
        lesson_plan_draft = None
        if use_agentic:
            lesson_plan_draft, _lesson_source_name, _persisted_row = await self._run_artifact_specialist(
                kind="lesson_plan",
                task=lesson_task,
                learner_id=learner_id,
                template_id=template_id,
                stage=stage,
                adaptation_context=adaptation_context,
                persist_decision=persist_decision,
                use_agentic=use_agentic,
            )

        planner_fp = lesson_plan_fingerprint(lesson_plan_draft)
        learner_memory_fp = learner_memory_fingerprint(learner_memory_summary)
        lesson_instruction = _lesson_instruction(
            stage=stage,
            adaptation_context=adaptation_context,
            lesson_plan_draft=lesson_plan_draft,
        )
        render_signature = _lesson_render_signature(
            stage=stage,
            adaptation_context=adaptation_context,
            lesson_instruction=lesson_instruction,
            planner_fingerprint=planner_fp,
            learner_memory_fingerprint=learner_memory_fp,
        )
        combined_artifact_key = lesson_cache_artifact_key(
            section_id=str(stage.get("section_id") or ""),
            stage_signal=adaptation_context.stage_signal,
            render_signature=render_signature,
            context_version=adaptation_context.context_version,
            planner_fingerprint=planner_fp,
            learner_memory_fingerprint=learner_memory_fp,
        )
        cache = await self.repository.get_lesson_cache(
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
            artifact_key=combined_artifact_key,
            context_version=adaptation_context.context_version,
        )
        if self._is_valid_cache(cache, source_payload.source_hash):
            lesson = dict(cache["lesson_json"])
            lesson["cached"] = True
            log_event(
                logger,
                "lesson.cache_hit",
                template_id=template_id,
                stage_index=int(stage["stage_index"]),
                section_id=str(stage.get("section_id") or ""),
                parent_doc_id=source_payload.parent_doc_id,
                artifact_key=combined_artifact_key,
                stage_signal=adaptation_context.stage_signal,
                render_signature=render_signature,
                cache_hit=True,
                generation_mode=lesson.get("generation_mode"),
                duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
            )
            return lesson, bind_parent_doc_id(stage, source_payload.parent_doc_id), {
                "cache_written": False,
                "cache_hit": True,
                "cache_key": combined_artifact_key,
                "planner_fingerprint": planner_fp,
                "learner_memory_fingerprint": learner_memory_fp,
                "context_version": adaptation_context.context_version,
            }
        lesson = await self.lesson_generator.generate_lesson(
            section_id=str(stage["section_id"]),
            title=str(stage.get("title") or ""),
            breadcrumb=list(stage.get("breadcrumb") or []),
            parent_doc_id=source_payload.parent_doc_id,
            source_markdown=source_payload.source_markdown,
            lesson_instruction=lesson_instruction,
            lesson_render_signature=render_signature,
            stage_signal=adaptation_context.stage_signal,
            adaptation_brief=lesson_adaptation_brief(adaptation_context),
            lesson_plan_draft=lesson_plan_draft,
            learner_teaching_brief=None if learner_memory_summary is None else learner_memory_summary.teaching_brief,
        )
        await self.repository.upsert_lesson_cache(
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
            artifact_key=combined_artifact_key,
            context_version=adaptation_context.context_version,
            lesson_json=lesson,
        )
        lesson["cached"] = False
        log_event(
            logger,
            "lesson.generated",
            template_id=template_id,
            stage_index=int(stage["stage_index"]),
            section_id=str(stage.get("section_id") or ""),
            parent_doc_id=source_payload.parent_doc_id,
            artifact_key=combined_artifact_key,
            stage_signal=adaptation_context.stage_signal,
            render_signature=render_signature,
            cache_hit=False,
            generation_mode=lesson.get("generation_mode"),
            duration_ms=round((time.perf_counter() - started) * 1000.0, 1),
        )
        return lesson, bind_parent_doc_id(stage, source_payload.parent_doc_id), {
            "cache_written": True,
            "cache_hit": False,
            "cache_key": combined_artifact_key,
            "planner_fingerprint": planner_fp,
            "learner_memory_fingerprint": learner_memory_fp,
            "context_version": adaptation_context.context_version,
        }

__all__ = ["TeacherArtifactRuntime"]
