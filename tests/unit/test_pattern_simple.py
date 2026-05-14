"""The Simple pattern produces a COMPLETED task from a stubbed provider."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent


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
def client_with_stub() -> tuple[TestClient, _StubProvider, str]:
    _, agent, prefix = load_single_agent("01-simple")
    provider = _StubProvider("Bonjour ! Comment puis-je vous aider ?")
    runtime = AgentRuntime(agent, provider=provider)
    return TestClient(create_app_for_runtime(runtime)), provider, prefix


def test_simple_pattern_completes_stream(
    client_with_stub: tuple[TestClient, _StubProvider, str],
) -> None:
    client, provider, prefix = client_with_stub
    payload = {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "salut"}]}}
    with client.stream("POST", f"{prefix}/message:stream", json=payload) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    blocks = [b for b in body.split("\n\n") if b.strip()]
    final_data = next(
        json.loads(line[len("data: ") :])
        for block in reversed(blocks)
        for line in block.splitlines()
        if line.startswith("data: ") and "statusUpdate" in line
    )
    assert final_data["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "Bonjour" in final_data["statusUpdate"]["status"]["message"]["parts"][0]["text"]
    assert provider.calls
    first_call = provider.calls[0]
    assert first_call.messages[0].role == "system"
    assert "friendly assistant" in first_call.messages[0].content


def test_simple_pattern_send_endpoint(
    client_with_stub: tuple[TestClient, _StubProvider, str],
) -> None:
    client, _, prefix = client_with_stub
    response = client.post(
        f"{prefix}/message:send",
        json={"message": {"messageId": "m-2", "role": "ROLE_USER", "parts": [{"text": "salut"}]}},
    )
    assert response.status_code == 200
    task = response.json()
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
