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


class FeedbackResponse(BaseModel):
    updated: bool
    auto_advanced: bool
    message: str | None = None
    current_item: dict[str, Any] | None = None


class StartRequest(BaseModel):
    learner_id: str


class StartResponse(BaseModel):
    message: str
    plan: dict[str, Any]
    current_item: dict[str, Any] | None


class NextRequest(BaseModel):
    learner_id: str
    force: bool = False


class NextResponse(BaseModel):
    message: str
    current_item: dict[str, Any] | None
