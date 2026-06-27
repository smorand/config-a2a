"""Shared destructive-tool confirmation logic for tool-using patterns.

Both ``simple`` and ``react`` must consult ``confirmations.policy_for`` before
running a destructive tool:

* ``auto_approve`` runs it immediately,
* ``auto_deny`` refuses without running,
* ``prompt`` suspends the task with ``TASK_STATE_INPUT_REQUIRED`` and persists
  the pending call.

A suspended task resumes on the next message with the same ``taskId``: an
approval re-executes the persisted call; anything else cancels. Keeping this in
one place stops the two patterns from drifting apart.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

from config_a2a.a2a.envelope import TaskStatus, text_message
from config_a2a.guardrails.confirmations import (
    confirm_metadata,
    confirm_prompt,
    is_approval,
    policy_for,
)
from config_a2a.patterns.base import ExecutionContext, emit_status
from config_a2a.providers.base import ChatMessage, ToolCall

log = logging.getLogger(__name__)


async def dispatch_tool(ctx: ExecutionContext, tool_call: ToolCall) -> str:
    """Run a tool through the MCP registry, flattening the result to text."""
    if ctx.mcp is None:
        return f"(no MCP registry: tool '{tool_call.name}' not available)"
    result = await ctx.mcp.call(tool_call.name, tool_call.arguments)
    if result.get("isError"):
        return f"[tool error] {result.get('text', '')}"
    return result.get("text") or json.dumps(result)


async def suspend_for_confirmation(ctx: ExecutionContext, tool_call: ToolCall) -> None:
    """Emit ``INPUT_REQUIRED`` and persist the pending call for later resume."""
    metadata = confirm_metadata(tool_call.name, tool_call.id, tool_call.arguments)
    prompt = confirm_prompt(tool_call.name, tool_call.arguments)
    await emit_status(ctx, "TASK_STATE_INPUT_REQUIRED", text=prompt, final=False, metadata=metadata)
    await ctx.task_store.update_status(
        ctx.task_id,
        TaskStatus(state="TASK_STATE_INPUT_REQUIRED", message=text_message("ROLE_AGENT", prompt)),
        pending_action=metadata,
    )


@dataclass
class ToolDecision:
    """Outcome of evaluating one tool call against the confirmation policy."""

    suspended: bool
    text: str | None  # tool-message content when not suspended


async def decide_tool(ctx: ExecutionContext, tool_call: ToolCall) -> ToolDecision:
    """Apply the confirmation policy, then run, deny, or suspend the call.

    Honours ``confirmations.destructive_hint`` and ``confirmations.per_tool``
    via :func:`policy_for`. Only tools annotated ``destructiveHint`` are gated;
    everything else runs immediately.
    """
    handle = ctx.mcp.handles.get(tool_call.name) if ctx.mcp else None
    destructive = bool(handle and handle.descriptor.annotations.get("destructiveHint"))
    policy = policy_for(ctx.config.confirmations, tool_call.name) if destructive else "auto_approve"
    if destructive and policy == "auto_deny":
        return ToolDecision(suspended=False, text=f"Tool '{tool_call.name}' denied by policy.")
    if destructive and policy == "prompt":
        await suspend_for_confirmation(ctx, tool_call)
        return ToolDecision(suspended=True, text=None)
    return ToolDecision(suspended=False, text=await dispatch_tool(ctx, tool_call))


ResumeOutcome = Literal["none", "resumed", "cancelled"]


async def resume_pending(ctx: ExecutionContext, messages: list[ChatMessage]) -> ResumeOutcome:
    """Resume a task suspended on a destructive-tool confirmation.

    On approval, re-execute the persisted pending call, append its result to
    ``messages``, clear the pending state, and return ``"resumed"`` so the caller
    can continue its loop. Otherwise emit a terminal cancellation and return
    ``"cancelled"``. Returns ``"none"`` when nothing is pending.
    """
    existing = await ctx.task_store.get(ctx.task_id)
    pending = getattr(existing, "pending_action", None) if existing else None
    if not pending or pending.get("kind") != "confirm_tool":
        return "none"
    await emit_status(ctx, "TASK_STATE_WORKING")
    if not is_approval(ctx.user_text):
        await emit_status(ctx, "TASK_STATE_COMPLETED", text="Cancelled at user request.", final=True)
        return "cancelled"
    tool_call_id = pending.get("tool_call_id", "resume")
    tool_name = pending["tool_name"]
    tool_result = await dispatch_tool(
        ctx,
        ToolCall(id=tool_call_id, name=tool_name, arguments=pending.get("arguments", {})),
    )
    messages.append(ChatMessage(role="tool", content=tool_result, name=tool_name, tool_call_id=tool_call_id))
    await ctx.task_store.update_status(ctx.task_id, TaskStatus(state="TASK_STATE_WORKING"), clear_pending=True)
    return "resumed"


__all__ = [
    "ResumeOutcome",
    "ToolDecision",
    "decide_tool",
    "dispatch_tool",
    "resume_pending",
    "suspend_for_confirmation",
]
