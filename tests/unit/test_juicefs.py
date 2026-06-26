"""Native JuiceFS support: model desugaring, prompt injection, identity flow."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from config_a2a.api import _resolve_inbound_user_header
from config_a2a.config.juicefs import JuiceFSConfig
from config_a2a.config.loader import load_server_config
from config_a2a.config.models import AgentConfig, McpStreamableHttpServer
from config_a2a.identity import (
    DEFAULT_FORWARDED_USER_HEADER,
    IdentityCaptureMiddleware,
    bind_user,
    current_user,
    reset_user,
)
from config_a2a.a2a.sse import SseEmitter
from config_a2a.juicefs.binding import compile_juicefs, juicefs_prompt_suffix
from config_a2a.mcp.streamable_http import _request_headers
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import example_yaml

EXAMPLES = Path(__file__).resolve().parents[2] / "config_examples"


def _agent(**juicefs: object) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "name": "fsbot",
            "model": {"provider": "openai-compatible", "model": "x"},
            "pattern": {"type": "simple"},
            "juicefs": {"url": "http://localhost:8000/mcp", **juicefs},
        }
    )


# --- model + desugaring -----------------------------------------------------


def test_juicefs_block_desugars_to_identity_forwarding_server() -> None:
    agent = _agent(default_mount_id="perso-alice", service_identity="svc-bot")
    assert agent.juicefs is not None
    servers = agent.tools.mcp_servers
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, McpStreamableHttpServer)
    assert server.name == "juicefs"
    assert server.url == "http://localhost:8000/mcp"
    assert server.forward_identity is True
    assert server.identity_header == "X-Forwarded-User"
    assert server.service_identity == "svc-bot"


def test_juicefs_custom_name_and_header() -> None:
    agent = _agent(name="vol", identity={"forwarded_user_header": "X-User"})
    server = agent.tools.mcp_servers[0]
    assert server.name == "vol"
    assert server.identity_header == "X-User"


def test_desugaring_is_idempotent_on_revalidation() -> None:
    agent = _agent()
    again = AgentConfig.model_validate(agent.model_dump())
    assert len(again.tools.mcp_servers) == 1


def test_no_juicefs_block_means_no_extra_server() -> None:
    agent = AgentConfig.model_validate(
        {
            "name": "plain",
            "model": {"provider": "openai-compatible", "model": "x"},
            "pattern": {"type": "simple"},
        }
    )
    assert agent.juicefs is None
    assert agent.tools.mcp_servers == []


def test_compile_juicefs_direct() -> None:
    server = compile_juicefs(JuiceFSConfig(url="http://h/mcp", name="jfs", service_identity="svc"))
    assert server.forward_identity is True
    assert server.service_identity == "svc"
    assert server.headers == {}


# --- prompt injection -------------------------------------------------------


def test_prompt_suffix_mentions_list_allowed_roots() -> None:
    text = juicefs_prompt_suffix(default_mount_id=None)
    assert "fs.list_allowed_roots" in text
    assert "mount_id" in text


def test_prompt_suffix_includes_default_mount() -> None:
    text = juicefs_prompt_suffix(default_mount_id="projet-marketing")
    assert "projet-marketing" in text
    assert "current project" in text


# --- outbound identity headers ---------------------------------------------


def test_request_headers_no_forwarding() -> None:
    server = McpStreamableHttpServer(name="x", url="http://h", headers={"A": "b"})
    assert _request_headers(server, discovery=False) == {"A": "b"}


def test_request_headers_discovery_uses_service_identity() -> None:
    server = compile_juicefs(JuiceFSConfig(url="http://h/mcp", service_identity="svc-bot"))
    headers = _request_headers(server, discovery=True)
    assert headers["X-Forwarded-User"] == "svc-bot"


def test_request_headers_call_uses_bound_user() -> None:
    server = compile_juicefs(JuiceFSConfig(url="http://h/mcp", service_identity="svc-bot"))
    token = bind_user("alice")
    try:
        headers = _request_headers(server, discovery=False)
    finally:
        reset_user(token)
    assert headers["X-Forwarded-User"] == "alice"


def test_request_headers_call_without_user_omits_header() -> None:
    server = compile_juicefs(JuiceFSConfig(url="http://h/mcp"))
    assert current_user() is None
    assert "X-Forwarded-User" not in _request_headers(server, discovery=False)


# --- inbound identity capture (middleware) ----------------------------------


def _identity_probe_app(header_name: str) -> Starlette:
    async def whoami(_request: Request) -> JSONResponse:
        return JSONResponse({"user": current_user()})

    app = Starlette(routes=[Route("/whoami", whoami)])
    app.add_middleware(IdentityCaptureMiddleware, header_name=header_name)
    return app


def test_middleware_binds_forwarded_user() -> None:
    client = TestClient(_identity_probe_app(DEFAULT_FORWARDED_USER_HEADER))
    resp = client.get("/whoami", headers={"X-Forwarded-User": "bob"})
    assert resp.json() == {"user": "bob"}


def test_middleware_binds_none_when_header_absent() -> None:
    client = TestClient(_identity_probe_app(DEFAULT_FORWARDED_USER_HEADER))
    assert client.get("/whoami").json() == {"user": None}


def test_middleware_custom_header_name() -> None:
    client = TestClient(_identity_probe_app("X-User"))
    resp = client.get("/whoami", headers={"X-User": "carol"})
    assert resp.json() == {"user": "carol"}


# --- inbound header resolution ---------------------------------------------


def test_resolve_inbound_header_defaults_without_juicefs() -> None:
    plain = AgentConfig.model_validate(
        {"name": "p", "model": {"provider": "openai-compatible", "model": "x"}, "pattern": {"type": "simple"}}
    )
    assert _resolve_inbound_user_header([plain]) == DEFAULT_FORWARDED_USER_HEADER


def test_resolve_inbound_header_from_juicefs_agent() -> None:
    agent = _agent(identity={"forwarded_user_header": "X-User"})
    assert _resolve_inbound_user_header([agent]) == "X-User"


# --- example ----------------------------------------------------------------


class _CapturingProvider(LlmProvider):
    name = "capturing"

    def __init__(self) -> None:
        self.calls: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        return ChatResponse(content="done", usage=TokenUsage(input_tokens=1, output_tokens=1))

    async def aclose(self) -> None:
        return None


async def _run_and_capture(agent: AgentConfig, *, mount_id: str | None) -> str:
    provider = _CapturingProvider()
    runtime = AgentRuntime(agent, provider=provider)
    task = await runtime.tasks.create()
    emitter = SseEmitter()

    async def drain() -> None:
        async for _ in emitter.stream():
            pass

    import asyncio

    await asyncio.gather(
        runtime.run_message("list my files", emitter, task, mount_id=mount_id),
        drain(),
    )
    system = provider.calls[0].messages[0]
    assert system.role == "system"
    return system.content or ""


async def test_runtime_injects_default_mount_into_system_prompt() -> None:
    agent = _agent(default_mount_id="perso-alice")
    prompt = await _run_and_capture(agent, mount_id=None)
    assert "perso-alice" in prompt
    assert "fs.list_allowed_roots" in prompt


async def test_runtime_per_message_mount_overrides_default() -> None:
    agent = _agent(default_mount_id="perso-alice")
    prompt = await _run_and_capture(agent, mount_id="projet-marketing")
    assert "projet-marketing" in prompt
    assert "perso-alice" not in prompt


def test_example_09_loads_and_desugars() -> None:
    server = load_server_config(example_yaml("09-juicefs"))
    agent = server.agents[0]
    assert agent.slug == "files"
    assert agent.juicefs is not None
    assert any(s.name == "juicefs" and s.forward_identity for s in agent.tools.mcp_servers)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
