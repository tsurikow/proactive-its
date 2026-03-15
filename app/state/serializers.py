from __future__ import annotations

from typing import Any

from app.state.models import LearnerPlanState, PlanTemplate


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
