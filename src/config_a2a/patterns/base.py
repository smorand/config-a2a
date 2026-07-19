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
    from config_a2a.mcp.client import McpRegistry
    from config_a2a.memory import MemoryOrchestrator
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
    mcp: "McpRegistry | None" = None
    memory: "MemoryOrchestrator | None" = None


class PatternError(Exception):
    """Raised when a pattern hits a guardrail or unrecoverable failure."""


async def emit_status(
    ctx: ExecutionContext,
    state: str,
    *,
    text: str | None = None,
    final: bool = False,
    metadata: dict | None = None,
    emit_artifact_on_complete: bool = True,
) -> None:
    """Helper to push a TaskState update over SSE.

    On TASK_STATE_COMPLETED with text, this also emits (and persists) a proper A2A Artifact
    *in addition to* status.message, before the final statusUpdate — matching the delivery
    order (artifactUpdate, then statusUpdate final=true) observed against the official a2a-sdk
    reference server. status.message keeps carrying the text too: config-a2a's own outbound
    client and web-a2a both already read it there, and this keeps them working unchanged while
    a spec-standard peer that only looks at artifacts is now also served correctly.
    """
    if state == "TASK_STATE_COMPLETED" and text and emit_artifact_on_complete:
        artifact = {"artifactId": str(uuid.uuid4()), "parts": [{"text": text}]}
        await ctx.task_store.append_artifact(ctx.task_id, artifact)
        await ctx.emitter.emit(
            {
                "artifactUpdate": {
                    "taskId": ctx.task_id,
                    "contextId": ctx.context_id,
                    "artifact": artifact,
                    "append": False,
                    "lastChunk": True,
                }
            },
            event="artifactUpdate",
        )
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


async def call_llm(
    ctx: ExecutionContext, messages: list[ChatMessage], *, tools: list[ToolSpec] | None = None
) -> ChatResponse:
    # Working-memory hook: enforce the sliding window before the call.
    if ctx.memory is not None:
        messages = await ctx.memory.maybe_summarise(messages, provider=ctx.provider)
    request = ChatRequest(
        messages=messages,
        tools=tools or [],
        temperature=ctx.config.model.temperature,
        max_output_tokens=ctx.config.model.max_output_tokens,
    )
    return await ctx.provider.chat(request)


async def call_llm_with_budget(
    ctx: ExecutionContext,
    messages: list[ChatMessage],
    *,
    total_tokens: int,
    max_tokens: int,
) -> tuple[ChatResponse, int]:
    """``call_llm`` plus the cancel check and running token-budget enforcement shared by every pattern's loop.

    Returns the response and the updated running total; raises ``PatternError``
    on cancellation or once ``total_tokens`` exceeds ``max_tokens``.
    """
    if ctx.cancel_event.is_set():
        raise PatternError("cancelled")
    response = await call_llm(ctx, messages, tools=ctx.tools)
    total_tokens += response.usage.input_tokens + response.usage.output_tokens
    if total_tokens > max_tokens:
        raise PatternError(f"max_tokens exceeded ({total_tokens} > {max_tokens})")
    return response, total_tokens
