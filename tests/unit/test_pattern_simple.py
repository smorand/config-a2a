"""Iter 2: the Simple pattern produces a COMPLETED task from a stubbed provider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app
from config_a2a.config.loader import load_agent_config
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime

EXAMPLE = Path(__file__).resolve().parents[2] / "config_examples" / "01-simple" / "agent.yaml"


class _StubProvider(LlmProvider):
    name = "stub"

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.calls: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content=self._reply, usage=TokenUsage(input_tokens=5, output_tokens=7))

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def client_with_stub() -> tuple[TestClient, _StubProvider]:
    config = load_agent_config(EXAMPLE)
    provider = _StubProvider("Bonjour ! Comment puis-je vous aider ?")
    runtime = AgentRuntime(config, provider=provider)
    return TestClient(create_app(runtime)), provider


def test_simple_pattern_completes_stream(client_with_stub: tuple[TestClient, _StubProvider]) -> None:
    client, provider = client_with_stub
    payload = {
        "message": {
            "messageId": "m-1",
            "role": "ROLE_USER",
            "parts": [{"text": "salut"}],
        }
    }
    with client.stream("POST", "/message:stream", json=payload) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    # Last data: line is the final statusUpdate with COMPLETED state and assistant text.
    blocks = [b for b in body.split("\n\n") if b.strip()]
    final_data = next(
        json.loads(line[len("data: ") :])
        for block in reversed(blocks)
        for line in block.splitlines()
        if line.startswith("data: ") and "statusUpdate" in line
    )
    assert final_data["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "Bonjour" in final_data["statusUpdate"]["status"]["message"]["parts"][0]["text"]
    assert provider.calls  # provider was actually called
    # System prompt was inserted from the example's prompts/system.md
    first_call = provider.calls[0]
    assert first_call.messages[0].role == "system"
    assert "friendly assistant" in first_call.messages[0].content


def test_simple_pattern_send_endpoint(client_with_stub: tuple[TestClient, _StubProvider]) -> None:
    client, _ = client_with_stub
    response = client.post(
        "/message:send",
        json={
            "message": {
                "messageId": "m-2",
                "role": "ROLE_USER",
                "parts": [{"text": "salut"}],
            }
        },
    )
    assert response.status_code == 200
    task = response.json()
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
