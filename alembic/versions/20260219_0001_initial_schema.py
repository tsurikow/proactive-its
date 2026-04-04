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
        sa.Column("first_name", sa.String(), nullable=False),
        sa.Column("last_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("timezone", sa.String(), server_default=sa.text("'UTC'"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "learner_auth_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("session_token_hash", sa.String(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_token_hash"),
    )

    op.create_table(
        "learner_auth_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("token_kind", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
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
        "teacher_turns",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("request_key", sa.String(), nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("section_id", sa.String(), nullable=True),
        sa.Column("state", sa.String(), server_default=sa.text("'accepted'"), nullable=False),
        sa.Column("request_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("final_interaction_id", sa.Integer(), nullable=True),
        sa.Column("final_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("degraded_execution", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fallback_reason", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["final_interaction_id"], ["interactions.id"]),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_key"),
    )

    op.create_table(
        "teacher_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("job_kind", sa.String(), nullable=False),
        sa.Column("state", sa.String(), server_default=sa.text("'accepted'"), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("broker_message_id", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("degraded_execution", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("fallback_reason", sa.String(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["turn_id"], ["teacher_turns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("turn_id"),
    )

    op.create_table(
        "teacher_job_results",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("result_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("worker_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["teacher_jobs.id"]),
        sa.ForeignKeyConstraint(["turn_id"], ["teacher_turns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id"),
        sa.UniqueConstraint("turn_id"),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("aggregate_type", sa.String(), nullable=False),
        sa.Column("aggregate_id", sa.String(), nullable=False),
        sa.Column("event_kind", sa.String(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("publish_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("broker_message_id", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
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
        sa.Column("artifact_key", sa.String(), nullable=False),
        sa.Column("context_version", sa.String(), nullable=False),
        sa.Column("lesson_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
        sa.PrimaryKeyConstraint("template_id", "stage_index", "artifact_key", "context_version"),
    )

    op.create_table(
        "teacher_artifacts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("stage_index", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.String(), nullable=False),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("decision_kind", sa.String(), nullable=False),
        sa.Column("artifact_key", sa.String(), nullable=False),
        sa.Column("stage_signal", sa.String(), nullable=False),
        sa.Column("decision_source", sa.String(), nullable=False),
        sa.Column("context_version", sa.String(), nullable=False),
        sa.Column("effective_mastery_score", sa.Float(), nullable=True),
        sa.Column("weak_topic_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("module_evidence_coverage", sa.Float(), nullable=True),
        sa.Column("fallback_reason", sa.String(), nullable=True),
        sa.Column("decision_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "teacher_session_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("interaction_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("proposal_type", sa.String(), nullable=True),
        sa.Column("stage_index", sa.Integer(), nullable=True),
        sa.Column("section_id", sa.String(), nullable=True),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("event_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"]),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "learning_debt",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("learner_id", sa.String(), nullable=False),
        sa.Column("template_id", sa.String(), nullable=False),
        sa.Column("section_id", sa.String(), nullable=False),
        sa.Column("module_id", sa.String(), nullable=True),
        sa.Column("debt_kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Integer(), nullable=True),
        sa.Column("source_interaction_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["learner_id"], ["learners.id"]),
        sa.ForeignKeyConstraint(["template_id"], ["plan_templates.id"]),
        sa.ForeignKeyConstraint(["source_event_id"], ["teacher_session_events.id"]),
        sa.ForeignKeyConstraint(["source_interaction_id"], ["interactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "content_index_state",
        sa.Column("state_key", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("source_fingerprint", sa.String(), nullable=False),
        sa.Column("embedding_model", sa.String(), nullable=False),
        sa.Column("chunk_target_tokens", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap_tokens", sa.Integer(), nullable=False),
        sa.Column("documents_path", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("state_key"),
    )

    op.execute("CREATE INDEX idx_interactions_learner_created ON interactions (learner_id, created_at DESC)")
    op.execute("CREATE INDEX idx_learners_email ON learners (email)")
    op.execute(
        "CREATE INDEX idx_learner_auth_sessions_active "
        "ON learner_auth_sessions (learner_id, expires_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_learner_auth_tokens_kind_expiry "
        "ON learner_auth_tokens (learner_id, token_kind, expires_at DESC)"
    )
    op.execute("CREATE INDEX idx_teacher_turns_learner_created ON teacher_turns (learner_id, created_at DESC)")
    op.execute("CREATE INDEX idx_teacher_turns_state_created ON teacher_turns (state, created_at DESC)")
    op.execute("CREATE INDEX idx_teacher_jobs_state_created ON teacher_jobs (state, created_at DESC)")
    op.execute("CREATE INDEX idx_outbox_events_pending ON outbox_events (published_at, created_at DESC)")
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
    op.execute(
        "CREATE INDEX idx_teacher_artifacts_learner_stage_created "
        "ON teacher_artifacts (learner_id, stage_index, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_teacher_session_events_learner_created "
        "ON teacher_session_events (learner_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_learning_debt_learner_status_created "
        "ON learning_debt (learner_id, status, created_at DESC)"
    )
    op.execute("CREATE INDEX idx_content_index_state_updated ON content_index_state (updated_at DESC)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_learner_auth_tokens_kind_expiry")
    op.execute("DROP INDEX IF EXISTS idx_learner_auth_sessions_active")
    op.execute("DROP INDEX IF EXISTS idx_learners_email")
    op.execute("DROP INDEX IF EXISTS idx_lesson_cache_updated")
    op.execute("DROP INDEX IF EXISTS idx_learner_plan_state_template")
    op.execute("DROP INDEX IF EXISTS idx_plan_templates_active")
    op.execute("DROP INDEX IF EXISTS idx_topic_evidence_learner_section_created")
    op.execute("DROP INDEX IF EXISTS idx_mastery_snapshots_learner_updated")
    op.execute("DROP INDEX IF EXISTS idx_learner_profiles_updated")
    op.execute("DROP INDEX IF EXISTS idx_topic_progress_learner_updated")
    op.execute("DROP INDEX IF EXISTS idx_interactions_learner_created")
    op.execute("DROP INDEX IF EXISTS idx_teacher_artifacts_learner_stage_created")
    op.execute("DROP INDEX IF EXISTS idx_teacher_turns_learner_created")
    op.execute("DROP INDEX IF EXISTS idx_teacher_turns_state_created")
    op.execute("DROP INDEX IF EXISTS idx_teacher_jobs_state_created")
    op.execute("DROP INDEX IF EXISTS idx_outbox_events_pending")
    op.execute("DROP INDEX IF EXISTS idx_teacher_session_events_learner_created")
    op.execute("DROP INDEX IF EXISTS idx_learning_debt_learner_status_created")
    op.execute("DROP INDEX IF EXISTS idx_content_index_state_updated")

    op.drop_table("content_index_state")
    op.drop_table("learning_debt")
    op.drop_table("teacher_session_events")
    op.drop_table("teacher_artifacts")
    op.drop_table("outbox_events")
    op.drop_table("teacher_job_results")
    op.drop_table("teacher_jobs")
    op.drop_table("teacher_turns")
    op.drop_table("lesson_cache")
    op.drop_table("topic_evidence")
    op.drop_table("mastery_snapshots")
    op.drop_table("learner_profiles")
    op.drop_table("learner_plan_state")
    op.drop_table("plan_templates")
    op.drop_table("topic_progress")
    op.drop_table("learner_auth_tokens")
    op.drop_table("learner_auth_sessions")
    op.drop_table("interaction_sources")
    op.drop_table("interactions")
    op.drop_table("sessions")
    op.drop_table("learners")
