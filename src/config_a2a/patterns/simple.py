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

# Recovery for a model that finishes a turn with no tool call and no text.
_MAX_EMPTY_RETRIES = 2
_EMPTY_NUDGE = (
    "Your previous reply was empty. Using the tool results above, answer the "
    "user's request now in plain text."
)
_EMPTY_FALLBACK = (
    "I ran the requested tools but the model returned no text answer. "
    "The tool results are shown above; please rephrase or try again."
)


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
    empty_retries = 0

    for _ in range(max_loops):
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        response = await call_llm(ctx, messages, tools=ctx.tools)
        total_tokens += response.usage.input_tokens + response.usage.output_tokens
        if total_tokens > max_tokens:
            raise PatternError(f"max_tokens exceeded ({total_tokens} > {max_tokens})")

        if not response.tool_calls:
            if response.content.strip():
                final_text = response.content
                break
            # Empty final turn: some models (e.g. Claude behind an OpenAI-compat
            # shim) return no text after a tool result. Nudge for a concrete
            # answer a bounded number of times before surfacing a clear fallback,
            # so the user never sees a silently blank reply.
            if empty_retries < _MAX_EMPTY_RETRIES:
                empty_retries += 1
                await emit_thinking(ctx, "(model returned an empty reply; asking it to answer)")
                messages.append(ChatMessage(role="user", content=_EMPTY_NUDGE))
                continue
            final_text = _EMPTY_FALLBACK
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
