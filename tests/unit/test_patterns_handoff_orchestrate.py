"""Iter 8: Handoff (local + remote) and Orchestrate (parallel) via scripted provider."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from config_a2a.a2a import client as a2a_client
from config_a2a.api import create_app
from config_a2a.config.loader import load_agent_config
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime

EX_HANDOFF = Path(__file__).resolve().parents[2] / "config_examples" / "04-handoff" / "router.yaml"
EX_ORCH = Path(__file__).resolve().parents[2] / "config_examples" / "05-orchestrate" / "agent.yaml"


class _Scripted(LlmProvider):
    name = "scripted"

    def __init__(self, queue: list[ChatResponse]) -> None:
        self._queue = list(queue)
        self.calls: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return self._queue.pop(0) if self._queue else ChatResponse(content="(default)", usage=TokenUsage())

    async def aclose(self) -> None:
        return None


def _final(body: str) -> dict[str, Any]:
    blocks = [b for b in body.split("\n\n") if b.strip()]
    return next(
        json.loads(line[len("data: ") :])
        for block in reversed(blocks)
        for line in block.splitlines()
        if line.startswith("data: ") and "statusUpdate" in line
    )


def test_handoff_local_subagent() -> None:
    config = load_agent_config(EX_HANDOFF)
    # Router picks 'math' → sub-agent (math.yaml) runs Simple and returns text.
    scripted = [
        ChatResponse(content='{"target": "math"}', usage=TokenUsage(input_tokens=1, output_tokens=1)),
        ChatResponse(content="17 * 23 = 391.", usage=TokenUsage(input_tokens=2, output_tokens=2)),
    ]
    runtime = AgentRuntime(config, provider=_Scripted(scripted))
    client = TestClient(create_app(runtime))
    with client.stream(
        "POST",
        "/message:stream",
        json={"message": {"messageId": "h1", "role": "ROLE_USER", "parts": [{"text": "17 * 23?"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    text = final["statusUpdate"]["status"]["message"]["parts"][0]["text"]
    assert "391" in text


def test_orchestrate_parallel_with_mocked_remote(monkeypatch) -> None:
    config = load_agent_config(EX_ORCH)
    captured_urls: list[str] = []

    async def fake_send(url: str, text: str, **kwargs):  # noqa: ARG001
        captured_urls.append(url)
        from config_a2a.a2a.client import RemoteAgentResult

        return RemoteAgentResult(
            state="TASK_STATE_COMPLETED",
            text=f"reply from {url}",
            task_id="t",
            raw={},
        )

    monkeypatch.setattr(a2a_client, "send_text", fake_send)
    # Also need to patch the import inside orchestrate.py since it does `from config_a2a.a2a.client import send_text`.
    from config_a2a.patterns import orchestrate as orch_mod

    monkeypatch.setattr(orch_mod, "send_text", fake_send)

    scripted = [ChatResponse(content="synthesised reply", usage=TokenUsage(input_tokens=1, output_tokens=1))]
    runtime = AgentRuntime(config, provider=_Scripted(scripted))
    client = TestClient(create_app(runtime))
    with client.stream(
        "POST",
        "/message:stream",
        json={"message": {"messageId": "o1", "role": "ROLE_USER", "parts": [{"text": "go"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    text = final["statusUpdate"]["status"]["message"]["parts"][0]["text"]
    assert "synthesised reply" in text
    assert len(captured_urls) == 2  # both remote agents called
