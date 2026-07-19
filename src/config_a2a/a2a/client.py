"""Outbound A2A client used by Handoff and Orchestrate patterns.

Fetches an agent card, opens a ``/message:stream`` SSE connection, and
returns the final text + terminal state.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)


@dataclass
class RemoteAgentResult:
    state: str
    text: str
    task_id: str | None
    raw: dict[str, Any]


_CARD_PATHS = (
    "/.well-known/a2a/agent-card",
    "/.well-known/agent-card.json",
    "/.well-known/agent.json",
)


async def fetch_agent_card(url: str, *, headers: dict[str, str] | None = None) -> dict[str, Any]:
    base = url.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0) as client:
        last_status = 0
        for path in _CARD_PATHS:
            try:
                response = await client.get(base + path, headers=headers or {})
            except httpx.HTTPError as exc:
                log.warning("agent card fetch failed at %s: %s", base + path, exc)
                continue
            if response.status_code == 200:
                return response.json()
            last_status = response.status_code
    raise RuntimeError(f"could not fetch agent card from {url} (last status: {last_status})")


def _auth_headers(auth: Any) -> dict[str, str]:
    if auth is None or getattr(auth, "type", "none") == "none":
        return {}
    value_env = getattr(auth, "value_env", None)
    value = os.environ.get(value_env) if value_env else None
    if not value:
        return {}
    if auth.type == "bearer":
        return {"Authorization": f"Bearer {value}"}
    if auth.type == "api_key":
        return {getattr(auth, "header_name", "Authorization"): value}
    return {}


async def send_text(
    url: str,
    text: str,
    *,
    auth: Any = None,
    context_id: str | None = None,
    timeout_seconds: float = 180.0,
) -> RemoteAgentResult:
    """Send a single user message to a remote agent and drain the SSE stream."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "A2A-Version": "1.0",
    }
    headers.update(_auth_headers(auth))
    payload = {
        "message": {
            "messageId": str(uuid.uuid4()),
            "role": "ROLE_USER",
            "contextId": context_id or str(uuid.uuid4()),
            "parts": [{"text": text}],
        }
    }
    final_state = "TASK_STATE_FAILED"
    final_text = ""
    task_id: str | None = None
    raw: dict[str, Any] = {}
    target = url.rstrip("/") + "/message:stream"
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        async with client.stream("POST", target, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for chunk in response.aiter_lines():
                if not chunk.startswith("data: "):
                    continue
                try:
                    parsed = json.loads(chunk[len("data: ") :])
                except json.JSONDecodeError:
                    continue
                raw = parsed
                if "task" in parsed:
                    task_id = parsed["task"].get("id")
                artifact_update = parsed.get("artifactUpdate")
                if artifact_update:
                    for part in artifact_update.get("artifact", {}).get("parts", []):
                        text_value = part.get("text")
                        if text_value:
                            final_text = text_value
                update = parsed.get("statusUpdate") or {}
                status = update.get("status") or {}
                state = status.get("state")
                if state:
                    final_state = state
                msg = status.get("message") or {}
                for part in msg.get("parts", []):
                    text_value = part.get("text")
                    if text_value:
                        final_text = text_value
                if update.get("final"):
                    break
    return RemoteAgentResult(state=final_state, text=final_text, task_id=task_id, raw=raw)
