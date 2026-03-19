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
    op.create_table(
        "learners",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "interactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
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

    op.create_table(
        "interaction_sources",
        sa.Column("interaction_id", sa.Integer(), nullable=False),
        sa.Column("chunk_id", sa.String(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"]),
        sa.PrimaryKeyConstraint("interaction_id", "chunk_id"),
    )

    op.create_table(
        "interaction_assessments",
        sa.Column("interaction_id", sa.Integer(), nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("section_id", sa.String(), nullable=True),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("recommended_next_action", sa.String(), nullable=False),
        sa.Column("learner_rationale", sa.Text(), nullable=False),
        sa.Column("reasoning_summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cited_chunk_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assessment_model", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fallback_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"]),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("interaction_id"),
    )

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

    op.create_table(
        "learner_profiles",
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("active_template_id", sa.String(), nullable=True),
        sa.Column("state_schema_version", sa.String(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["active_template_id"], ["plan_templates.id"]),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.PrimaryKeyConstraint("learner_id"),
    )

    op.create_table(
        "mastery_snapshots",
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("section_id", sa.String(), nullable=False),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("mastery_score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_update_source", sa.String(), nullable=False),
        sa.Column("last_interaction_id", sa.Integer(), nullable=True),
        sa.Column("last_assessment_decision", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["last_interaction_id"], ["interactions.id"]),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.PrimaryKeyConstraint("learner_id", "section_id"),
    )

    op.create_table(
        "topic_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("section_id", sa.String(), nullable=False),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("interaction_id", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("assessment_decision", sa.String(), nullable=True),
        sa.Column("recommended_next_action", sa.String(), nullable=True),
        sa.Column("confidence_submitted", sa.Integer(), nullable=True),
        sa.Column("mastery_delta", sa.Float(), nullable=False),
        sa.Column("mastery_before", sa.Float(), nullable=False),
        sa.Column("mastery_after", sa.Float(), nullable=False),
        sa.Column("status_after", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"]),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "lesson_cache",
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("lesson_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
        sa.PrimaryKeyConstraint("template_id", "stage_index"),
    )

    op.create_table(
        "start_message_cache",
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("completed_count", sa.Integer(), nullable=False),
        sa.Column("plan_completed", sa.Boolean(), nullable=False),
        sa.Column("profile_version", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
        sa.PrimaryKeyConstraint(
            "learner_id",
            "template_id",
            "stage_index",
            "completed_count",
            "plan_completed",
            "profile_version",
        ),
    )

    op.execute("CREATE INDEX idx_interactions_learner_created ON interactions (learner_id, created_at DESC)")
    op.execute(
        "CREATE INDEX idx_interaction_assessments_learner_created "
        "ON interaction_assessments (learner_id, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_topic_progress_learner_updated ON topic_progress (learner_id, updated_at DESC)")
    op.execute("CREATE INDEX idx_learner_profiles_updated ON learner_profiles (updated_at DESC)")
    op.execute(
        "CREATE INDEX idx_mastery_snapshots_learner_updated "
        "ON mastery_snapshots (learner_id, updated_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_topic_evidence_learner_section_created "
        "ON topic_evidence (learner_id, section_id, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_plan_templates_active ON plan_templates (is_active)")
    op.execute("CREATE INDEX idx_learner_plan_state_template ON learner_plan_state (template_id)")
    op.execute("CREATE INDEX idx_lesson_cache_updated ON lesson_cache (updated_at DESC)")
    op.execute("CREATE INDEX idx_start_message_cache_updated ON start_message_cache (updated_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_start_message_cache_updated")
    op.execute("DROP INDEX IF EXISTS idx_lesson_cache_updated")
    op.execute("DROP INDEX IF EXISTS idx_learner_plan_state_template")
    op.execute("DROP INDEX IF EXISTS idx_plan_templates_active")
    op.execute("DROP INDEX IF EXISTS idx_topic_evidence_learner_section_created")
    op.execute("DROP INDEX IF EXISTS idx_mastery_snapshots_learner_updated")
    op.execute("DROP INDEX IF EXISTS idx_learner_profiles_updated")
    op.execute("DROP INDEX IF EXISTS idx_topic_progress_learner_updated")
    op.execute("DROP INDEX IF EXISTS idx_interaction_assessments_learner_created")
    op.execute("DROP INDEX IF EXISTS idx_interactions_learner_created")

    op.drop_table("start_message_cache")
    op.drop_table("lesson_cache")
    op.drop_table("topic_evidence")
    op.drop_table("mastery_snapshots")
    op.drop_table("learner_profiles")
    op.drop_table("learner_plan_state")
    op.drop_table("plan_templates")
    op.drop_table("topic_progress")
    op.drop_table("interaction_assessments")
    op.drop_table("interaction_sources")
    op.drop_table("interactions")
    op.drop_table("sessions")
    op.drop_table("learners")
