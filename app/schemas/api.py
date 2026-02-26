from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatContext(BaseModel):
    current_module_id: str | None = None
    current_section_id: str | None = None


class ChatRequest(BaseModel):
    learner_id: str
    message: str
    context: ChatContext = Field(default_factory=ChatContext)
    mode: Literal["tutor", "quiz"] = "tutor"


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
    dense_top_score: float | None = None
    dense_failed: bool | None = None
    best_term_overlap: float | None = None
    required_term_matches: int | None = None
    query_intent: str | None = None
    evidence_chars: int | None = None
    weak_evidence: bool | None = None
    retrieval_mode: str | None = None
    embedding_backend: str | None = None
    timings_ms: dict[str, float] | None = None


class ChatResponse(BaseModel):
    interaction_id: int
    answer_md: str
    citations: list[Citation]
    retrieval_debug: RetrievalDebug | None = None


class FeedbackRequest(BaseModel):
    learner_id: str
    interaction_id: int
    confidence: int = Field(ge=1, le=5)
    helpful: bool | None = None


class StartRequest(BaseModel):
    learner_id: str


class NextRequest(BaseModel):
    learner_id: str
    force: bool = False


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


class StartResponse(BaseModel):
    message: str
    plan: PlanProgress
    current_stage: StageInfo | None
    plan_completed: bool


class NextResponse(BaseModel):
    message: str
    current_stage: StageInfo | None
    plan_completed: bool


class LessonCurrentResponse(BaseModel):
    current_stage: StageInfo | None
    lesson: LessonPayload | None
    plan_completed: bool


class FeedbackResponse(BaseModel):
    updated: bool
    auto_advanced: bool
    message: str | None = None
    current_stage: StageInfo | None = None
