from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.state.models import AdaptationContext
from app.teacher.models import TeacherMessageResult


def stable_json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def stable_json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=True, default=stable_json_default)


def section_understanding_artifact_key(*, section_id: str, source_hash: str, context_version: str) -> str:
    return f"section_understanding:{section_id}:{source_hash}:{context_version}"


def lesson_cache_artifact_key(
    *,
    section_id: str,
    stage_signal: str,
    render_signature: str,
    context_version: str,
    planner_fingerprint: str,
    learner_memory_fingerprint: str,
) -> str:
    return (
        f"lesson:{section_id}:{stage_signal}:{render_signature}:{context_version}"
        f"|planner:{planner_fingerprint}|memory:{learner_memory_fingerprint}"
    )


def lesson_plan_fingerprint(lesson_plan_draft: TeacherMessageResult | None) -> str:
    if lesson_plan_draft is None:
        return "none"
    raw = json.dumps(lesson_plan_draft.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def grounding_analysis_fingerprint(grounding_analysis: TeacherMessageResult) -> str:
    raw = json.dumps(grounding_analysis.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def learner_memory_fingerprint(learner_memory_summary: TeacherMessageResult | None) -> str:
    if learner_memory_summary is None:
        return "none"
    raw = json.dumps(learner_memory_summary.model_dump(mode="json"), sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def learner_model_source_signature(
    *,
    current_stage: dict[str, Any] | None,
    adaptation_context: AdaptationContext,
    source_payload: dict[str, Any],
    recent_decision_summary: dict[str, Any],
) -> str:
    raw = stable_json_dumps(
        {
            "surface_scope": "shared_teacher_turn",
            "current_stage": None
            if current_stage is None
            else {
                "stage_index": int(current_stage.get("stage_index", -1)),
                "section_id": str(current_stage.get("section_id") or ""),
                "module_id": None if current_stage.get("module_id") is None else str(current_stage.get("module_id")),
            },
            "stage_signal": adaptation_context.stage_signal,
            "context_version": adaptation_context.context_version,
            "source_payload": source_payload,
            "recent_decision_summary": recent_decision_summary,
        }
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
def lesson_adaptation_brief(
    adaptation_context: AdaptationContext,
) -> str:
    current_mastery = (
        None if adaptation_context.current_topic is None else adaptation_context.current_topic.effective_mastery_score
    )
    return (
        f"Stage signal={adaptation_context.stage_signal}; "
        f"current_effective_mastery={current_mastery}; "
        f"weak_related_topics={len(adaptation_context.weak_related_topics)}; "
        f"module_coverage={adaptation_context.module_summary.get('evidence_coverage_ratio')}; "
        f"last_assessment_decision={None if adaptation_context.current_topic is None else adaptation_context.current_topic.last_assessment_decision}"
    )
