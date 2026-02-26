"""initial schema

Revision ID: 20260219_0001
Revises:
Create Date: 2026-02-19 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260219_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "learners" not in existing_tables:
        op.create_table(
            "learners",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("timezone", sa.String(), server_default=sa.text("'UTC'"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "sessions" not in existing_tables:
        op.create_table(
            "sessions",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("learner_id", sa.String(), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "interactions" not in existing_tables:
        op.create_table(
            "interactions",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("learner_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.BigInteger(), nullable=True),
            sa.Column("module_id", sa.String(), nullable=True),
            sa.Column("section_id", sa.String(), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
            sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "interaction_sources" not in existing_tables:
        op.create_table(
            "interaction_sources",
            sa.Column("interaction_id", sa.BigInteger(), nullable=False),
            sa.Column("chunk_id", sa.String(), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"]),
            sa.PrimaryKeyConstraint("interaction_id", "chunk_id"),
        )

    if "topic_progress" not in existing_tables:
        op.create_table(
            "topic_progress",
            sa.Column("learner_id", sa.String(), nullable=False),
            sa.Column("module_id", sa.String(), nullable=True),
            sa.Column("section_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), server_default=sa.text("'in_progress'"), nullable=False),
            sa.Column("mastery_score", sa.Float(), server_default=sa.text("0.0"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
            sa.PrimaryKeyConstraint("learner_id", "section_id"),
        )

    if "study_plans" not in existing_tables:
        op.create_table(
            "study_plans",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("learner_id", sa.String(), nullable=False),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if "plan_templates" not in existing_tables:
        op.create_table(
            "plan_templates",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("book_id", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("plan_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "learner_plan_state" not in existing_tables:
        op.create_table(
            "learner_plan_state",
            sa.Column("learner_id", sa.String(), nullable=False),
            sa.Column("template_id", sa.String(), nullable=False),
            sa.Column("current_stage_index", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("plan_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
            sa.Column("completed_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
            sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
            sa.PrimaryKeyConstraint("learner_id"),
        )

    if "lesson_cache" not in existing_tables:
        op.create_table(
            "lesson_cache",
            sa.Column("learner_id", sa.String(), nullable=False),
            sa.Column("template_id", sa.String(), nullable=False),
            sa.Column("stage_index", sa.Integer(), nullable=False),
            sa.Column("lesson_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
            sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
            sa.PrimaryKeyConstraint("learner_id", "template_id", "stage_index"),
        )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_study_plans_learner_week ON study_plans (learner_id, week_start)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_interactions_learner_created ON interactions (learner_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_topic_progress_learner_updated ON topic_progress (learner_id, updated_at DESC)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_plan_templates_active ON plan_templates (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_learner_plan_state_template ON learner_plan_state (template_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_lesson_cache_learner_updated ON lesson_cache (learner_id, updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lesson_cache_learner_updated")
    op.execute("DROP INDEX IF EXISTS idx_learner_plan_state_template")
    op.execute("DROP INDEX IF EXISTS idx_plan_templates_active")
    op.drop_index("idx_study_plans_learner_week", table_name="study_plans")
    op.drop_index("idx_topic_progress_learner_updated", table_name="topic_progress")
    op.drop_index("idx_interactions_learner_created", table_name="interactions")

    op.drop_table("lesson_cache")
    op.drop_table("learner_plan_state")
    op.drop_table("plan_templates")
    op.drop_table("study_plans")
    op.drop_table("topic_progress")
    op.drop_table("interaction_sources")
    op.drop_table("interactions")
    op.drop_table("sessions")
    op.drop_table("learners")
