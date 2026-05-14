"""Tree of Thoughts pattern: branch fan-out → evaluator → top_k pruning → repeat."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from config_a2a.config.models import ToTPattern
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

_GENERATOR_INSTRUCTIONS = (
    "Expand the parent thought into one distinct next step. Be concrete and"
    " novel — do not repeat the parent. Two to four sentences."
)

_EVALUATOR_INSTRUCTIONS = (
    "Score the candidate thought from 0 to 10 for how well it advances the"
    " stated goal. Return ONLY a number, optionally followed by a colon and"
    " a short reason."
)

_SYNTHESISER_INSTRUCTIONS = (
    "You are given a tree of partial reasoning steps. Pick the strongest"
    " path and reply to the user with a focused, complete answer."
)


@dataclass
class _Thought:
    path: list[str]
    score: float = 0.0


async def run_tree_of_thoughts(ctx: ExecutionContext) -> None:
    pattern = ctx.config.pattern
    assert isinstance(pattern, ToTPattern)
    generator_prompt = resolve_prompt(
        type("p", (), {"prompt": pattern.generator_prompt, "prompt_file": pattern.generator_prompt_file})(),
        default=_GENERATOR_INSTRUCTIONS,
    )
    evaluator_prompt = resolve_prompt(
        type("p", (), {"prompt": pattern.evaluator_prompt, "prompt_file": pattern.evaluator_prompt_file})(),
        default=_EVALUATOR_INSTRUCTIONS,
    )

    await emit_status(ctx, "TASK_STATE_WORKING")
    frontier: list[_Thought] = [_Thought(path=[ctx.user_text])]

    for depth in range(pattern.depth):
        if ctx.cancel_event.is_set():
            raise PatternError("cancelled")
        candidates = await _expand(ctx, frontier, generator_prompt, pattern.branches)
        await _score(ctx, candidates, evaluator_prompt)
        candidates.sort(key=lambda t: t.score, reverse=True)
        if pattern.selection == "best":
            frontier = candidates[:1]
        else:
            frontier = candidates[: pattern.top_k]
        await emit_thinking(
            ctx,
            f"ToT depth {depth + 1}: kept {len(frontier)} of {len(candidates)} " f"(top score {frontier[0].score:.1f})",
        )

    best = frontier[0]
    synth_messages = [
        ChatMessage(role="system", content=_SYNTHESISER_INSTRUCTIONS),
        ChatMessage(
            role="user",
            content=f"Goal: {ctx.user_text}\n\nWinning path:\n" + "\n".join(f"- {step}" for step in best.path[1:]),
        ),
    ]
    final = await call_llm(ctx, synth_messages)
    await emit_status(ctx, "TASK_STATE_COMPLETED", text=final.content or "(empty)", final=True)


async def _expand(
    ctx: ExecutionContext, frontier: list[_Thought], generator_prompt: str, branches: int
) -> list[_Thought]:
    async def _branch(parent: _Thought) -> list[_Thought]:
        async def _one() -> _Thought:
            messages = [
                ChatMessage(role="system", content=generator_prompt),
                ChatMessage(
                    role="user",
                    content=f"Goal: {ctx.user_text}\nParent path:\n" + "\n".join(parent.path),
                ),
            ]
            response = await call_llm(ctx, messages)
            return _Thought(path=parent.path + [response.content.strip()])

        return await asyncio.gather(*(_one() for _ in range(branches)))

    nested = await asyncio.gather(*(_branch(thought) for thought in frontier))
    return [item for sublist in nested for item in sublist]


async def _score(ctx: ExecutionContext, candidates: list[_Thought], evaluator_prompt: str) -> None:
    async def _rate(thought: _Thought) -> None:
        messages = [
            ChatMessage(role="system", content=evaluator_prompt),
            ChatMessage(
                role="user",
                content=f"Goal: {ctx.user_text}\nCandidate path:\n" + "\n".join(thought.path),
            ),
        ]
        response = await call_llm(ctx, messages)
        thought.score = _extract_score(response.content)

    await asyncio.gather(*(_rate(c) for c in candidates))


def _extract_score(text: str) -> float:
    match = re.search(r"-?\d+(\.\d+)?", text)
    if not match:
        return 0.0
    try:
        return float(match.group(0))
    except ValueError:
        return 0.0
