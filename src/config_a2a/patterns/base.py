"""Pattern execution primitives shared by every agent strategy."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config_a2a.a2a.envelope import TaskStatus, text_message
from config_a2a.a2a.sse import SseEmitter
from config_a2a.config.models import AgentConfig
from config_a2a.providers.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LlmProvider,
    ToolSpec,
)

if TYPE_CHECKING:  # pragma: no cover
    from config_a2a.runtime import TaskStore


@dataclass(frozen=True)
class ExecutionContext:
    """Immutable per-message execution scope handed to a pattern."""

    config: AgentConfig
    user_text: str
    task_id: str
    context_id: str
    emitter: SseEmitter
    provider: LlmProvider
    task_store: "TaskStore"
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    depth: int = 0
    system_prompt: str = ""
    history: list[ChatMessage] = field(default_factory=list)
    tools: list[ToolSpec] = field(default_factory=list)


class PatternError(Exception):
    """Raised when a pattern hits a guardrail or unrecoverable failure."""


async def emit_status(
    ctx: ExecutionContext, state: str, *, text: str | None = None, final: bool = False, metadata: dict | None = None
) -> None:
    """Helper to push a TaskState update over SSE."""
    message = text_message("ROLE_AGENT", text) if text is not None else None
    status = TaskStatus(state=state, message=message)
    await ctx.task_store.update_status(ctx.task_id, status)
    if message is not None:
        await ctx.task_store.append_message(ctx.task_id, message)
    payload = {
        "statusUpdate": {
            "taskId": ctx.task_id,
            "contextId": ctx.context_id,
            "status": status.model_dump(),
            "final": final,
        }
    }
    if metadata:
        payload["statusUpdate"]["metadata"] = metadata
    await ctx.emitter.emit(payload, event="statusUpdate")


async def emit_thinking(ctx: ExecutionContext, text: str) -> None:
    await ctx.emitter.emit(
        {
            "statusUpdate": {
                "taskId": ctx.task_id,
                "contextId": ctx.context_id,
                "status": {
                    "state": "TASK_STATE_WORKING",
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "ROLE_AGENT",
                        "parts": [{"text": text}],
                        "metadata": {"kind": "thought"},
                    },
                },
                "final": False,
            }
        },
        event="statusUpdate",
    )


async def call_llm(ctx: ExecutionContext, messages: list[ChatMessage], *, tools: list[ToolSpec] | None = None) -> ChatResponse:
    request = ChatRequest(
        messages=messages,
        tools=tools or [],
        temperature=ctx.config.model.temperature,
        max_output_tokens=ctx.config.model.max_output_tokens,
    )
    return await ctx.provider.chat(request)
