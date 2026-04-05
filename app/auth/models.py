from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.orm_base import Base


class LearnerAuthSession(Base):
    __tablename__ = "learner_auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), nullable=False, index=True)
    session_token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LearnerAuthToken(Base):
    __tablename__ = "learner_auth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    learner_id: Mapped[str] = mapped_column(ForeignKey("learners.id"), nullable=False, index=True)
    token_kind: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


Index(
    "idx_learner_auth_sessions_active",
    LearnerAuthSession.learner_id,
    LearnerAuthSession.expires_at.desc(),
)
Index(
    "idx_learner_auth_tokens_kind_expiry",
    LearnerAuthToken.learner_id,
    LearnerAuthToken.token_kind,
    LearnerAuthToken.expires_at.desc(),
)


__all__ = ["LearnerAuthSession", "LearnerAuthToken"]
