"""Add ``artifacts`` column to tasks.

A2A v1.0 servers deliver their final answer as an Artifact, not only inside
status.message. This adds the storage for it. No backfill/NOT NULL: the
column is nullable and application code treats NULL/missing as an empty
list, so existing rows need no migration-time data change.

Revision ID: 0004_task_artifacts
Revises: 0003_multi_agent
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_task_artifacts"
down_revision = "0003_multi_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("artifacts", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_column("artifacts")
