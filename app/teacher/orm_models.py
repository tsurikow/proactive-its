from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.orm_base import Base


class LessonCache(Base):
    __tablename__ = "lesson_cache"

    template_id: Mapped[str] = mapped_column(ForeignKey("plan_templates.id"), primary_key=True)
    stage_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    artifact_key: Mapped[str] = mapped_column(String, primary_key=True)
    context_version: Mapped[str] = mapped_column(String, primary_key=True)
    lesson_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class TeacherArtifactRecord(Base):
    __tablename__ = "teacher_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("plan_templates.id"), nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_id: Mapped[str] = mapped_column(String, nullable=False)
    module_id: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_kind: Mapped[str] = mapped_column(String, nullable=False)
    artifact_key: Mapped[str] = mapped_column(String, nullable=False)
    stage_signal: Mapped[str] = mapped_column(String, nullable=False)
    decision_source: Mapped[str] = mapped_column(String, nullable=False)
    context_version: Mapped[str] = mapped_column(String, nullable=False)
    effective_mastery_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    weak_topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    module_evidence_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    decision_payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index("idx_lesson_cache_updated", LessonCache.updated_at.desc())
Index(
    "idx_teacher_artifacts_learner_stage_created",
    TeacherArtifactRecord.learner_id,
    TeacherArtifactRecord.stage_index,
    TeacherArtifactRecord.created_at.desc(),
)


__all__ = ["LessonCache", "TeacherArtifactRecord"]
