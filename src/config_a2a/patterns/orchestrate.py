"""Orchestrate pattern: dispatch to N remote A2A agents (sequential or parallel)."""

from __future__ import annotations

import asyncio
import logging

from config_a2a.a2a.client import send_text
from config_a2a.config.models import OrchestrateAgentRef, OrchestratePattern
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

_AGG_INSTRUCTIONS = (
    "You are given replies from several agents. Synthesise them into a "
    "single answer for the user, attributing disagreements when they matter."
)


async def run_orchestrate(ctx: ExecutionContext) -> None:
    pattern = ctx.config.pattern
    assert isinstance(pattern, OrchestratePattern)
    aggregator_prompt = resolve_prompt(pattern.aggregator, default=_AGG_INSTRUCTIONS)

    await emit_status(ctx, "TASK_STATE_WORKING")
    await emit_thinking(ctx, f"orchestrate: {pattern.mode} fan-out over {len(pattern.agents)} agents")

    if pattern.mode == "parallel":
        results = await asyncio.gather(
            *(_dispatch(agent, ctx) for agent in pattern.agents), return_exceptions=True
        )
    else:
        results = []
        for agent in pattern.agents:
            try:
                results.append(await _dispatch(agent, ctx))
            except Exception as exc:  # pylint: disable=broad-except
                results.append(exc)

    transcript = []
    for agent, outcome in zip(pattern.agents, results, strict=False):
        if isinstance(outcome, Exception):
            transcript.append(f"- {agent.name}: ERROR {outcome}")
        else:
            transcript.append(f"- {agent.name}: {outcome}")

    final_messages = [
        ChatMessage(role="system", content=aggregator_prompt),
        ChatMessage(
            role="user",
            content=f"Original request: {ctx.user_text}\n\nReplies:\n" + "\n".join(transcript),
        ),
    ]
    synth = await call_llm(ctx, final_messages)
    await emit_status(ctx, "TASK_STATE_COMPLETED", text=synth.content or "(empty)", final=True)


async def _dispatch(agent: OrchestrateAgentRef, ctx: ExecutionContext) -> str:
    if ctx.cancel_event.is_set():
        raise PatternError("cancelled")
    rendered = agent.input_template.replace("{{ user_text }}", ctx.user_text)
    result = await send_text(
        agent.a2a_url,
        rendered,
        auth=agent.auth,
        context_id=ctx.context_id,
    )
    if result.state != "TASK_STATE_COMPLETED":
        raise PatternError(f"remote agent '{agent.name}' returned {result.state}")
    return result.text
