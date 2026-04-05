from __future__ import annotations

from app.platform.chat.models import ChatTurn, OutboxEvent, TeacherJob, TeacherJobResult


def chat_turn_row_to_dict(row: ChatTurn) -> dict:
    return {
        "id": row.id,
        "request_key": row.request_key,
        "learner_id": row.learner_id,
        "session_id": int(row.session_id) if row.session_id is not None else None,
        "module_id": row.module_id,
        "section_id": row.section_id,
        "state": row.state,
        "request_payload_json": dict(row.request_payload_json or {}),
        "final_interaction_id": int(row.final_interaction_id) if row.final_interaction_id is not None else None,
        "final_result_json": None if row.final_result_json is None else dict(row.final_result_json),
        "degraded_execution": bool(row.degraded_execution),
        "fallback_reason": row.fallback_reason,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "completed_at": row.completed_at,
    }


def teacher_job_row_to_dict(row: TeacherJob) -> dict:
    return {
        "id": row.id,
        "turn_id": row.turn_id,
        "job_kind": row.job_kind,
        "state": row.state,
        "idempotency_key": row.idempotency_key,
        "broker_message_id": row.broker_message_id,
        "attempt_count": int(row.attempt_count),
        "degraded_execution": bool(row.degraded_execution),
        "fallback_reason": row.fallback_reason,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "queued_at": row.queued_at,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "updated_at": row.updated_at,
    }


def teacher_job_result_row_to_dict(row: TeacherJobResult) -> dict:
    return {
        "id": int(row.id),
        "turn_id": row.turn_id,
        "job_id": row.job_id,
        "result_payload_json": dict(row.result_payload_json or {}),
        "worker_metadata_json": dict(row.worker_metadata_json or {}),
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


def outbox_event_row_to_dict(row: OutboxEvent) -> dict:
    return {
        "id": int(row.id),
        "aggregate_type": row.aggregate_type,
        "aggregate_id": row.aggregate_id,
        "event_kind": row.event_kind,
        "payload_json": dict(row.payload_json or {}),
        "publish_attempt_count": int(row.publish_attempt_count),
        "broker_message_id": row.broker_message_id,
        "last_error": row.last_error,
        "created_at": row.created_at,
        "published_at": row.published_at,
    }


__all__ = [
    "chat_turn_row_to_dict",
    "teacher_job_row_to_dict",
    "teacher_job_result_row_to_dict",
    "outbox_event_row_to_dict",
]
