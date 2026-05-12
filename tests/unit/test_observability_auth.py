"""Iter 10: JSONL OTel exporter, redaction, bearer-auth middleware, card hints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app
from config_a2a.config.loader import load_agent_config
from config_a2a.observability.jsonl_exporter import JsonlSpanExporter, _redact
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime

EX = Path(__file__).resolve().parents[2] / "config_examples" / "01-simple" / "agent.yaml"


class _Echo(LlmProvider):
    name = "echo"

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: ARG002
        return ChatResponse(content="ok", usage=TokenUsage())

    async def aclose(self) -> None:
        return None


def test_redaction_helper() -> None:
    assert _redact("authorization", "Bearer secret") == "[REDACTED]"
    assert _redact("Authorization", "Bearer secret") == "[REDACTED]"  # case-insensitive
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
    # Reset the global tracer so other tests don't see our test-only provider.
    trace._TRACER_PROVIDER = None  # noqa: SLF001 — safe in test isolation


def test_bearer_auth_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BEARER", "shh")
    config = load_agent_config(EX)
    config.authentication.type = "bearer"
    config.authentication.value_env = "AGENT_BEARER"
    runtime = AgentRuntime(config, provider=_Echo())
    client = TestClient(create_app(runtime))
    # Public path: no auth needed.
    assert client.get("/health").status_code == 200
    # Protected: missing header → 401.
    response = client.post(
        "/message:send",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    )
    assert response.status_code == 401
    # With bearer.
    response = client.post(
        "/message:send",
        headers={"Authorization": "Bearer shh"},
        json={"message": {"messageId": "m2", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    )
    assert response.status_code == 200


def test_agent_card_surfaces_skill_hints() -> None:
    config = load_agent_config(EX)
    runtime = AgentRuntime(config, provider=_Echo())
    client = TestClient(create_app(runtime))
    card = client.get("/.well-known/a2a/agent-card").json()
    chat_skill = next(s for s in card["skills"] if s["id"] == "chat")
    assert chat_skill["tags"] == ["chat"]
    assert "What can you do?" in chat_skill["examples"]
