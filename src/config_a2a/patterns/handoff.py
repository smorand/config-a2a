"""Handoff pattern: pick one target agent (local sub-agent or remote A2A URL)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from config_a2a.a2a.client import send_text
from config_a2a.config.loader import load_agent_config
from config_a2a.config.models import HandoffPattern, HandoffTarget
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

_ROUTER_INSTRUCTIONS = (
    "You route user requests to the right target agent. "
    "Return ONLY JSON of the form {\"target\": \"<name>\"}. "
    "Pick exactly one of the listed targets."
)


async def run_handoff(ctx: ExecutionContext) -> None:
    pattern = ctx.config.pattern
    assert isinstance(pattern, HandoffPattern)
    router_prompt = resolve_prompt(pattern.router, default="You are a router.")
    targets = pattern.targets
    target_listing = "\n".join(
        f"- {t.name}: {t.description or '(no description)'}" for t in targets
    )

    await emit_status(ctx, "TASK_STATE_WORKING")
    messages = [
        ChatMessage(role="system", content=router_prompt + "\n\n" + _ROUTER_INSTRUCTIONS + "\n\nTargets:\n" + target_listing),
        ChatMessage(role="user", content=ctx.user_text),
    ]
    decision = await call_llm(ctx, messages)
    chosen = _parse_choice(decision.content, [t.name for t in targets])
    target = next(t for t in targets if t.name == chosen)
    await emit_thinking(ctx, f"handoff: routing to '{chosen}'")

    if target.agent_ref:
        text = await _run_local(ctx, target)
    else:
        text = await _run_remote(ctx, target)

    await emit_status(ctx, "TASK_STATE_COMPLETED", text=text or "(empty)", final=True)


def _parse_choice(text: str, valid: list[str]) -> str:
    text = text.strip()
    try:
        data = json.loads(text)
        choice = str(data.get("target", "")).strip()
        if choice in valid:
            return choice
    except json.JSONDecodeError:
        pass
    # Fallback: pick the first valid name that appears anywhere in the output.
    for name in valid:
        if name in text:
            return name
    raise PatternError(f"router output did not pick a valid target: {text[:200]}")


async def _run_local(parent: ExecutionContext, target: HandoffTarget) -> str:
    """Instantiate the sub-agent in-process and dispatch the user message through its pattern."""
    if parent.depth >= parent.config.guardrails.max_depth:
        raise PatternError(f"handoff depth exceeded ({parent.config.guardrails.max_depth})")
    config_path = Path(str(target.agent_ref))
    sub_config = load_agent_config(config_path)
    from config_a2a.patterns import get_runner

    sub_runner = get_runner(sub_config.pattern.type)
    sub_ctx = parent_with_overrides(parent, sub_config)
    captured: list[str] = []
    # Capture the final text by patching emit_status' completion event.
    original = parent.emitter

    class _Capturing:
        async def emit(self, payload: dict[str, Any], event: str | None = None) -> None:
            await original.emit(payload, event=event)
            update = payload.get("statusUpdate") or {}
            status = update.get("status") or {}
            if status.get("state") == "TASK_STATE_COMPLETED":
                message = status.get("message") or {}
                for part in message.get("parts", []):
                    if part.get("text"):
                        captured.append(part["text"])

        async def close(self) -> None:  # not propagated; parent owns the lifecycle
            return None

    sub_ctx_with_capture = sub_ctx.__class__(  # type: ignore[call-arg]
        config=sub_ctx.config,
        user_text=sub_ctx.user_text,
        task_id=sub_ctx.task_id,
        context_id=sub_ctx.context_id,
        emitter=_Capturing(),  # type: ignore[arg-type]
        provider=parent.provider,
        task_store=parent.task_store,
        system_prompt=sub_ctx.system_prompt,
        depth=parent.depth + 1,
        tools=parent.tools,
        mcp=parent.mcp,
    )
    await sub_runner(sub_ctx_with_capture)
    return "\n\n".join(captured)


async def _run_remote(parent: ExecutionContext, target: HandoffTarget) -> str:
    result = await send_text(
        str(target.a2a_url),
        parent.user_text,
        auth=target.auth,
        context_id=parent.context_id,
    )
    if result.state != "TASK_STATE_COMPLETED":
        raise PatternError(f"remote target '{target.name}' returned state {result.state}")
    return result.text


def parent_with_overrides(parent: ExecutionContext, sub_config: Any) -> ExecutionContext:
    """Build a child context that inherits the parent's runtime resources."""
    from config_a2a.config.prompts import resolve_system_prompt

    system = resolve_system_prompt(
        sub_config.prompts.system, sub_config.prompts.system_file, default=""
    )
    return parent.__class__(  # type: ignore[call-arg]
        config=sub_config,
        user_text=parent.user_text,
        task_id=parent.task_id,
        context_id=parent.context_id,
        emitter=parent.emitter,
        provider=parent.provider,
        task_store=parent.task_store,
        system_prompt=system,
        depth=parent.depth + 1,
        tools=parent.tools,
        mcp=parent.mcp,
    )
