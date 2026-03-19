from __future__ import annotations

from datetime import UTC, datetime
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.learner.models import LEARNER_STATE_SCHEMA_VERSION, MasteryUpdate
from app.platform.db import get_session
from app.platform.models import (
    LearnerProfileRecord,
    MasterySnapshotRecord,
    TopicEvidenceRecord,
    TopicProgress,
)
from app.platform.serializers import (
    learner_profile_row_to_dict,
    mastery_snapshot_row_to_dict,
    topic_evidence_row_to_dict,
)


class LearnerRepository:
    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        async with get_session() as session:
            yield session

    async def get_profile(self, learner_id: str, *, session: AsyncSession | None = None) -> dict[str, Any] | None:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.get_profile(learner_id, session=owned_session)
        row = await session.scalar(
            select(LearnerProfileRecord).where(LearnerProfileRecord.learner_id == learner_id).limit(1)
        )
        if row is None:
            return None
        return learner_profile_row_to_dict(row)

    async def get_or_create_profile(
        self,
        learner_id: str,
        *,
        active_template_id: str | None = None,
        touch_evidence: bool = False,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.get_or_create_profile(
                    learner_id,
                    active_template_id=active_template_id,
                    touch_evidence=touch_evidence,
                    session=owned_session,
                )
        now = datetime.now(UTC)
        values = {
            "learner_id": learner_id,
            "active_template_id": active_template_id,
            "state_schema_version": LEARNER_STATE_SCHEMA_VERSION,
            "last_activity_at": now,
        }
        if touch_evidence:
            values["last_evidence_at"] = now
        set_values: dict[str, Any] = {
            "last_activity_at": now,
            "updated_at": now,
            "state_schema_version": LEARNER_STATE_SCHEMA_VERSION,
        }
        if active_template_id is not None:
            set_values["active_template_id"] = active_template_id
        if touch_evidence:
            set_values["last_evidence_at"] = now
        stmt = (
            pg_insert(LearnerProfileRecord)
            .values(**values)
            .on_conflict_do_update(
                index_elements=[LearnerProfileRecord.learner_id],
                set_=set_values,
            )
        )
        await session.execute(stmt)
        row = await session.scalar(
            select(LearnerProfileRecord).where(LearnerProfileRecord.learner_id == learner_id).limit(1)
        )
        if row is None:
            raise RuntimeError("Failed to persist learner profile")
        return learner_profile_row_to_dict(row)

    async def upsert_topic_progress_projection(
        self,
        *,
        learner_id: str,
        section_id: str,
        module_id: str | None,
        status: str,
        mastery_score: float,
        session: AsyncSession | None = None,
    ) -> None:
        if session is None:
            async with self.session_scope() as owned_session:
                await self.upsert_topic_progress_projection(
                    learner_id=learner_id,
                    section_id=section_id,
                    module_id=module_id,
                    status=status,
                    mastery_score=mastery_score,
                    session=owned_session,
                )
                return
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

    async def append_topic_evidence(
        self,
        update: MasteryUpdate,
        *,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.append_topic_evidence(update, session=owned_session)
        row = TopicEvidenceRecord(
            learner_id=update.learner_id,
            section_id=update.section_id,
            module_id=update.module_id,
            interaction_id=update.interaction_id,
            source_kind=update.source_kind,
            assessment_decision=update.assessment_decision,
            recommended_next_action=update.recommended_next_action,
            confidence_submitted=update.confidence_submitted,
            mastery_delta=update.mastery_delta,
            mastery_before=update.mastery_before,
            mastery_after=update.mastery_after,
            status_after=update.status_after,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        return topic_evidence_row_to_dict(row)

    async def get_mastery_snapshot(
        self,
        learner_id: str,
        section_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> dict[str, Any] | None:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.get_mastery_snapshot(learner_id, section_id, session=owned_session)
        row = await session.scalar(
            select(MasterySnapshotRecord)
            .where(
                MasterySnapshotRecord.learner_id == learner_id,
                MasterySnapshotRecord.section_id == section_id,
            )
            .limit(1)
        )
        if row is None:
            return None
        return mastery_snapshot_row_to_dict(row)

    async def upsert_mastery_snapshot(
        self,
        update: MasteryUpdate,
        *,
        session: AsyncSession | None = None,
    ) -> dict[str, Any]:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.upsert_mastery_snapshot(update, session=owned_session)
        row = await session.scalar(
            select(MasterySnapshotRecord)
            .where(
                MasterySnapshotRecord.learner_id == update.learner_id,
                MasterySnapshotRecord.section_id == update.section_id,
            )
            .limit(1)
        )
        if row is None:
            now = datetime.now(UTC)
            row = MasterySnapshotRecord(
                learner_id=update.learner_id,
                section_id=update.section_id,
                module_id=update.module_id,
                mastery_score=update.mastery_after,
                status=update.status_after,
                evidence_count=1,
                last_evidence_at=now,
                last_update_source=update.source_kind,
                last_interaction_id=update.interaction_id,
                last_assessment_decision=update.assessment_decision,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            await session.flush()
        else:
            now = datetime.now(UTC)
            row.module_id = update.module_id
            row.mastery_score = update.mastery_after
            row.status = update.status_after
            row.evidence_count = int(row.evidence_count) + 1
            row.last_evidence_at = now
            row.last_update_source = update.source_kind
            row.last_interaction_id = update.interaction_id
            row.last_assessment_decision = update.assessment_decision
            row.updated_at = now
            await session.flush()
        await session.refresh(row)
        return mastery_snapshot_row_to_dict(row)

    async def list_recent_topic_evidence(
        self,
        learner_id: str,
        section_id: str,
        *,
        limit: int = 5,
        session: AsyncSession | None = None,
    ) -> list[dict[str, Any]]:
        if session is None:
            async with self.session_scope() as owned_session:
                return await self.list_recent_topic_evidence(
                    learner_id,
                    section_id,
                    limit=limit,
                    session=owned_session,
                )
        rows = (
            await session.scalars(
                select(TopicEvidenceRecord)
                .where(
                    TopicEvidenceRecord.learner_id == learner_id,
                    TopicEvidenceRecord.section_id == section_id,
                )
                .order_by(TopicEvidenceRecord.created_at.desc(), TopicEvidenceRecord.id.desc())
                .limit(limit)
            )
        ).all()
        return [topic_evidence_row_to_dict(row) for row in rows]
