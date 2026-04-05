"""add learner_memory table

Revision ID: 20260404_0001
Revises: 20260219_0001
Create Date: 2026-04-04 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260404_0001"
down_revision: Union[str, None] = "20260219_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learner_memory",
        sa.Column("learner_id", sa.String(), sa.ForeignKey("learners.id"), nullable=False),
        sa.Column("template_id", sa.String(), sa.ForeignKey("plan_templates.id"), nullable=False),
        sa.Column("memory_json", postgresql.JSONB(), nullable=False),
        sa.Column("session_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("learner_id", "template_id"),
    )


def downgrade() -> None:
    op.drop_table("learner_memory")
