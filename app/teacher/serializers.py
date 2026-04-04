from __future__ import annotations

from typing import Any

from app.teacher.orm_models import TeacherArtifactRecord


def teacher_artifact_row_to_dict(row: TeacherArtifactRecord) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "learner_id": row.learner_id,
        "template_id": row.template_id,
        "stage_index": int(row.stage_index),
        "section_id": row.section_id,
        "module_id": row.module_id,
        "decision_kind": row.decision_kind,
        "artifact_key": row.artifact_key,
        "stage_signal": row.stage_signal,
        "decision_source": row.decision_source,
        "context_version": row.context_version,
        "effective_mastery_score": None
        if row.effective_mastery_score is None
        else float(row.effective_mastery_score),
        "weak_topic_count": int(row.weak_topic_count),
        "module_evidence_coverage": None
        if row.module_evidence_coverage is None
        else float(row.module_evidence_coverage),
        "fallback_reason": row.fallback_reason,
        "decision_payload_json": row.decision_payload_json,
        "created_at": row.created_at,
    }


__all__ = ["teacher_artifact_row_to_dict"]
