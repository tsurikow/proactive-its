from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.chat.models import AssessmentResult
from app.platform.db import get_session
from app.platform.models import Interaction, InteractionAssessment, InteractionSource, SessionRecord


class InteractionRepository:
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

    async def create_interaction_with_sources(
        self,
        *,
        learner_id: str,
        session_id: int,
        message: str,
        answer: str,
        module_id: str | None,
        section_id: str | None,
        sources: list[dict[str, Any]],
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
            interaction_id = int(interaction.id)

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
            return interaction_id

    async def update_interaction_confidence(self, interaction_id: int, confidence: int) -> None:
        async with get_session() as session:
            await session.execute(
                update(Interaction)
                .where(Interaction.id == interaction_id)
                .values(confidence=confidence)
            )

    async def get_interaction(self, interaction_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(select(Interaction).where(Interaction.id == interaction_id))
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

    async def list_interaction_sources(self, interaction_id: int) -> list[dict[str, Any]]:
        async with get_session() as session:
            rows = (
                await session.scalars(
                    select(InteractionSource)
                    .where(InteractionSource.interaction_id == interaction_id)
                    .order_by(InteractionSource.rank.asc())
                )
            ).all()
        return [
            {
                "interaction_id": int(row.interaction_id),
                "chunk_id": row.chunk_id,
                "score": float(row.score) if row.score is not None else None,
                "rank": int(row.rank),
            }
            for row in rows
        ]

    async def upsert_interaction_assessment(
        self,
        *,
        interaction_id: int,
        learner_id: str,
        session_id: int | None,
        module_id: str | None,
        section_id: str | None,
        result: AssessmentResult,
    ) -> None:
        async with get_session() as session:
            stmt = (
                pg_insert(InteractionAssessment)
                .values(
                    interaction_id=interaction_id,
                    learner_id=learner_id,
                    session_id=session_id,
                    module_id=module_id,
                    section_id=section_id,
                    decision=result.decision.value,
                    confidence=result.confidence,
                    recommended_next_action=result.recommended_next_action.value,
                    learner_rationale=result.learner_rationale,
                    reasoning_summary_json=result.reasoning_summary.model_dump(mode="json"),
                    cited_chunk_ids=list(result.cited_chunk_ids),
                    assessment_model=result.assessment_model,
                    schema_version=result.schema_version,
                    fallback_used=result.fallback_used,
                    fallback_reason=result.fallback_reason,
                )
                .on_conflict_do_update(
                    index_elements=[InteractionAssessment.interaction_id],
                    set_={
                        "learner_id": learner_id,
                        "session_id": session_id,
                        "module_id": module_id,
                        "section_id": section_id,
                        "decision": result.decision.value,
                        "confidence": result.confidence,
                        "recommended_next_action": result.recommended_next_action.value,
                        "learner_rationale": result.learner_rationale,
                        "reasoning_summary_json": result.reasoning_summary.model_dump(mode="json"),
                        "cited_chunk_ids": list(result.cited_chunk_ids),
                        "assessment_model": result.assessment_model,
                        "schema_version": result.schema_version,
                        "fallback_used": result.fallback_used,
                        "fallback_reason": result.fallback_reason,
                        "updated_at": func.now(),
                    },
                )
            )
            await session.execute(stmt)

    async def get_interaction_assessment(self, interaction_id: int) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(InteractionAssessment).where(InteractionAssessment.interaction_id == interaction_id)
            )
        if row is None:
            return None
        return {
            "interaction_id": int(row.interaction_id),
            "learner_id": row.learner_id,
            "session_id": int(row.session_id) if row.session_id is not None else None,
            "module_id": row.module_id,
            "section_id": row.section_id,
            "decision": row.decision,
            "confidence": float(row.confidence),
            "recommended_next_action": row.recommended_next_action,
            "learner_rationale": row.learner_rationale,
            "reasoning_summary_json": dict(row.reasoning_summary_json or {}),
            "cited_chunk_ids": list(row.cited_chunk_ids or []),
            "assessment_model": row.assessment_model,
            "schema_version": row.schema_version,
            "fallback_used": bool(row.fallback_used),
            "fallback_reason": row.fallback_reason,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
