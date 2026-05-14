"""Runtime wiring: composes config + provider + pattern + task store + emitter."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from config_a2a.a2a.envelope import Message, Task, TaskStatus, text_message
from config_a2a.a2a.sse import SseEmitter
from config_a2a.config.models import AgentConfig
from config_a2a.config.prompts import resolve_system_prompt
from config_a2a.mcp.client import McpRegistry
from config_a2a.memory import MemoryOrchestrator
from config_a2a.patterns import ExecutionContext, get_runner
from config_a2a.patterns.base import PatternError
from config_a2a.providers.base import LlmProvider
from config_a2a.providers.registry import build_provider


@dataclass
class TaskRecord:
    id: str
    context_id: str
    status: TaskStatus
    history: list[Message] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    pending_action: dict[str, Any] | None = None


class TaskStore(Protocol):  # pragma: no cover — structural
    async def create(self, context_id: str | None = None) -> Any: ...
    async def get(self, task_id: str) -> Any: ...
    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        pending_action: dict[str, Any] | None = ...,
        clear_pending: bool = ...,
    ) -> None: ...
    async def append_message(self, task_id: str, message: Message) -> None: ...
    async def list_recent(self, limit: int = ...) -> list[Any]: ...
    async def record_step(self, *, task_id: str, kind: str, payload: dict[str, Any], summary: str = ...) -> None: ...


class InMemoryTaskStore:
    """Default, ephemeral store used when persistence is not wired in."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, context_id: str | None = None) -> TaskRecord:
        async with self._lock:
            task_id = str(uuid.uuid4())
            record = TaskRecord(
                id=task_id,
                context_id=context_id or str(uuid.uuid4()),
                status=TaskStatus(state="TASK_STATE_SUBMITTED"),
            )
            self._tasks[task_id] = record
            return record

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        pending_action: dict[str, Any] | None = None,
        clear_pending: bool = False,
    ) -> None:
        async with self._lock:
            record = self._tasks.get(task_id)
            if record is None:
                return
            record.status = status
            if pending_action is not None:
                record.pending_action = pending_action
            if clear_pending:
                record.pending_action = None

    async def append_message(self, task_id: str, message: Message) -> None:
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].history.append(message)

    async def list_recent(self, limit: int = 100) -> list[TaskRecord]:
        async with self._lock:
            return list(self._tasks.values())[-limit:][::-1]

    async def record_step(
        self, *, task_id: str, kind: str, payload: dict[str, Any], summary: str = ""
    ) -> None:  # pragma: no cover — in-memory has no run-step table
        return None


class AgentRuntime:
    """Holds long-lived state for one agent process."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        provider: LlmProvider | None = None,
        tasks: TaskStore | None = None,
        mcp_registry: McpRegistry | None = None,
        memory: MemoryOrchestrator | None = None,
    ) -> None:
        self.config = config
        self.tasks: TaskStore = tasks or InMemoryTaskStore()
        self.provider: LlmProvider | None = provider
        self.mcp = mcp_registry or McpRegistry()
        self.memory: MemoryOrchestrator = memory or MemoryOrchestrator(config, store=None)
        self._system_prompt = resolve_system_prompt(config.prompts.system, config.prompts.system_file, default="")

    async def discover_tools(self) -> None:
        """Run once at process start to populate the MCP registry."""
        if self.config.tools.mcp_servers:
            await self.mcp.discover(self.config.tools.mcp_servers, self.config.tools.filters)

    def get_provider(self) -> LlmProvider:
        if self.provider is None:
            self.provider = build_provider(self.config.model)
        return self.provider

    async def aclose(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()

    async def _harvest_if_complete(self, task_id: str, user_text: str) -> None:
        if not self.memory.enabled:
            return
        record = await self.tasks.get(task_id)
        if record is None:
            return
        status = getattr(record, "status", None)
        if not status or status.state != "TASK_STATE_COMPLETED":
            return
        msg = status.message
        assistant_text = ""
        if msg and getattr(msg, "parts", None):
            for part in msg.parts:
                text = getattr(part, "text", None)
                if text:
                    assistant_text += text
        try:
            await self.memory.harvest(
                user_text=user_text,
                assistant_text=assistant_text,
                provider=self.get_provider(),
            )
        except Exception as exc:  # pylint: disable=broad-except
            import logging

            logging.getLogger(__name__).warning("memory: harvest failed: %s", exc)

    async def run_message(
        self,
        user_text: str,
        emitter: SseEmitter,
        task: Any,
        *,
        skill_id: str | None = None,
    ) -> None:
        # ``skill_id`` is piped through for layer-2 (per-skill overrides). The runtime
        # currently ignores it; validation happens at the A2A boundary.
        del skill_id
        from config_a2a.observability.otel import gen_ai_attributes, get_tracer

        tracer = get_tracer("config-a2a.runtime")
        await emitter.emit(
            {"task": Task(id=task.id, contextId=task.context_id, status=task.status).model_dump()},
            event="task",
        )
        runner = get_runner(self.config.pattern.type)

        # Pre-pattern hook: inject relevant long-term memory into the system prompt.
        system_prompt = self._system_prompt
        if self.memory.enabled and self.config.memory.long_term.read.when != "none":
            injected = await self.memory.inject_long_term(user_text)
            if injected:
                system_prompt = f"{system_prompt}\n\n{injected}" if system_prompt else injected

        ctx = ExecutionContext(
            config=self.config,
            user_text=user_text,
            task_id=task.id,
            context_id=task.context_id,
            emitter=emitter,
            provider=self.get_provider(),
            task_store=self.tasks,
            system_prompt=system_prompt,
            tools=list(self.mcp.specs),
            mcp=self.mcp,
            memory=self.memory,
        )
        try:
            with tracer.start_as_current_span(
                f"pattern.{self.config.pattern.type}",
                attributes={
                    **gen_ai_attributes(
                        provider=self.config.model.provider,
                        model=self.config.model.model,
                    ),
                    "agent.slug": self.config.slug or "",
                    "agent.name": self.config.name,
                    "agent.version": self.config.version,
                    "a2a.task_id": task.id,
                    "a2a.context_id": task.context_id,
                },
            ):
                await runner(ctx)
            # Post-pattern hook: harvest facts on a terminal-success state.
            await self._harvest_if_complete(task.id, user_text)
        except PatternError as exc:
            failed = TaskStatus(state="TASK_STATE_FAILED", message=text_message("ROLE_AGENT", f"pattern error: {exc}"))
            await self.tasks.update_status(task.id, failed)
            await emitter.emit(
                {
                    "statusUpdate": {
                        "taskId": task.id,
                        "contextId": task.context_id,
                        "status": failed.model_dump(),
                        "final": True,
                    }
                },
                event="statusUpdate",
            )
        finally:
            await emitter.close()
