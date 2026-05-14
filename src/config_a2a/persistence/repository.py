"""Async repository wrapping the ORM models for the runtime to consume."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from config_a2a.persistence.models import MessageRow, RunStepRow, TaskRow


class TaskRepository:
    """Async repository for tasks, messages, and run steps.

    Each repository instance is scoped to one ``(agent_slug, agent_name)`` pair;
    every query filters by ``agent_slug``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        agent_slug: str,
        agent_name: str,
    ) -> None:
        self._session_factory = session_factory
        self._agent_slug = agent_slug
        self._agent_name = agent_name

    async def create_task(self, *, context_id: str | None = None) -> TaskRow:
        task_id = str(uuid.uuid4())
        ctx_id = context_id or str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        async with self._session_factory.begin() as session:
            row = TaskRow(
                id=task_id,
                context_id=ctx_id,
                agent_slug=self._agent_slug,
                agent_name=self._agent_name,
                state="TASK_STATE_SUBMITTED",
                status_payload={"state": "TASK_STATE_SUBMITTED"},
                pending_action=None,
                extra={},
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        return row

    async def get_task(self, task_id: str) -> TaskRow | None:
        async with self._session_factory() as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.agent_slug != self._agent_slug:
                return None
            return row

    async def update_status(
        self,
        task_id: str,
        *,
        state: str,
        status_payload: dict[str, Any],
        pending_action: dict[str, Any] | None = None,
        clear_pending: bool = False,
    ) -> None:
        async with self._session_factory.begin() as session:
            row = await session.get(TaskRow, task_id)
            if row is None or row.agent_slug != self._agent_slug:
                return
            row.state = state
            row.status_payload = status_payload
            row.updated_at = datetime.now(timezone.utc)
            if pending_action is not None:
                row.pending_action = pending_action
            if clear_pending:
                row.pending_action = None

    async def append_message(
        self,
        *,
        task_id: str,
        role: str,
        parts: list[dict[str, Any]],
        extra: dict[str, Any] | None = None,
    ) -> MessageRow:
        async with self._session_factory.begin() as session:
            count = await session.scalar(
                select(MessageRow).where(MessageRow.task_id == task_id).order_by(MessageRow.position.desc()).limit(1)
            )
            position = (count.position + 1) if count else 0
            row = MessageRow(
                id=str(uuid.uuid4()),
                task_id=task_id,
                role=role,
                parts=parts,
                extra=extra or {},
                position=position,
            )
            session.add(row)
        return row

    async def list_messages(self, task_id: str) -> list[MessageRow]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(MessageRow).where(MessageRow.task_id == task_id).order_by(MessageRow.position.asc())
            )
            return list(result)

    async def record_step(self, *, task_id: str, kind: str, payload: dict[str, Any], summary: str = "") -> None:
        async with self._session_factory.begin() as session:
            session.add(RunStepRow(task_id=task_id, kind=kind, payload=payload, summary=summary))

    async def list_recent_tasks(self, limit: int = 100) -> list[TaskRow]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(TaskRow)
                .where(TaskRow.agent_slug == self._agent_slug)
                .order_by(TaskRow.created_at.desc())
                .limit(limit)
            )
            return list(result)
