"""JSONL OTel exporter, redaction, bearer-auth dependency, card hints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.config.models import AuthenticationConfig
from config_a2a.observability.jsonl_exporter import JsonlSpanExporter, _redact
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent


class _Echo(LlmProvider):
    name = "echo"

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: ARG002
        return ChatResponse(content="ok", usage=TokenUsage())

    async def aclose(self) -> None:
        return None


def test_redaction_helper() -> None:
    assert _redact("authorization", "Bearer secret") == "[REDACTED]"
    assert _redact("Authorization", "Bearer secret") == "[REDACTED]"
    assert _redact("model", "gpt-x") == "gpt-x"


def test_jsonl_exporter_writes_redacted_attrs(tmp_path: Path) -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    file = tmp_path / "trace.jsonl"
    exporter = JsonlSpanExporter(file)
    provider = TracerProvider(resource=Resource.create({"service.name": "t"}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        span.set_attribute("authorization", "Bearer secret")
        span.set_attribute("gen_ai.system", "openai-compatible")
    provider.force_flush()
    provider.shutdown()
    line = file.read_text(encoding="utf-8").strip().splitlines()[0]
    record = json.loads(line)
    assert record["name"] == "op"
    assert record["attributes"]["authorization"] == "[REDACTED]"
    assert record["attributes"]["gen_ai.system"] == "openai-compatible"
    trace._TRACER_PROVIDER = None  # noqa: SLF001 — safe in test isolation


def test_bearer_auth_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER", "shh")
    _, agent, prefix = load_single_agent("01-simple")
    agent.authentication = AuthenticationConfig(type="bearer", value_env="AGENT_BEARER")
    runtime = AgentRuntime(agent, provider=_Echo())
    client = TestClient(create_app_for_runtime(runtime))
    # Health is server-level, no auth.
    assert client.get("/health").status_code == 200
    # Per-agent A2A: missing bearer → 401.
    response = client.post(
        f"{prefix}/message:send",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    )
    assert response.status_code == 401
    response = client.post(
        f"{prefix}/message:send",
        headers={"Authorization": "Bearer shh"},
        json={"message": {"messageId": "m2", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    )
    assert response.status_code == 200


def test_agent_card_surfaces_skill_hints() -> None:
    _, agent, prefix = load_single_agent("01-simple")
    runtime = AgentRuntime(agent, provider=_Echo())
    client = TestClient(create_app_for_runtime(runtime))
    card = client.get(f"{prefix}/.well-known/agent-card.json").json()
    chat_skill = next(s for s in card["skills"] if s["id"] == "chat")
    assert chat_skill["tags"] == ["chat"]
    assert "What can you do?" in chat_skill["examples"]
