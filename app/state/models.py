from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


LEARNER_STATE_SCHEMA_VERSION = "learner_state_v1"
ADAPTATION_CONTEXT_VERSION = "adaptation_context_v1"


class LearnerProfile(BaseModel):
    learner_id: str
    active_template_id: str | None = None
    state_schema_version: str = LEARNER_STATE_SCHEMA_VERSION
    last_activity_at: datetime
    last_evidence_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MasterySnapshot(BaseModel):
    learner_id: str
    section_id: str
    module_id: str | None = None
    mastery_score: float = Field(ge=0.0, le=1.0)
    status: str
    evidence_count: int = Field(ge=0)
    last_evidence_at: datetime
    last_update_source: str
    last_interaction_id: int | None = None
    last_assessment_decision: str | None = None
    created_at: datetime
    updated_at: datetime


class TopicEvidence(BaseModel):
    id: int
    learner_id: str
    section_id: str
    module_id: str | None = None
    interaction_id: int | None = None
    source_kind: str
    assessment_decision: str | None = None
    recommended_next_action: str | None = None
    confidence_submitted: int | None = None
    mastery_delta: float
    mastery_before: float = Field(ge=0.0, le=1.0)
    mastery_after: float = Field(ge=0.0, le=1.0)
    status_after: str
    created_at: datetime


class MasteryUpdate(BaseModel):
    learner_id: str
    section_id: str
    module_id: str | None = None
    interaction_id: int | None = None
    source_kind: str
    assessment_decision: str | None = None
    recommended_next_action: str | None = None
    confidence_submitted: int | None = None
    mastery_delta: float
    mastery_before: float = Field(ge=0.0, le=1.0)
    mastery_after: float = Field(ge=0.0, le=1.0)
    status_after: str
    active_template_id: str | None = None


class StageMasteryView(BaseModel):
    section_id: str
    module_id: str | None = None
    raw_mastery_score: float = Field(ge=0.0, le=1.0)
    effective_mastery_score: float = Field(ge=0.0, le=1.0)
    effective_status: str
    decay_multiplier: float = Field(ge=0.0, le=1.0)
    hours_since_last_evidence: float = Field(ge=0.0)
    evidence_count: int = Field(ge=0)
    last_assessment_decision: str | None = None
    last_update_source: str | None = None


class RecentEvidencePattern(BaseModel):
    correct_like_count: int = Field(ge=0)
    support_needed_count: int = Field(ge=0)
    fallback_confidence_count: int = Field(ge=0)
    latest_assessment_decision: str | None = None


class AdaptationContext(BaseModel):
    learner_id: str
    current_stage: dict[str, object] | None = None
    stage_signal: str
    current_topic: StageMasteryView | None = None
    module_summary: dict[str, float | int | None]
    weak_related_topics: list[StageMasteryView] = Field(default_factory=list)
    strong_related_topics: list[StageMasteryView] = Field(default_factory=list)
    recent_pattern: RecentEvidencePattern
    context_version: str = ADAPTATION_CONTEXT_VERSION
