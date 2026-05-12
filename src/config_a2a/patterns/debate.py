"""Debate pattern: N debaters argue across rounds; a judge picks the winner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config_a2a.config.models import DebatePattern, DebaterConfig
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

_JUDGE_INSTRUCTIONS = (
    "You are a debate judge. Read the rounds of arguments, then produce a "
    "concise verdict (2-4 sentences) explaining which side is more correct "
    "and why. Reference the strongest argument from each side."
)


async def run_debate(ctx: ExecutionContext) -> None:
    pattern = ctx.config.pattern
    assert isinstance(pattern, DebatePattern)

    await emit_status(ctx, "TASK_STATE_WORKING")
    transcript: list[dict[str, str]] = []

    for round_index in range(pattern.rounds):
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        await emit_thinking(ctx, f"debate round {round_index + 1}/{pattern.rounds}")
        # All debaters speak in parallel for this round, given the prior transcript.
        responses = await asyncio.gather(
            *(_debater_turn(ctx, debater, transcript, round_index) for debater in pattern.debaters)
        )
        for debater, content in zip(pattern.debaters, responses, strict=False):
            transcript.append({"round": str(round_index), "name": debater.name, "content": content})

    judge_prompt = resolve_prompt(pattern.judge, default=_JUDGE_INSTRUCTIONS)
    judge_messages = [
        ChatMessage(role="system", content=judge_prompt),
        ChatMessage(role="user", content=_format_transcript(ctx.user_text, transcript)),
    ]
    verdict = await call_llm(ctx, judge_messages)
    await emit_status(ctx, "TASK_STATE_COMPLETED", text=verdict.content or "(empty)", final=True)


async def _debater_turn(
    ctx: ExecutionContext,
    debater: DebaterConfig,
    transcript: list[dict[str, Any]],
    round_index: int,
) -> str:
    prompt = resolve_prompt(debater, default=f"You are debater {debater.name}.")
    history = "\n".join(f"[{item['name']}] {item['content']}" for item in transcript) or "(no prior rounds)"
    messages = [
        ChatMessage(role="system", content=prompt),
        ChatMessage(
            role="user",
            content=(
                f"Topic: {ctx.user_text}\n\nTranscript so far:\n{history}\n\n"
                f"Your turn (round {round_index + 1}). Reply in 2-4 sentences."
            ),
        ),
    ]
    response = await call_llm(ctx, messages)
    return response.content or "(silent)"


def _format_transcript(topic: str, transcript: list[dict[str, Any]]) -> str:
    rounds: dict[str, list[str]] = {}
    for item in transcript:
        rounds.setdefault(item["round"], []).append(f"[{item['name']}] {item['content']}")
    body = "\n\n".join(
        f"Round {index}:\n" + "\n".join(lines) for index, lines in sorted(rounds.items(), key=lambda kv: int(kv[0]))
    )
    return f"Topic: {topic}\n\n{body}"
