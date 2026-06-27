"""Simple pattern: one user → assistant message, with a guarded tool-use loop."""

from __future__ import annotations

import logging

from config_a2a.patterns.base import (
    ExecutionContext,
    PatternError,
    call_llm,
    emit_status,
    emit_thinking,
)
from config_a2a.patterns.confirm import decide_tool, resume_pending
from config_a2a.providers.base import ChatMessage

log = logging.getLogger(__name__)


async def run_simple(ctx: ExecutionContext) -> None:
    """``pattern.type == 'simple'``: loop while the model emits tool_calls."""
    messages: list[ChatMessage] = []
    if ctx.system_prompt:
        messages.append(ChatMessage(role="system", content=ctx.system_prompt))
    messages.extend(ctx.history)
    messages.append(ChatMessage(role="user", content=ctx.user_text))

    # Resume from a pending destructive-tool confirmation, if any.
    if await resume_pending(ctx, messages) == "cancelled":
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
            decision = await decide_tool(ctx, tool_call)
            if decision.suspended:
                suspended = True
                break
            tool_text = decision.text or ""
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
