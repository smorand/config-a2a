"""Smoke test the A2A surface (card, send, stream, get task) end-to-end."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent


class _EchoProvider(LlmProvider):
    name = "echo"

    async def chat(self, request: ChatRequest) -> ChatResponse:
        user = next((m for m in reversed(request.messages) if m.role == "user"), None)
        return ChatResponse(content=f"echo: {user.content if user else ''}")

    async def aclose(self) -> None:
        return None


@pytest.fixture()
def client_and_prefix() -> tuple[TestClient, str]:
    _, agent, prefix = load_single_agent("01-simple")
    runtime = AgentRuntime(agent, provider=_EchoProvider())
    return TestClient(create_app_for_runtime(runtime)), prefix


def test_health(client_and_prefix: tuple[TestClient, str]) -> None:
    client, _ = client_and_prefix
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_directory_lists_agents(client_and_prefix: tuple[TestClient, str]) -> None:
    client, _ = client_and_prefix
    response = client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "simple-assistant"
    assert any(a["slug"] == "simple" for a in body["agents"])


def test_agent_card(client_and_prefix: tuple[TestClient, str]) -> None:
    client, prefix = client_and_prefix
    response = client.get(f"{prefix}/.well-known/agent-card.json")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "simple-assistant"
    assert body["capabilities"]["streaming"] is True
    assert body["url"].endswith(prefix)


def test_send_message_completes(client_and_prefix: tuple[TestClient, str]) -> None:
    client, prefix = client_and_prefix
    payload = {"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "hello"}]}}
    response = client.post(f"{prefix}/message:send", json=payload)
    assert response.status_code == 200
    task = response.json()
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    final_msg = task["status"]["message"]
    assert "echo: hello" in final_msg["parts"][0]["text"]


def test_stream_message_emits_sse(client_and_prefix: tuple[TestClient, str]) -> None:
    client, prefix = client_and_prefix
    payload = {"message": {"messageId": "m-2", "role": "ROLE_USER", "parts": [{"text": "ping"}]}}
    with client.stream("POST", f"{prefix}/message:stream", json=payload) as response:
        assert response.status_code == 200
        chunks = list(response.iter_text())
    body = "".join(chunks)
    assert "event: task" in body
    assert "event: statusUpdate" in body
    blocks = [b for b in body.split("\n\n") if b.strip()]
    final = next(
        json.loads(line[len("data: ") :])
        for block in reversed(blocks)
        for line in block.splitlines()
        if line.startswith("data: ") and "statusUpdate" in line
    )
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert final["statusUpdate"]["final"] is True


def test_get_and_cancel_task(client_and_prefix: tuple[TestClient, str]) -> None:
    client, prefix = client_and_prefix
    payload = {"message": {"messageId": "m-3", "role": "ROLE_USER", "parts": [{"text": "trace me"}]}}
    sent = client.post(f"{prefix}/message:send", json=payload).json()
    task_id = sent["id"]
    fetched = client.get(f"{prefix}/tasks/{task_id}").json()
    assert fetched["id"] == task_id
    canceled = client.post(f"{prefix}/tasks/{task_id}:cancel").json()
    assert canceled["status"]["state"] == "TASK_STATE_CANCELED"


def test_missing_task_returns_404(client_and_prefix: tuple[TestClient, str]) -> None:
    client, prefix = client_and_prefix
    response = client.get(f"{prefix}/tasks/does-not-exist")
    assert response.status_code == 404


def test_unknown_slug_returns_404(client_and_prefix: tuple[TestClient, str]) -> None:
    client, _ = client_and_prefix
    response = client.get("/agents/does-not-exist/.well-known/agent-card.json")
    assert response.status_code == 404
