from __future__ import annotations

from typing import Any

from app.state.orm_models import (
    LearnerMemoryRecord,
    LearnerPlanState,
    LearnerProfileRecord,
    LearningDebtRecord,
    MasterySnapshotRecord,
    PlanTemplate,
    TeacherSessionEventRecord,
    TopicEvidenceRecord,
)


def template_row_to_dict(row: PlanTemplate) -> dict[str, Any]:
    return {
        "id": row.id,
        "book_id": row.book_id,
        "version": int(row.version),
        "plan_json": row.plan_json,
        "is_active": bool(row.is_active),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def plan_state_row_to_dict(row: LearnerPlanState) -> dict[str, Any]:
    return {
        "learner_id": row.learner_id,
        "template_id": row.template_id,
        "current_stage_index": int(row.current_stage_index),
        "plan_completed": bool(row.plan_completed),
        "completed_count": int(row.completed_count),
        "updated_at": row.updated_at,
    }


def learner_profile_row_to_dict(row: LearnerProfileRecord) -> dict[str, Any]:
    return {
        "learner_id": row.learner_id,
        "active_template_id": row.active_template_id,
        "state_schema_version": row.state_schema_version,
        "last_activity_at": row.last_activity_at,
        "last_evidence_at": row.last_evidence_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def mastery_snapshot_row_to_dict(row: MasterySnapshotRecord) -> dict[str, Any]:
    return {
        "learner_id": row.learner_id,
        "section_id": row.section_id,
        "module_id": row.module_id,
        "mastery_score": float(row.mastery_score),
        "status": row.status,
        "evidence_count": int(row.evidence_count),
        "last_evidence_at": row.last_evidence_at,
        "last_update_source": row.last_update_source,
        "last_interaction_id": int(row.last_interaction_id) if row.last_interaction_id is not None else None,
        "last_assessment_decision": row.last_assessment_decision,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def topic_evidence_row_to_dict(row: TopicEvidenceRecord) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "learner_id": row.learner_id,
        "section_id": row.section_id,
        "module_id": row.module_id,
        "interaction_id": int(row.interaction_id) if row.interaction_id is not None else None,
        "source_kind": row.source_kind,
        "assessment_decision": row.assessment_decision,
        "recommended_next_action": row.recommended_next_action,
        "confidence_submitted": int(row.confidence_submitted) if row.confidence_submitted is not None else None,
        "mastery_delta": float(row.mastery_delta),
        "mastery_before": float(row.mastery_before),
        "mastery_after": float(row.mastery_after),
        "status_after": row.status_after,
        "created_at": row.created_at,
    }


def teacher_session_event_row_to_dict(row: TeacherSessionEventRecord) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "learner_id": row.learner_id,
        "template_id": row.template_id,
        "interaction_id": int(row.interaction_id) if row.interaction_id is not None else None,
        "event_type": row.event_type,
        "proposal_type": row.proposal_type,
        "stage_index": int(row.stage_index) if row.stage_index is not None else None,
        "section_id": row.section_id,
        "module_id": row.module_id,
        "message": row.message,
        "event_payload_json": dict(row.event_payload_json or {}),
        "created_at": row.created_at,
    }


def learning_debt_row_to_dict(row: LearningDebtRecord) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "learner_id": row.learner_id,
        "template_id": row.template_id,
        "section_id": row.section_id,
        "module_id": row.module_id,
        "debt_kind": row.debt_kind,
        "status": row.status,
        "rationale": row.rationale,
        "source_event_id": int(row.source_event_id) if row.source_event_id is not None else None,
        "source_interaction_id": int(row.source_interaction_id) if row.source_interaction_id is not None else None,
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
    }


def learner_memory_row_to_dict(row: LearnerMemoryRecord) -> dict[str, Any]:
    return {
        "learner_id": row.learner_id,
        "template_id": row.template_id,
        "memory_json": dict(row.memory_json or {}),
        "session_count": int(row.session_count),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


__all__ = [
    "template_row_to_dict",
    "plan_state_row_to_dict",
    "learner_profile_row_to_dict",
    "mastery_snapshot_row_to_dict",
    "topic_evidence_row_to_dict",
    "teacher_session_event_row_to_dict",
    "learning_debt_row_to_dict",
    "learner_memory_row_to_dict",
]
