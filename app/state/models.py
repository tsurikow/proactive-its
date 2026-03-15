from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Learner(Base):
    __tablename__ = "learners"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC", server_default="UTC")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), nullable=False)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"), nullable=True)
    module_id: Mapped[str | None] = mapped_column(String, nullable=True)
    section_id: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class InteractionSource(Base):
    __tablename__ = "interaction_sources"

    interaction_id: Mapped[int] = mapped_column(ForeignKey("interactions.id"), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String, primary_key=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)


class TopicProgress(Base):
    __tablename__ = "topic_progress"

    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), primary_key=True)
    module_id: Mapped[str | None] = mapped_column(String, nullable=True)
    section_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="in_progress",
        server_default="in_progress",
    )
    mastery_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default="0.0",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class PlanTemplate(Base):
    __tablename__ = "plan_templates"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    book_id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class LearnerPlanState(Base):
    __tablename__ = "learner_plan_state"

    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), primary_key=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("plan_templates.id"), nullable=False)
    current_stage_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    plan_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    completed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class LessonCache(Base):
    __tablename__ = "lesson_cache"

    template_id: Mapped[str] = mapped_column(ForeignKey("plan_templates.id"), primary_key=True)
    stage_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    lesson_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class StartMessageCache(Base):
    __tablename__ = "start_message_cache"

    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), primary_key=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("plan_templates.id"), primary_key=True)
    stage_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    completed_count: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_completed: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    profile_version: Mapped[str] = mapped_column(String, primary_key=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


Index("idx_interactions_learner_created", Interaction.learner_id, Interaction.created_at.desc())
Index("idx_topic_progress_learner_updated", TopicProgress.learner_id, TopicProgress.updated_at.desc())
Index("idx_plan_templates_active", PlanTemplate.is_active)
Index("idx_learner_plan_state_template", LearnerPlanState.template_id)
Index("idx_lesson_cache_updated", LessonCache.updated_at.desc())
Index("idx_start_message_cache_updated", StartMessageCache.updated_at.desc())
