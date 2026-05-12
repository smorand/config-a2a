"""Initial schema: tasks, messages, run_steps.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("context_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("agent_name", sa.String(length=128), nullable=False, index=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("status_payload", sa.JSON(), nullable=False),
        sa.Column("pending_action", sa.JSON(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("parts", sa.JSON(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "run_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(length=64), nullable=False, index=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("run_steps")
    op.drop_table("messages")
    op.drop_table("tasks")
