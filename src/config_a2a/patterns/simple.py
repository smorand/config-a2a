"""Simple pattern: one user → assistant message, with a guarded tool-use loop."""

from __future__ import annotations

import json
import logging

from config_a2a.a2a.envelope import TaskStatus, text_message
from config_a2a.guardrails.confirmations import (
    confirm_metadata,
    confirm_prompt,
    is_approval,
    policy_for,
)
from config_a2a.patterns.base import (
    ExecutionContext,
    PatternError,
    call_llm,
    emit_status,
    emit_thinking,
)
from config_a2a.providers.base import ChatMessage, ToolCall

log = logging.getLogger(__name__)


async def run_simple(ctx: ExecutionContext) -> None:
    """``pattern.type == 'simple'``: loop while the model emits tool_calls."""
    messages: list[ChatMessage] = []
    if ctx.system_prompt:
        messages.append(ChatMessage(role="system", content=ctx.system_prompt))
    messages.extend(ctx.history)
    messages.append(ChatMessage(role="user", content=ctx.user_text))

    # Resume from a pending confirmation, if any.
    existing = await ctx.task_store.get(ctx.task_id)
    pending = getattr(existing, "pending_action", None) if existing else None
    if pending and pending.get("kind") == "confirm_tool":
        await emit_status(ctx, "TASK_STATE_WORKING")
        if is_approval(ctx.user_text):
            tool_result = await _dispatch_tool(
                ctx,
                ToolCall(
                    id=pending.get("tool_call_id", "resume"),
                    name=pending["tool_name"],
                    arguments=pending.get("arguments", {}),
                ),
            )
            messages.append(
                ChatMessage(
                    role="tool",
                    content=tool_result,
                    name=pending["tool_name"],
                    tool_call_id=pending.get("tool_call_id", "resume"),
                )
            )
            await ctx.task_store.update_status(ctx.task_id, TaskStatus(state="TASK_STATE_WORKING"), clear_pending=True)
        else:
            await emit_status(ctx, "TASK_STATE_COMPLETED", text="Cancelled at user request.", final=True)
            return

    await emit_status(ctx, "TASK_STATE_WORKING")
    max_loops = ctx.config.guardrails.max_loops
    max_tokens = ctx.config.guardrails.max_tokens
    total_tokens = 0
    final_text = ""

    for _ in range(max_loops):
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        response = await call_llm(ctx, messages, tools=ctx.tools)
        total_tokens += response.usage.input_tokens + response.usage.output_tokens
        if total_tokens > max_tokens:
            raise PatternError(f"max_tokens exceeded ({total_tokens} > {max_tokens})")

        if not response.tool_calls:
            final_text = response.content
            break

        messages.append(ChatMessage(role="assistant", content=response.content or "", tool_calls=response.tool_calls))

        suspended = False
        for tool_call in response.tool_calls:
            handle = ctx.mcp.handles.get(tool_call.name) if ctx.mcp else None
            destructive = bool(handle and handle.descriptor.annotations.get("destructiveHint"))
            policy = policy_for(ctx.config.confirmations, tool_call.name) if destructive else "auto_approve"
            if destructive and policy == "auto_deny":
                tool_text = f"Tool '{tool_call.name}' denied by policy."
            elif destructive and policy == "prompt":
                await _suspend_for_confirmation(ctx, tool_call)
                suspended = True
                break
            else:
                tool_text = await _dispatch_tool(ctx, tool_call)
                await emit_thinking(ctx, f"Tool {tool_call.name} → {tool_text[:200]}")
            messages.append(
                ChatMessage(
                    role="tool",
                    content=tool_text,
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                )
            )
        if suspended:
            return  # Resume happens when the user re-sends with the same taskId.
    else:
        raise PatternError(f"max_loops exceeded ({max_loops})")

    await emit_status(
        ctx,
        "TASK_STATE_COMPLETED",
        text=final_text or "(empty response)",
        final=True,
    )


async def _dispatch_tool(ctx: ExecutionContext, tool_call: ToolCall) -> str:
    if ctx.mcp is None:
        return f"(no MCP registry: tool '{tool_call.name}' not available)"
    result = await ctx.mcp.call(tool_call.name, tool_call.arguments)
    if result.get("isError"):
        return f"[tool error] {result.get('text', '')}"
    return result.get("text") or json.dumps(result)


async def _suspend_for_confirmation(ctx: ExecutionContext, tool_call: ToolCall) -> None:
    metadata = confirm_metadata(tool_call.name, tool_call.id, tool_call.arguments)
    prompt = confirm_prompt(tool_call.name, tool_call.arguments)
    await emit_status(
        ctx,
        "TASK_STATE_INPUT_REQUIRED",
        text=prompt,
        final=False,
        metadata=metadata,
    )
    await ctx.task_store.update_status(
        ctx.task_id,
        TaskStatus(state="TASK_STATE_INPUT_REQUIRED", message=text_message("ROLE_AGENT", prompt)),
        pending_action=metadata,
    )
