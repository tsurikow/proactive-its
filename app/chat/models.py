from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class AssessmentDecision(str, Enum):
    CORRECT = "correct"
    PARTIALLY_CORRECT = "partially_correct"
    MISCONCEPTION = "misconception"
    PROCEDURAL_ERROR = "procedural_error"
    OFF_TOPIC = "off_topic"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RecommendedNextAction(str, Enum):
    AFFIRM_AND_ADVANCE = "affirm_and_advance"
    REINFORCE_KEY_POINT = "reinforce_key_point"
    CORRECT_MISCONCEPTION = "correct_misconception"
    REQUEST_STEP_REVISION = "request_step_revision"
    ASK_FOR_CLARIFICATION = "ask_for_clarification"
    REDIRECT_TO_TOPIC = "redirect_to_topic"


class AssessmentReasoningSummary(BaseModel):
    evidence_basis: str = Field(min_length=1, max_length=280)
    key_issue: str | None = Field(default=None, max_length=240)
    strength_signals: list[str] = Field(default_factory=list, max_length=4)
    risk_flags: list[str] = Field(default_factory=list, max_length=4)


class AssessmentStructuredPayload(BaseModel):
    decision: AssessmentDecision
    confidence: float = Field(ge=0.0, le=1.0)
    learner_rationale: str = Field(min_length=1, max_length=500)
    reasoning_summary: AssessmentReasoningSummary
    recommended_next_action: RecommendedNextAction
    citations: list[str] = Field(default_factory=list, max_length=4)


class AssessmentResult(BaseModel):
    decision: AssessmentDecision
    confidence: float = Field(ge=0.0, le=1.0)
    learner_rationale: str = Field(min_length=1, max_length=500)
    reasoning_summary: AssessmentReasoningSummary
    recommended_next_action: RecommendedNextAction
    section_id: str | None = None
    module_id: str | None = None
    cited_chunk_ids: list[str] = Field(default_factory=list)
    assessment_model: str
    schema_version: str
    fallback_used: bool = False
    fallback_reason: str | None = None
    normalization_events: list[str] = Field(default_factory=list, exclude=True)
