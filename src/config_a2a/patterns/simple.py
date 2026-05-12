"""Simple pattern: one LLM call, with a tool-use loop bounded by guardrails."""

from __future__ import annotations

from config_a2a.patterns.base import (
    ExecutionContext,
    PatternError,
    call_llm,
    emit_status,
    emit_thinking,
)
from config_a2a.providers.base import ChatMessage


async def run_simple(ctx: ExecutionContext) -> None:
    """Implements pattern.type == 'simple'.

    The model is queried once; if it emits ``tool_calls`` we'd dispatch them here,
    but tools are wired in Iter 4. For now we loop only until the assistant
    produces a textual answer or we hit ``max_loops``.
    """
    messages: list[ChatMessage] = []
    if ctx.system_prompt:
        messages.append(ChatMessage(role="system", content=ctx.system_prompt))
    messages.extend(ctx.history)
    messages.append(ChatMessage(role="user", content=ctx.user_text))

    await emit_status(ctx, "TASK_STATE_WORKING")

    max_loops = ctx.config.guardrails.max_loops
    total_tokens = 0
    max_tokens = ctx.config.guardrails.max_tokens
    final_text = ""

    for loop_index in range(max_loops):
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        response = await call_llm(ctx, messages, tools=ctx.tools)
        total_tokens += response.usage.input_tokens + response.usage.output_tokens
        if total_tokens > max_tokens:
            raise PatternError(f"max_tokens exceeded ({total_tokens} > {max_tokens})")

        if response.tool_calls:
            # Tool dispatch arrives with Iter 4; for now surface as a thought
            # and break to avoid an infinite loop on providers that always
            # request tools when none are wired.
            await emit_thinking(ctx, f"(tool call requested: {response.tool_calls[0].name})")
            final_text = response.content or "(tool call requested but no tools wired in this iteration)"
            break

        final_text = response.content
        break  # pylint: disable=useless-suppression
    else:
        raise PatternError(f"max_loops exceeded ({max_loops})")

    await emit_status(
        ctx,
        "TASK_STATE_COMPLETED",
        text=final_text or "(empty response)",
        final=True,
    )
