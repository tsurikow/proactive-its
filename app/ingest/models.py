from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SourceInfo(BaseModel):
    cnxml_path: str | None = None
    uuid: str | None = None
    chunk: str | None = None


class FigureRef(BaseModel):
    id: str | None = None
    src: str | None = None
    alt: str | None = None
    caption: str | None = None
    mime_type: str | None = None


class DocumentRecord(BaseModel):
    doc_id: str
    book_id: str
    module_id: str | None = None
    section_id: str | None = None
    title: str
    breadcrumb: list[str]
    content_md: str
    doc_type: str
    learning_objectives: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    figures: list[FigureRef] = Field(default_factory=list)
    source: SourceInfo | dict[str, Any] = Field(default_factory=dict)
