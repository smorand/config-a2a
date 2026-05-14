"""Multi-agent server: agent_slug discriminator on tasks and memory_records.

The previous (single-agent) schema kept only ``agent_name``; multi-agent makes
``agent_slug`` the canonical filter. We add it, backfill from ``agent_name``
(SQLite-safe), and index it.

Revision ID: 0003_multi_agent
Revises: 0002_memory
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_multi_agent"
down_revision = "0002_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(sa.Column("agent_slug", sa.String(length=128), nullable=True))
    op.execute("UPDATE tasks SET agent_slug = agent_name WHERE agent_slug IS NULL")
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("agent_slug", existing_type=sa.String(length=128), nullable=False)
        batch.create_index("ix_tasks_agent_slug", ["agent_slug"])

    with op.batch_alter_table("memory_records") as batch:
        batch.add_column(sa.Column("agent_slug", sa.String(length=128), nullable=True))
    op.execute("UPDATE memory_records SET agent_slug = agent_name WHERE agent_slug IS NULL")
    with op.batch_alter_table("memory_records") as batch:
        batch.alter_column("agent_slug", existing_type=sa.String(length=128), nullable=False)
        batch.create_index("ix_memory_records_agent_slug", ["agent_slug"])


def downgrade() -> None:
    with op.batch_alter_table("memory_records") as batch:
        batch.drop_index("ix_memory_records_agent_slug")
        batch.drop_column("agent_slug")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_agent_slug")
        batch.drop_column("agent_slug")
