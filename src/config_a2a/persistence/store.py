"""TaskStore implementation backed by SQLAlchemy through TaskRepository."""

from __future__ import annotations

from typing import Any

from config_a2a.a2a.envelope import Message, TaskStatus
from config_a2a.persistence.models import TaskRow
from config_a2a.persistence.repository import TaskRepository


class _TaskView:
    """Lightweight view object exposing the same shape as the legacy in-memory record."""

    def __init__(self, row: TaskRow, history: list[Message]) -> None:
        self.id: str = row.id
        self.context_id: str = row.context_id
        self.state: str = row.state
        self.status: TaskStatus = TaskStatus.model_validate(row.status_payload)
        self.history: list[Message] = history
        self.artifacts: list[dict[str, Any]] = row.artifacts or []
        self.metadata: dict[str, Any] = row.extra or {}
        self.pending_action: dict[str, Any] | None = row.pending_action


class PersistentTaskStore:
    """Drop-in replacement for the in-memory TaskStore (Iter 1 / 2)."""

    def __init__(self, repository: TaskRepository) -> None:
        self._repo = repository

    async def create(self, context_id: str | None = None) -> _TaskView:
        row = await self._repo.create_task(context_id=context_id)
        return _TaskView(row, history=[])

    async def get(self, task_id: str) -> _TaskView | None:
        row = await self._repo.get_task(task_id)
        if row is None:
            return None
        messages = [_message_from_row(m) for m in await self._repo.list_messages(task_id)]
        return _TaskView(row, history=messages)

    async def history_for_context(self, context_id: str) -> list[Message]:
        """Prior conversation turns for a context (across tasks), oldest first."""
        return [_message_from_row(m) for m in await self._repo.list_messages_by_context(context_id)]

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        pending_action: dict[str, Any] | None = None,
        clear_pending: bool = False,
    ) -> None:
        await self._repo.update_status(
            task_id,
            state=status.state,
            status_payload=status.model_dump(),
            pending_action=pending_action,
            clear_pending=clear_pending,
        )

    async def append_artifact(self, task_id: str, artifact: dict[str, Any]) -> None:
        await self._repo.append_artifact(task_id, artifact)

    async def append_message(self, task_id: str, message: Message) -> None:
        await self._repo.append_message(
            task_id=task_id,
            role=message.role,
            parts=[part.model_dump() for part in message.parts],
            extra=message.metadata,
        )

    async def list_recent(self, limit: int = 100) -> list[_TaskView]:
        rows = await self._repo.list_recent_tasks(limit=limit)
        return [_TaskView(row, history=[]) for row in rows]

    async def record_step(self, *, task_id: str, kind: str, payload: dict[str, Any], summary: str = "") -> None:
        await self._repo.record_step(task_id=task_id, kind=kind, payload=payload, summary=summary)


def _message_from_row(row: Any) -> Message:
    return Message.model_validate(
        {
            "messageId": row.id,
            "role": row.role,
            "parts": row.parts,
            "metadata": row.extra or {},
        }
    )
