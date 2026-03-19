from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.platform.db import get_session
from app.platform.models import Learner, LearnerPlanState, LessonCache, PlanTemplate, StartMessageCache, TopicProgress
from app.platform.serializers import plan_state_row_to_dict, template_row_to_dict


class TutorRepository:
    async def ensure_learner(self, learner_id: str, timezone_name: str = "UTC") -> None:
        async with get_session() as session:
            stmt = (
                pg_insert(Learner)
                .values(id=learner_id, timezone=timezone_name)
                .on_conflict_do_nothing(index_elements=[Learner.id])
            )
            await session.execute(stmt)

    async def list_topic_progress(self, learner_id: str) -> list[dict[str, Any]]:
        async with get_session() as session:
            rows = (
                await session.scalars(
                    select(TopicProgress)
                    .where(TopicProgress.learner_id == learner_id)
                    .order_by(TopicProgress.updated_at.desc())
                )
            ).all()
        return [
            {
                "learner_id": row.learner_id,
                "module_id": row.module_id,
                "section_id": row.section_id,
                "status": row.status,
                "mastery_score": float(row.mastery_score),
                "updated_at": row.updated_at,
            }
            for row in rows
        ]

    async def get_plan_template(self, template_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(select(PlanTemplate).where(PlanTemplate.id == template_id).limit(1))
        if row is None:
            return None
        return template_row_to_dict(row)

    async def get_active_plan_template(self) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(PlanTemplate)
                .where(PlanTemplate.is_active.is_(True))
                .order_by(PlanTemplate.updated_at.desc())
                .limit(1)
            )
        if row is None:
            return None
        return template_row_to_dict(row)

    async def upsert_plan_template(
        self,
        template_id: str,
        book_id: str,
        version: int,
        plan_json: dict[str, Any],
        is_active: bool = True,
    ) -> dict[str, Any]:
        async with get_session() as session:
            if is_active:
                await session.execute(
                    update(PlanTemplate)
                    .where(PlanTemplate.id != template_id)
                    .values(is_active=False, updated_at=func.now())
                )

            stmt = (
                pg_insert(PlanTemplate)
                .values(
                    id=template_id,
                    book_id=book_id,
                    version=version,
                    plan_json=plan_json,
                    is_active=is_active,
                )
                .on_conflict_do_update(
                    index_elements=[PlanTemplate.id],
                    set_={
                        "book_id": book_id,
                        "version": version,
                        "plan_json": plan_json,
                        "is_active": is_active,
                        "updated_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)
            row = await session.scalar(select(PlanTemplate).where(PlanTemplate.id == template_id).limit(1))
        if row is None:
            raise RuntimeError("Failed to persist plan template")
        return template_row_to_dict(row)

    async def get_or_create_learner_plan_state(
        self,
        learner_id: str,
        template_id: str,
        total_stages: int,
    ) -> dict[str, Any]:
        async with get_session() as session:
            stmt = (
                pg_insert(LearnerPlanState)
                .values(
                    learner_id=learner_id,
                    template_id=template_id,
                    current_stage_index=0,
                    plan_completed=False,
                    completed_count=0,
                )
                .on_conflict_do_nothing(index_elements=[LearnerPlanState.learner_id])
            )
            await session.execute(stmt)

            row = await session.scalar(
                select(LearnerPlanState)
                .where(LearnerPlanState.learner_id == learner_id)
                .limit(1)
            )

            if row is None:
                raise RuntimeError("Failed to load learner plan state")

            max_index = max(0, total_stages - 1)
            needs_update = False
            next_stage_index = int(row.current_stage_index)
            next_completed_count = int(row.completed_count)
            next_plan_completed = bool(row.plan_completed)
            next_template_id = str(row.template_id)

            if next_template_id != template_id:
                next_template_id = template_id
                next_stage_index = 0
                next_completed_count = 0
                next_plan_completed = False
                needs_update = True

            if total_stages == 0:
                if not next_plan_completed:
                    next_plan_completed = True
                    needs_update = True
                next_stage_index = 0
                next_completed_count = 0
            else:
                if next_stage_index < 0:
                    next_stage_index = 0
                    needs_update = True
                if next_stage_index > max_index:
                    next_stage_index = max_index
                    needs_update = True
                if next_completed_count < 0:
                    next_completed_count = 0
                    needs_update = True
                if next_completed_count > total_stages:
                    next_completed_count = total_stages
                    needs_update = True
                if next_completed_count >= total_stages and not next_plan_completed:
                    next_plan_completed = True
                    needs_update = True

            if needs_update:
                await session.execute(
                    update(LearnerPlanState)
                    .where(LearnerPlanState.learner_id == learner_id)
                    .values(
                        template_id=next_template_id,
                        current_stage_index=next_stage_index,
                        completed_count=next_completed_count,
                        plan_completed=next_plan_completed,
                        updated_at=func.now(),
                    )
                )
                row = await session.scalar(
                    select(LearnerPlanState)
                    .where(LearnerPlanState.learner_id == learner_id)
                    .limit(1)
                )
        if row is None:
            raise RuntimeError("Failed to load learner plan state")
        return plan_state_row_to_dict(row)

    async def update_learner_plan_state(
        self,
        learner_id: str,
        template_id: str,
        current_stage_index: int,
        completed_count: int,
        plan_completed: bool,
    ) -> dict[str, Any]:
        async with get_session() as session:
            await session.execute(
                update(LearnerPlanState)
                .where(LearnerPlanState.learner_id == learner_id)
                .values(
                    template_id=template_id,
                    current_stage_index=current_stage_index,
                    completed_count=completed_count,
                    plan_completed=plan_completed,
                    updated_at=func.now(),
                )
            )
            row = await session.scalar(
                select(LearnerPlanState)
                .where(LearnerPlanState.learner_id == learner_id)
                .limit(1)
            )
        if row is None:
            raise RuntimeError("Failed to update learner plan state")
        return plan_state_row_to_dict(row)

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
