"""Plan & Execute pattern: planner LLM produces a JSON plan, executor runs steps."""

from __future__ import annotations

import json
import logging
from typing import Any

from config_a2a.config.models import PlanExecutePattern
from config_a2a.config.prompts import resolve_prompt
from config_a2a.patterns.base import (
    ExecutionContext,
    PatternError,
    call_llm,
    emit_status,
    emit_thinking,
)
from config_a2a.providers.base import ChatMessage

log = logging.getLogger(__name__)

_PLANNER_SUFFIX = (
    "\n\nReturn ONLY a JSON object with this exact shape:\n"
    '{"steps":[{"id":"s1","instruction":"...","done":false},'
    '{"id":"s2","instruction":"...","done":false}]}'
    "\n- Use 3-6 steps. Keep each instruction one sentence."
    "\n- Do not wrap the JSON in code fences."
)

_EXECUTOR_SUFFIX = (
    "\n\nFor the given step, produce a short result (1-3 sentences). "
    "If a tool would help, call it; otherwise reason directly."
)

_SYNTH_INSTRUCTIONS = (
    "Summarise the step results into a single final answer for the user. "
    "Be concise and reference numbers/values that actually appeared."
)


async def run_plan_execute(ctx: ExecutionContext) -> None:
    pattern = ctx.config.pattern
    assert isinstance(pattern, PlanExecutePattern)
    planner_prompt = resolve_prompt(pattern.planner, default="You are a careful planner.")
    executor_prompt = resolve_prompt(pattern.executor, default="You are a careful executor.")

    await emit_status(ctx, "TASK_STATE_WORKING")

    # ---- Plan ----------------------------------------------------------
    plan_messages: list[ChatMessage] = [
        ChatMessage(role="system", content=planner_prompt + _PLANNER_SUFFIX),
        ChatMessage(role="user", content=ctx.user_text),
    ]
    plan = await _request_plan(ctx, plan_messages, pattern.max_replans)
    await emit_thinking(ctx, f"plan: {len(plan)} step(s)")

    # ---- Execute -------------------------------------------------------
    step_results: list[dict[str, Any]] = []
    for step in plan[: pattern.max_steps]:
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        exec_messages = [
            ChatMessage(role="system", content=executor_prompt + _EXECUTOR_SUFFIX),
            ChatMessage(role="user", content=f"Task: {ctx.user_text}\nStep: {step['instruction']}"),
        ]
        response = await call_llm(ctx, exec_messages, tools=ctx.tools)
        step_results.append({"id": step["id"], "instruction": step["instruction"], "result": response.content})
        await emit_thinking(ctx, f"step {step['id']}: {response.content[:160]}")

    # ---- Synthesis -----------------------------------------------------
    synth_messages = [
        ChatMessage(role="system", content=_SYNTH_INSTRUCTIONS),
        ChatMessage(
            role="user",
            content=(
                f"Original request: {ctx.user_text}\n\nStep results:\n"
                + "\n".join(f"- {item['id']}: {item['result']}" for item in step_results)
            ),
        ),
    ]
    final = await call_llm(ctx, synth_messages)
    await emit_status(
        ctx,
        "TASK_STATE_COMPLETED",
        text=final.content or "(empty response)",
        final=True,
    )


async def _request_plan(
    ctx: ExecutionContext, messages: list[ChatMessage], max_replans: int
) -> list[dict[str, Any]]:
    last_error: str = ""
    for attempt in range(max_replans + 1):
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        response = await call_llm(ctx, messages)
        try:
            data = json.loads(response.content.strip())
            steps = data["steps"]
            if not isinstance(steps, list) or not steps:
                raise ValueError("plan must contain at least one step")
            for step in steps:
                if "id" not in step or "instruction" not in step:
                    raise ValueError("each step needs `id` and `instruction`")
            return steps
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            last_error = str(exc)
            if attempt == max_replans:
                break
            messages.append(ChatMessage(role="assistant", content=response.content))
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"Your previous output was not valid JSON: {exc}. Please return only the JSON object.",
                )
            )
    raise PatternError(f"plan validation failed after {max_replans + 1} attempts: {last_error}")
