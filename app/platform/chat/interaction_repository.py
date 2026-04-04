from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.platform.chat.models import Interaction, InteractionSource, SessionRecord
from app.platform.db import get_session


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
