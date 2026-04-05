from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictTransportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GenerationPayloadTransport(StrictTransportModel):
    answer_md: str = Field(
        description="Grounded Markdown answer for the learner. Use only retrieved evidence and no source metadata labels in prose."
    )
    citations: list[str] = Field(
        description="Ordered source labels that support the answer, such as S1 or S2."
    )
    figure_links: list[str] = Field(
        description="Subset of relevant figure links that may be appended to the answer when directly helpful."
    )


class QueryRewriteTransport(StrictTransportModel):
    rewritten_query: str = Field(
        description="One concise retrieval query rewritten from the learner question without answering it."
    )


class ChatTurnContext(StrictTransportModel):
    current_module_id: str | None = Field(
        default=None,
        description="Current module id for the learner turn when available.",
    )
    current_section_id: str | None = Field(
        default=None,
        description="Current section id for the learner turn when available.",
    )


class ChatTurnRequest(StrictTransportModel):
    learner_id: str = Field(description="Learner identifier for the chat turn.")
    message: str = Field(description="Learner message for the current chat turn.")
    context: ChatTurnContext = Field(
        default_factory=ChatTurnContext,
        description="Current turn-local module and section context.",
    )
    teacher_context_json: dict[str, Any] | None = Field(
        default=None,
        description="Optional serialized teacher context passed into the grounded chat path.",
    )
