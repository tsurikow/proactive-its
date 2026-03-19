from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


LEARNER_STATE_SCHEMA_VERSION = "learner_state_v1"


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
