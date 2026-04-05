from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.platform.db import get_session
from app.teacher.orm_models import LessonCache, TeacherArtifactRecord
from app.teacher.serializers import teacher_artifact_row_to_dict
from app.teacher.models import TeacherArtifact


class TeacherRepository:
    async def get_lesson_cache(
        self,
        template_id: str,
        stage_index: int,
        *,
        artifact_key: str,
        context_version: str,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(LessonCache)
                .where(
                    LessonCache.template_id == template_id,
                    LessonCache.stage_index == stage_index,
                    LessonCache.artifact_key == artifact_key,
                    LessonCache.context_version == context_version,
                )
                .limit(1)
            )
        if row is None:
            return None
        return {
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "artifact_key": row.artifact_key,
            "context_version": row.context_version,
            "lesson_json": row.lesson_json,
            "updated_at": row.updated_at,
        }

    async def upsert_lesson_cache(
        self,
        template_id: str,
        stage_index: int,
        *,
        artifact_key: str,
        context_version: str,
        lesson_json: dict[str, Any],
    ) -> dict[str, Any]:
        async with get_session() as session:
            stmt = (
                pg_insert(LessonCache)
                .values(
                    template_id=template_id,
                    stage_index=stage_index,
                    artifact_key=artifact_key,
                    context_version=context_version,
                    lesson_json=lesson_json,
                )
                .on_conflict_do_update(
                    index_elements=[
                        LessonCache.template_id,
                        LessonCache.stage_index,
                        LessonCache.artifact_key,
                        LessonCache.context_version,
                    ],
                    set_={"lesson_json": lesson_json, "updated_at": func.now()},
                )
            )
            await session.execute(stmt)
            row = await session.scalar(
                select(LessonCache)
                .where(
                    LessonCache.template_id == template_id,
                    LessonCache.stage_index == stage_index,
                    LessonCache.artifact_key == artifact_key,
                    LessonCache.context_version == context_version,
                )
                .limit(1)
            )
        if row is None:
            raise RuntimeError("Failed to upsert lesson cache")
        return {
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "artifact_key": row.artifact_key,
            "context_version": row.context_version,
            "lesson_json": row.lesson_json,
            "updated_at": row.updated_at,
        }

    async def append_teacher_artifact(self, decision: TeacherArtifact) -> dict[str, Any]:
        async with get_session() as session:
            row = TeacherArtifactRecord(
                learner_id=decision.learner_id,
                template_id=decision.template_id,
                stage_index=decision.stage_index,
                section_id=decision.section_id,
                module_id=decision.module_id,
                decision_kind=decision.decision_kind,
                artifact_key=decision.artifact_key,
                stage_signal=decision.stage_signal,
                decision_source=decision.decision_source,
                context_version=decision.context_version,
                effective_mastery_score=decision.effective_mastery_score,
                weak_topic_count=decision.weak_topic_count,
                module_evidence_coverage=decision.module_evidence_coverage,
                fallback_reason=decision.fallback_reason,
                decision_payload_json=decision.decision_payload_json,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
        return teacher_artifact_row_to_dict(row)

    async def write_section_understanding(self, artifact: TeacherArtifact) -> dict[str, Any]:
        return await self.append_teacher_artifact(artifact)

    async def get_latest_teacher_artifact(
        self,
        learner_id: str,
        stage_index: int,
        decision_kind: str,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(TeacherArtifactRecord)
                .where(
                    TeacherArtifactRecord.learner_id == learner_id,
                    TeacherArtifactRecord.stage_index == stage_index,
                    TeacherArtifactRecord.decision_kind == decision_kind,
                )
                .order_by(TeacherArtifactRecord.created_at.desc(), TeacherArtifactRecord.id.desc())
                .limit(1)
            )
        if row is None:
            return None
        return teacher_artifact_row_to_dict(row)

    async def get_section_understanding(
        self,
        *,
        learner_id: str,
        template_id: str,
        stage_index: int,
        section_id: str,
        artifact_key: str,
        context_version: str,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(TeacherArtifactRecord)
                .where(
                    TeacherArtifactRecord.learner_id == learner_id,
                    TeacherArtifactRecord.template_id == template_id,
                    TeacherArtifactRecord.stage_index == stage_index,
                    TeacherArtifactRecord.section_id == section_id,
                    TeacherArtifactRecord.decision_kind == "section_understanding",
                    TeacherArtifactRecord.artifact_key == artifact_key,
                    TeacherArtifactRecord.context_version == context_version,
                )
                .order_by(TeacherArtifactRecord.created_at.desc(), TeacherArtifactRecord.id.desc())
                .limit(1)
            )
        if row is None:
            return None
        return teacher_artifact_row_to_dict(row)

    async def get_latest_progression_targeting_section(
        self,
        learner_id: str,
        target_section_id: str,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(TeacherArtifactRecord)
                .where(
                    TeacherArtifactRecord.learner_id == learner_id,
                    TeacherArtifactRecord.decision_kind == "progression",
                    TeacherArtifactRecord.decision_payload_json["target_section_id"].astext == target_section_id,
                )
                .order_by(TeacherArtifactRecord.created_at.desc(), TeacherArtifactRecord.id.desc())
                .limit(1)
            )
        if row is None:
            return None
        return teacher_artifact_row_to_dict(row)

    async def list_recent_teacher_artifacts(
        self,
        learner_id: str,
        *,
        limit: int = 6,
        decision_kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        async with get_session() as session:
            stmt = (
                select(TeacherArtifactRecord)
                .where(TeacherArtifactRecord.learner_id == learner_id)
                .order_by(TeacherArtifactRecord.created_at.desc(), TeacherArtifactRecord.id.desc())
                .limit(limit)
            )
            if decision_kinds:
                stmt = stmt.where(TeacherArtifactRecord.decision_kind.in_(decision_kinds))
            rows = (await session.scalars(stmt)).all()
        return [teacher_artifact_row_to_dict(row) for row in rows]
