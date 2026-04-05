from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

class Citation(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    breadcrumb: list[str]
    quote: str


class RetrievalDebug(BaseModel):
    top_k: int
    filtered_by: dict[str, str]
    scores: list[dict[str, float | str]]
    top_score: float | None = None
    citation_fallback_used: bool | None = None
    rewrite_attempted: bool | None = None
    rewrite_query: str | None = None
    rewrite_accepted: bool | None = None
    rewrite_reason: str | None = None
    evidence_chars: int | None = None
    weak_evidence: bool | None = None
    eligible_query_term_count: int | None = None
    matched_query_term_count: int | None = None
    matched_query_terms: list[str] | None = None
    query_overlap_ratio: float | None = None
    offtopic_suspected: bool | None = None
    weak_evidence_reason: str | None = None
    retrieval_mode: str | None = None
    timings_ms: dict[str, float] | None = None


class FeedbackRequest(BaseModel):
    learner_id: str | None = None
    interaction_id: int
    confidence: int = Field(ge=1, le=5)
    helpful: bool | None = None


class AuthLearner(BaseModel):
    id: str
    first_name: str
    last_name: str
    display_name: str
    email: str
    is_active: bool


class AuthResponse(BaseModel):
    learner: AuthLearner


class AuthErrorResponse(BaseModel):
    code: str
    message: str
    field_errors: dict[str, str] | None = None


class SignupRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class BasicSuccessResponse(BaseModel):
    ok: bool = True


class StageInfo(BaseModel):
    stage_index: int
    section_id: str
    module_id: str | None = None
    parent_doc_id: str | None = None
    title: str | None = None
    breadcrumb: list[str] = Field(default_factory=list)


class LessonStep(BaseModel):
    step_id: str
    step_type: Literal["goal", "definition", "concept", "example", "check", "remediation", "summary"]
    title: str
    content_md: str
    source_chunk_ids: list[str]
    order_index: int


class PlanTreeNode(BaseModel):
    node_type: Literal["book", "group", "stage"]
    title: str
    breadcrumb: list[str] = Field(default_factory=list)
    children: list["PlanTreeNode"] = Field(default_factory=list)
    stage_index: int | None = None
    section_id: str | None = None
    module_id: str | None = None
    completed: bool | None = None
    completed_leaf_count: int = 0
    total_leaf_count: int = 0
    mastery_score: float = 0.0
    is_current_branch: bool = False
    is_current_stage: bool = False


class LessonPayload(BaseModel):
    section_summary_md: str | None = None
    lesson_steps: list[LessonStep] = Field(default_factory=list)
    cached: bool = False
    generation_mode: str | None = None
    preservation_report: dict[str, Any] | None = None


class PlanProgress(BaseModel):
    template_id: str
    total_stages: int
    completed_stages: int
    mastery_score: float = 0.0
    tree: PlanTreeNode | None = None


class FeedbackResponse(BaseModel):
    updated: bool
    auto_advanced: bool
    message: str | None = None
    current_stage: StageInfo | None = None


PlanTreeNode.model_rebuild()
