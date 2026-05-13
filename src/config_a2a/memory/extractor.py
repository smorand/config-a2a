"""LLM-based fact extractor used by the long-term `write` hook."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from config_a2a.memory.store import MemoryRecord
from config_a2a.providers.base import ChatMessage, ChatRequest, LlmProvider

log = logging.getLogger(__name__)

_EXTRACT_PROMPT = """\
You distil durable, user-facing facts from a conversation. Return ONLY JSON:

{"facts":[{"text":"<fact>","scope":"user|agent","tags":["..."]}]}

Rules:
- 0 to 3 facts max. Quality over quantity.
- `scope: "user"` for stable facts about THIS user (preferences, identity, profile).
- `scope: "agent"` for lessons the assistant learned (gotchas, successful tactics).
- Skip pleasantries, transient questions, and anything obvious in the assistant's role.
- If nothing is worth remembering, return {"facts":[]}.
- No prose, no code fences, no commentary — only the JSON object.
"""


def _strip_fences(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    return fence.group(1) if fence else text


def _find_json_object(text: str) -> str | None:
    """Locate the first balanced ``{...}`` substring in ``text``, or None.

    Lets us recover from free models that wrap their JSON in chatty prose.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


async def extract_facts(
    provider: LlmProvider,
    *,
    user_text: str,
    assistant_text: str,
    forced_scope: str | None = None,
) -> list[MemoryRecord]:
    """Run one extraction call. Returns 0..N records ready for `store.write`."""
    convo = f"User: {user_text}\nAssistant: {assistant_text}"
    response = await provider.chat(
        ChatRequest(
            messages=[
                ChatMessage(role="system", content=_EXTRACT_PROMPT),
                ChatMessage(role="user", content=convo),
            ],
            temperature=0.0,
        )
    )
    payload = _strip_fences(response.content or "")
    data: Any
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        embedded = _find_json_object(payload)
        if embedded is None:
            log.warning(
                "memory: extractor returned no JSON object (raw=%r), dropping",
                payload[:200],
            )
            return []
        try:
            data = json.loads(embedded)
        except json.JSONDecodeError:
            log.warning("memory: embedded JSON unparseable (raw=%r), dropping", payload[:200])
            return []
    facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(facts, list):
        return []
    out: list[MemoryRecord] = []
    for raw in facts:
        if not isinstance(raw, dict):
            continue
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        scope = forced_scope or raw.get("scope") or "agent"
        if scope not in ("user", "agent"):
            scope = "agent"
        tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
        out.append(MemoryRecord(text=text, scope=scope, tags=[str(t) for t in tags]))  # type: ignore[arg-type]
    return out
