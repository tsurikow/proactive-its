from __future__ import annotations

from typing import Any

from app.platform.models import (
    LearnerPlanState,
    LearnerProfileRecord,
    MasterySnapshotRecord,
    PlanTemplate,
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
