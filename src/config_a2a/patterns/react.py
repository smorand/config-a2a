"""ReAct pattern: explicit Thought/Action/Observation loop with anti-loop check."""

from __future__ import annotations

import logging

from config_a2a.config.models import ReactPattern
from config_a2a.config.prompts import resolve_prompt
from config_a2a.guardrails.anti_loop import is_loop
from config_a2a.patterns.base import (
    ExecutionContext,
    PatternError,
    call_llm,
    emit_status,
    emit_thinking,
)
from config_a2a.providers.base import ChatMessage

log = logging.getLogger(__name__)

_DEFAULT_REACT_INSTRUCTIONS = (
    "Follow the ReAct loop. For each step, think briefly, then either call a "
    "tool to gather information or produce a final answer when confident. "
    "Avoid repeating the same tool call with the same arguments."
)


async def run_react(ctx: ExecutionContext) -> None:
    pattern = ctx.config.pattern
    assert isinstance(pattern, ReactPattern)
    react_prompt = resolve_prompt(
        type("p", (), {"prompt": pattern.executor_prompt, "prompt_file": pattern.executor_prompt_file})(),
        default=_DEFAULT_REACT_INSTRUCTIONS,
    )
    system = ctx.system_prompt
    composed_system = f"{system}\n\n{react_prompt}".strip() if system else react_prompt

    messages: list[ChatMessage] = [ChatMessage(role="system", content=composed_system)]
    messages.append(ChatMessage(role="user", content=ctx.user_text))

    await emit_status(ctx, "TASK_STATE_WORKING")

    max_iter = pattern.max_iterations
    max_tokens = ctx.config.guardrails.max_tokens
    total_tokens = 0
    seen_assistant: list[str] = []
    final_text = ""

    for iteration in range(max_iter):
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        response = await call_llm(ctx, messages, tools=ctx.tools)
        total_tokens += response.usage.input_tokens + response.usage.output_tokens
        if total_tokens > max_tokens:
            raise PatternError(f"max_tokens exceeded ({total_tokens} > {max_tokens})")

        if not response.tool_calls:
            final_text = response.content
            if final_text and is_loop(seen_assistant, final_text):
                # Force a synthesis pass: ask for a different final answer.
                messages.append(ChatMessage(role="assistant", content=final_text))
                messages.append(
                    ChatMessage(
                        role="user",
                        content="That answer repeats earlier reasoning. Provide a concise final answer using only what you know.",
                    )
                )
                seen_assistant.append(final_text)
                continue
            break

        await emit_thinking(ctx, f"(iteration {iteration + 1}) calling {response.tool_calls[0].name}")
        messages.append(
            ChatMessage(role="assistant", content=response.content or "", tool_calls=response.tool_calls)
        )
        seen_assistant.append(response.content or "")
        for tool_call in response.tool_calls:
            handle = ctx.mcp.handles.get(tool_call.name) if ctx.mcp else None
            destructive = bool(handle and handle.descriptor.annotations.get("destructiveHint"))
            if destructive:
                from config_a2a.patterns.simple import _suspend_for_confirmation  # local import

                await _suspend_for_confirmation(ctx, tool_call)
                return
            tool_text = "(no tools wired)" if not handle else (
                (await ctx.mcp.call(tool_call.name, tool_call.arguments)).get("text") or ""
            )
            messages.append(
                ChatMessage(
                    role="tool",
                    content=tool_text,
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                )
            )
    else:
        raise PatternError(f"max_iterations exceeded ({max_iter})")

    await emit_status(ctx, "TASK_STATE_COMPLETED", text=final_text or "(empty response)", final=True)
