from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.platform.chat.models import ChatTurn, OutboxEvent, TeacherJob, TeacherJobResult
from app.platform.chat.serializers import (
    chat_turn_row_to_dict,
    outbox_event_row_to_dict,
    teacher_job_result_row_to_dict,
    teacher_job_row_to_dict,
)
from app.platform.db import get_session


class DurableChatRepository:
    async def get_chat_turn_by_request_key(self, request_key: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(select(ChatTurn).where(ChatTurn.request_key == request_key).limit(1))
        if row is None:
            return None
        return chat_turn_row_to_dict(row)

    async def get_chat_turn(self, turn_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(select(ChatTurn).where(ChatTurn.id == turn_id).limit(1))
        if row is None:
            return None
        return chat_turn_row_to_dict(row)

    async def get_teacher_job_for_turn(self, turn_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(select(TeacherJob).where(TeacherJob.turn_id == turn_id).limit(1))
        if row is None:
            return None
        return teacher_job_row_to_dict(row)

    async def get_teacher_job_result(self, turn_id: str) -> dict[str, Any] | None:
        async with get_session() as session:
            row = await session.scalar(
                select(TeacherJobResult).where(TeacherJobResult.turn_id == turn_id).limit(1)
            )
        if row is None:
            return None
        return teacher_job_result_row_to_dict(row)

    async def create_chat_turn_bundle(
        self,
        *,
        request_key: str,
        learner_id: str,
        session_id: int | None,
        module_id: str | None,
        section_id: str | None,
        request_payload_json: dict[str, Any],
    ) -> dict[str, Any]:
        async with get_session() as session:
            existing_turn = await session.scalar(
                select(ChatTurn).where(ChatTurn.request_key == request_key).limit(1)
            )
            if existing_turn is not None:
                existing_job = await session.scalar(
                    select(TeacherJob).where(TeacherJob.turn_id == existing_turn.id).limit(1)
                )
                existing_outbox = await session.scalar(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_type == "chat_turn",
                        OutboxEvent.aggregate_id == existing_turn.id,
                        OutboxEvent.event_kind == "chat_generation",
                    )
                    .order_by(OutboxEvent.created_at.desc())
                    .limit(1)
                )
                return {
                    "created": False,
                    "turn": chat_turn_row_to_dict(existing_turn),
                    "job": None if existing_job is None else teacher_job_row_to_dict(existing_job),
                    "outbox_event": None if existing_outbox is None else outbox_event_row_to_dict(existing_outbox),
                }

            turn = ChatTurn(
                id=uuid4().hex,
                request_key=request_key,
                learner_id=learner_id,
                session_id=session_id,
                module_id=module_id,
                section_id=section_id,
                request_payload_json=request_payload_json,
            )
            session.add(turn)
            await session.flush()

            job = TeacherJob(
                id=uuid4().hex,
                turn_id=turn.id,
                job_kind="chat_generation",
                idempotency_key=request_key,
            )
            session.add(job)
            await session.flush()

            outbox_event = OutboxEvent(
                aggregate_type="chat_turn",
                aggregate_id=turn.id,
                event_kind="chat_generation",
                payload_json={
                    "turn_id": turn.id,
                    "job_id": job.id,
                    "request_key": request_key,
                },
            )
            session.add(outbox_event)
            await session.flush()
            return {
                "created": True,
                "turn": chat_turn_row_to_dict(turn),
                "job": teacher_job_row_to_dict(job),
                "outbox_event": outbox_event_row_to_dict(outbox_event),
            }

    async def list_pending_outbox_events(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_kind: str,
    ) -> list[dict[str, Any]]:
        async with get_session() as session:
            rows = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_type == aggregate_type,
                        OutboxEvent.aggregate_id == aggregate_id,
                        OutboxEvent.event_kind == event_kind,
                        OutboxEvent.published_at.is_(None),
                    )
                    .order_by(OutboxEvent.created_at.asc())
                )
            ).all()
        return [outbox_event_row_to_dict(row) for row in rows]

    async def mark_outbox_event_published(self, event_id: int, broker_message_id: str) -> None:
        async with get_session() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id)
                .values(
                    publish_attempt_count=OutboxEvent.publish_attempt_count + 1,
                    broker_message_id=broker_message_id,
                    last_error=None,
                    published_at=func.now(),
                )
            )

    async def mark_outbox_event_failed(self, event_id: int, error_message: str) -> None:
        async with get_session() as session:
            await session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event_id)
                .values(
                    publish_attempt_count=OutboxEvent.publish_attempt_count + 1,
                    last_error=error_message[:1000],
                )
            )

    async def mark_chat_turn_queued(
        self,
        turn_id: str,
        *,
        broker_message_id: str | None,
    ) -> None:
        async with get_session() as session:
            await session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id, ChatTurn.state == "accepted")
                .values(
                    state="queued",
                    updated_at=func.now(),
                )
            )
            await session.execute(
                update(TeacherJob)
                .where(TeacherJob.turn_id == turn_id, TeacherJob.state == "accepted")
                .values(
                    state="queued",
                    broker_message_id=broker_message_id,
                    queued_at=func.now(),
                    updated_at=func.now(),
                )
            )

    async def claim_chat_turn_execution(self, turn_id: str) -> dict[str, Any]:
        async with get_session() as session:
            turn = await session.scalar(
                select(ChatTurn).where(ChatTurn.id == turn_id).with_for_update().limit(1)
            )
            if turn is None:
                return {"status": "missing"}
            job = await session.scalar(
                select(TeacherJob).where(TeacherJob.turn_id == turn_id).with_for_update().limit(1)
            )
            if job is None:
                return {"status": "missing"}
            if turn.state == "completed" and turn.final_result_json is not None:
                return {
                    "status": "completed",
                    "turn": chat_turn_row_to_dict(turn),
                    "job": teacher_job_row_to_dict(job),
                }
            if job.state == "running":
                return {
                    "status": "busy",
                    "turn": chat_turn_row_to_dict(turn),
                    "job": teacher_job_row_to_dict(job),
                }
            if job.state == "completed":
                return {
                    "status": "completed",
                    "turn": chat_turn_row_to_dict(turn),
                    "job": teacher_job_row_to_dict(job),
                }

            await session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id)
                .values(
                    state="running",
                    updated_at=func.now(),
                    error_message=None,
                )
            )
            await session.execute(
                update(TeacherJob)
                .where(TeacherJob.turn_id == turn_id)
                .values(
                    state="running",
                    attempt_count=TeacherJob.attempt_count + 1,
                    started_at=func.now(),
                    updated_at=func.now(),
                    error_message=None,
                )
            )
            refreshed_turn = await session.scalar(select(ChatTurn).where(ChatTurn.id == turn_id).limit(1))
            refreshed_job = await session.scalar(select(TeacherJob).where(TeacherJob.turn_id == turn_id).limit(1))
        return {
            "status": "claimed",
            "turn": None if refreshed_turn is None else chat_turn_row_to_dict(refreshed_turn),
            "job": None if refreshed_job is None else teacher_job_row_to_dict(refreshed_job),
        }

    async def complete_chat_turn(
        self,
        *,
        turn_id: str,
        final_interaction_id: int,
        final_result_json: dict[str, Any],
        worker_metadata_json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        async with get_session() as session:
            turn = await session.scalar(
                select(ChatTurn).where(ChatTurn.id == turn_id).with_for_update().limit(1)
            )
            if turn is None:
                return None
            job = await session.scalar(
                select(TeacherJob).where(TeacherJob.turn_id == turn_id).with_for_update().limit(1)
            )
            if job is None:
                return None
            await session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id)
                .values(
                    state="completed",
                    final_interaction_id=final_interaction_id,
                    final_result_json=final_result_json,
                    updated_at=func.now(),
                    completed_at=func.now(),
                    error_message=None,
                )
            )
            await session.execute(
                update(TeacherJob)
                .where(TeacherJob.turn_id == turn_id)
                .values(
                    state="completed",
                    completed_at=func.now(),
                    updated_at=func.now(),
                    error_message=None,
                )
            )
            result_stmt = (
                pg_insert(TeacherJobResult)
                .values(
                    turn_id=turn_id,
                    job_id=job.id,
                    result_payload_json=final_result_json,
                    worker_metadata_json=worker_metadata_json or {},
                )
                .on_conflict_do_update(
                    index_elements=[TeacherJobResult.turn_id],
                    set_={
                        "job_id": job.id,
                        "result_payload_json": final_result_json,
                        "worker_metadata_json": worker_metadata_json or {},
                        "completed_at": func.now(),
                    },
                )
            )
            await session.execute(result_stmt)
            refreshed_turn = await session.scalar(select(ChatTurn).where(ChatTurn.id == turn_id).limit(1))
        if refreshed_turn is None:
            return None
        return chat_turn_row_to_dict(refreshed_turn)

    async def mark_chat_turn_failed(self, turn_id: str, *, error_message: str) -> None:
        async with get_session() as session:
            await session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id)
                .values(
                    state="failed",
                    updated_at=func.now(),
                    error_message=error_message[:4000],
                )
            )
            await session.execute(
                update(TeacherJob)
                .where(TeacherJob.turn_id == turn_id)
                .values(
                    state="failed",
                    updated_at=func.now(),
                    error_message=error_message[:4000],
                )
            )

    async def mark_chat_turn_degraded(
        self,
        turn_id: str,
        *,
        fallback_reason: str,
    ) -> None:
        async with get_session() as session:
            await session.execute(
                update(ChatTurn)
                .where(ChatTurn.id == turn_id)
                .values(
                    degraded_execution=True,
                    fallback_reason=fallback_reason[:500],
                    updated_at=func.now(),
                )
            )
            await session.execute(
                update(TeacherJob)
                .where(TeacherJob.turn_id == turn_id)
                .values(
                    degraded_execution=True,
                    fallback_reason=fallback_reason[:500],
                    updated_at=func.now(),
                )
            )

    async def list_recent_turns_for_request(
        self,
        *,
        learner_id: str,
        module_id: str | None,
        section_id: str | None,
        message: str,
        created_after: datetime,
    ) -> list[dict[str, Any]]:
        async with get_session() as session:
            rows = (
                await session.scalars(
                    select(ChatTurn)
                    .where(
                        ChatTurn.learner_id == learner_id,
                        ChatTurn.module_id == module_id,
                        ChatTurn.section_id == section_id,
                        ChatTurn.created_at >= created_after.astimezone(UTC),
                        ChatTurn.request_payload_json["message"].astext == message,
                    )
                    .order_by(ChatTurn.created_at.desc())
                )
            ).all()
        return [chat_turn_row_to_dict(row) for row in rows]
