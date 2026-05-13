"""Memory records table.

Revision ID: 0002_memory
Revises: 0001_initial
Create Date: 2026-05-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_memory"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_records",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("agent_name", sa.String(length=128), nullable=False, index=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=True, index=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("memory_records")
