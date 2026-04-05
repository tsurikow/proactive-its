"""add section-scoped indexes for topic_progress and mastery_snapshots

Revision ID: 20260404_0002
Revises: 20260404_0001
Create Date: 2026-04-04 00:00:00
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20260404_0002"
down_revision: Union[str, None] = "20260404_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_topic_progress_section_status", "topic_progress", ["section_id", "status"])
    op.create_index("idx_mastery_snapshots_section", "mastery_snapshots", ["section_id"])


def downgrade() -> None:
    op.drop_index("idx_mastery_snapshots_section", table_name="mastery_snapshots")
    op.drop_index("idx_topic_progress_section_status", table_name="topic_progress")
