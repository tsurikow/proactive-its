from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.state.db import get_session
from app.state.models import (
    Interaction,
    InteractionSource,
    Learner,
    LearnerPlanState,
    LessonCache,
    PlanTemplate,
    SessionRecord,
    StudyPlan,
    TopicProgress,
)


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


class StateRepository:
    async def ensure_learner(self, learner_id: str, timezone_name: str = "UTC") -> None:
        async with get_session() as session:
            stmt = (
                pg_insert(Learner)
                .values(id=learner_id, timezone=timezone_name)
                .on_conflict_do_nothing(index_elements=[Learner.id])
            )
            await session.execute(stmt)

    async def get_or_create_session(self, learner_id: str, window_hours: int = 2) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        async with get_session() as session:
            existing_id = await session.scalar(
                select(SessionRecord.id)
                .where(
                    SessionRecord.learner_id == learner_id,
                    or_(SessionRecord.ended_at.is_(None), SessionRecord.ended_at >= cutoff),
                )
                .order_by(SessionRecord.started_at.desc())
                .limit(1)
            )
            if existing_id is not None:
                return int(existing_id)

            record = SessionRecord(learner_id=learner_id)
            session.add(record)
            await session.flush()
            return int(record.id)

    async def add_interaction(
        self,
        learner_id: str,
        session_id: int,
        message: str,
        answer: str,
        module_id: str | None,
        section_id: str | None,
    ) -> int:
        async with get_session() as session:
            interaction = Interaction(
                learner_id=learner_id,
                session_id=session_id,
                module_id=module_id,
                section_id=section_id,
                message=message,
                answer=answer,
            )
            session.add(interaction)
            await session.flush()
            return int(interaction.id)

    async def add_interaction_sources(self, interaction_id: int, sources: list[dict[str, Any]]) -> None:
        if not sources:
            return
        async with get_session() as session:
            for source in sources:
                stmt = (
                    pg_insert(InteractionSource)
                    .values(
                        interaction_id=interaction_id,
                        chunk_id=source["chunk_id"],
                        score=source.get("score"),
                        rank=source["rank"],
                    )
                    .on_conflict_do_update(
                        index_elements=[InteractionSource.interaction_id, InteractionSource.chunk_id],
                        set_={"score": source.get("score"), "rank": source["rank"]},
                    )
                )
                await session.execute(stmt)

    async def update_interaction_confidence(self, interaction_id: int, confidence: int) -> None:
        async with get_session() as session:
            await session.execute(
                update(Interaction)
                .where(Interaction.id == interaction_id)
                .values(confidence=confidence)
            )

    async def get_interaction(self, interaction_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(Interaction).where(Interaction.id == interaction_id)
            )
            if row is None:
                return None
            return {
                "id": int(row.id),
                "learner_id": row.learner_id,
                "session_id": int(row.session_id) if row.session_id is not None else None,
                "module_id": row.module_id,
                "section_id": row.section_id,
                "message": row.message,
                "answer": row.answer,
                "confidence": row.confidence,
                "created_at": row.created_at,
            }

    async def upsert_topic_progress(
        self,
        learner_id: str,
        section_id: str,
        module_id: str | None,
        status: str,
        mastery_score: float,
    ) -> None:
        async with get_session() as session:
            stmt = (
                pg_insert(TopicProgress)
                .values(
                    learner_id=learner_id,
                    section_id=section_id,
                    module_id=module_id,
                    status=status,
                    mastery_score=mastery_score,
                )
                .on_conflict_do_update(
                    index_elements=[TopicProgress.learner_id, TopicProgress.section_id],
                    set_={
                        "module_id": module_id,
                        "status": status,
                        "mastery_score": mastery_score,
                        "updated_at": func.now(),
                    },
                )
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

    async def get_active_study_plan(self, learner_id: str, week_start: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(StudyPlan)
                .where(
                    StudyPlan.learner_id == learner_id,
                    StudyPlan.week_start == _as_date(week_start),
                    StudyPlan.status == "active",
                )
                .limit(1)
            )
        if row is None:
            return None
        return {
            "id": int(row.id),
            "learner_id": row.learner_id,
            "week_start": row.week_start.isoformat(),
            "status": row.status,
            "updated_at": row.updated_at,
            "plan": row.plan_json,
        }

    async def save_study_plan(self, learner_id: str, week_start: str, plan: dict[str, Any]) -> dict[str, Any]:
        week_start_date = _as_date(week_start)
        async with get_session() as session:
            upsert_stmt = (
                pg_insert(StudyPlan)
                .values(
                    learner_id=learner_id,
                    week_start=week_start_date,
                    plan_json=plan,
                    status="active",
                )
                .on_conflict_do_update(
                    index_elements=[StudyPlan.learner_id, StudyPlan.week_start],
                    set_={"plan_json": plan, "status": "active", "updated_at": func.now()},
                )
            )
            await session.execute(upsert_stmt)
            row = await session.scalar(
                select(StudyPlan).where(
                    StudyPlan.learner_id == learner_id,
                    StudyPlan.week_start == week_start_date,
                )
            )
        if row is None:
            raise RuntimeError("Failed to persist study plan")
        return {
            "id": int(row.id),
            "learner_id": row.learner_id,
            "week_start": row.week_start.isoformat(),
            "status": row.status,
            "updated_at": row.updated_at,
            "plan": row.plan_json,
        }

    async def clear_topic_progress(self, learner_id: str) -> None:
        async with get_session() as session:
            await session.execute(
                delete(TopicProgress).where(TopicProgress.learner_id == learner_id)
            )

    async def clear_study_plans(self, learner_id: str) -> None:
        async with get_session() as session:
            await session.execute(
                delete(StudyPlan).where(StudyPlan.learner_id == learner_id)
            )

    async def get_plan_template(self, template_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(PlanTemplate).where(PlanTemplate.id == template_id).limit(1)
            )
        if row is None:
            return None
        return self._template_row_to_dict(row)

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
        return self._template_row_to_dict(row)

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
            row = await session.scalar(
                select(PlanTemplate).where(PlanTemplate.id == template_id).limit(1)
            )
        if row is None:
            raise RuntimeError("Failed to persist plan template")
        return self._template_row_to_dict(row)

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
        return self._plan_state_row_to_dict(row)

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
        return self._plan_state_row_to_dict(row)

    async def get_lesson_cache(
        self,
        learner_id: str,
        template_id: str,
        stage_index: int,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(LessonCache)
                .where(
                    LessonCache.learner_id == learner_id,
                    LessonCache.template_id == template_id,
                    LessonCache.stage_index == stage_index,
                )
                .limit(1)
            )
        if row is None:
            return None
        return {
            "learner_id": row.learner_id,
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "lesson_json": row.lesson_json,
            "updated_at": row.updated_at,
        }

    async def upsert_lesson_cache(
        self,
        learner_id: str,
        template_id: str,
        stage_index: int,
        lesson_json: dict[str, Any],
    ) -> dict[str, Any]:
        async with get_session() as session:
            stmt = (
                pg_insert(LessonCache)
                .values(
                    learner_id=learner_id,
                    template_id=template_id,
                    stage_index=stage_index,
                    lesson_json=lesson_json,
                )
                .on_conflict_do_update(
                    index_elements=[LessonCache.learner_id, LessonCache.template_id, LessonCache.stage_index],
                    set_={
                        "lesson_json": lesson_json,
                        "updated_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)
            row = await session.scalar(
                select(LessonCache)
                .where(
                    LessonCache.learner_id == learner_id,
                    LessonCache.template_id == template_id,
                    LessonCache.stage_index == stage_index,
                )
                .limit(1)
            )
        if row is None:
            raise RuntimeError("Failed to upsert lesson cache")
        return {
            "learner_id": row.learner_id,
            "template_id": row.template_id,
            "stage_index": int(row.stage_index),
            "lesson_json": row.lesson_json,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _template_row_to_dict(row: PlanTemplate) -> dict[str, Any]:
        return {
            "id": row.id,
            "book_id": row.book_id,
            "version": int(row.version),
            "plan_json": row.plan_json,
            "is_active": bool(row.is_active),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _plan_state_row_to_dict(row: LearnerPlanState) -> dict[str, Any]:
        return {
            "learner_id": row.learner_id,
            "template_id": row.template_id,
            "current_stage_index": int(row.current_stage_index),
            "plan_completed": bool(row.plan_completed),
            "completed_count": int(row.completed_count),
            "updated_at": row.updated_at,
        }
