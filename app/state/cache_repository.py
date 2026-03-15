from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.state.db import get_session
from app.state.models import LessonCache, StartMessageCache


class CacheRepository:
    async def get_lesson_cache(self, template_id: str, stage_index: int) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(LessonCache)
                .where(
                    LessonCache.template_id == template_id,
                    LessonCache.stage_index == stage_index,
                )
                .limit(1)
            )
        if row is None:
            return None
        return {
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "lesson_json": row.lesson_json,
            "updated_at": row.updated_at,
        }

    async def upsert_lesson_cache(
        self,
        template_id: str,
        stage_index: int,
        lesson_json: dict[str, Any],
    ) -> dict[str, Any]:
        async with get_session() as session:
            stmt = (
                pg_insert(LessonCache)
                .values(
                    template_id=template_id,
                    stage_index=stage_index,
                    lesson_json=lesson_json,
                )
                .on_conflict_do_update(
                    index_elements=[LessonCache.template_id, LessonCache.stage_index],
                    set_={"lesson_json": lesson_json, "updated_at": func.now()},
                )
            )
            await session.execute(stmt)
            row = await session.scalar(
                select(LessonCache)
                .where(
                    LessonCache.template_id == template_id,
                    LessonCache.stage_index == stage_index,
                )
                .limit(1)
            )
        if row is None:
            raise RuntimeError("Failed to upsert lesson cache")
        return {
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "lesson_json": row.lesson_json,
            "updated_at": row.updated_at,
        }

    async def get_start_message_cache(
        self,
        learner_id: str,
        template_id: str,
        stage_index: int,
        completed_count: int,
        plan_completed: bool,
        profile_version: str,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(StartMessageCache)
                .where(
                    StartMessageCache.learner_id == learner_id,
                    StartMessageCache.template_id == template_id,
                    StartMessageCache.stage_index == stage_index,
                    StartMessageCache.completed_count == completed_count,
                    StartMessageCache.plan_completed == plan_completed,
                    StartMessageCache.profile_version == profile_version,
                )
                .limit(1)
            )
        if row is None:
            return None
        return {
            "learner_id": row.learner_id,
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "completed_count": int(row.completed_count),
            "plan_completed": bool(row.plan_completed),
            "profile_version": row.profile_version,
            "message": row.message,
            "updated_at": row.updated_at,
        }

    async def upsert_start_message_cache(
        self,
        learner_id: str,
        template_id: str,
        stage_index: int,
        completed_count: int,
        plan_completed: bool,
        profile_version: str,
        message: str,
    ) -> dict[str, Any]:
        async with get_session() as session:
            stmt = (
                pg_insert(StartMessageCache)
                .values(
                    learner_id=learner_id,
                    template_id=template_id,
                    stage_index=stage_index,
                    completed_count=completed_count,
                    plan_completed=plan_completed,
                    profile_version=profile_version,
                    message=message,
                )
                .on_conflict_do_update(
                    index_elements=[
                        StartMessageCache.learner_id,
                        StartMessageCache.template_id,
                        StartMessageCache.stage_index,
                        StartMessageCache.completed_count,
                        StartMessageCache.plan_completed,
                        StartMessageCache.profile_version,
                    ],
                    set_={"message": message, "updated_at": func.now()},
                )
            )
            await session.execute(stmt)
            row = await session.scalar(
                select(StartMessageCache)
                .where(
                    StartMessageCache.learner_id == learner_id,
                    StartMessageCache.template_id == template_id,
                    StartMessageCache.stage_index == stage_index,
                    StartMessageCache.completed_count == completed_count,
                    StartMessageCache.plan_completed == plan_completed,
                    StartMessageCache.profile_version == profile_version,
                )
                .limit(1)
            )
        if row is None:
            raise RuntimeError("Failed to upsert start message cache")
        return {
            "learner_id": row.learner_id,
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "completed_count": int(row.completed_count),
            "plan_completed": bool(row.plan_completed),
            "profile_version": row.profile_version,
            "message": row.message,
            "updated_at": row.updated_at,
        }
