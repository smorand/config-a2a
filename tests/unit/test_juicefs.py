"""Native JuiceFS support: model desugaring, prompt injection, filter merge.

Identity is server-wide and JWT-only; the inbound/outbound JWT flow is covered
in ``tests/unit/test_identity_jwt.py``. Here we exercise the parts that are
identity-agnostic plus the JWT-only shape of the compiled MCP server.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from config_a2a.config.juicefs import JuiceFSConfig
from config_a2a.config.loader import load_server_config
from config_a2a.config.models import (
    AgentConfig,
    McpStreamableHttpServer,
    ToolFilters,
)
from config_a2a.juicefs.binding import compile_juicefs, juicefs_prompt_suffix, merge_filters
from config_a2a.mcp.streamable_http import _request_headers
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime
from config_a2a.a2a.sse import SseEmitter

EXAMPLES = Path(__file__).resolve().parents[2] / "config_examples"
JWT_EXAMPLE = EXAMPLES / "09-juicefs" / "agents-jwt.yaml"


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


def test_juicefs_block_desugars_to_jwt_forwarding_server() -> None:
    # Standalone agent validation (no server identity yet): JWT header default,
    # no service credential until the server-level pass folds in identity.
    agent = _agent(default_mount_id="perso-alice")
    assert agent.juicefs is not None
    servers = agent.tools.mcp_servers
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, McpStreamableHttpServer)
    assert server.name == "juicefs"
    assert server.url == "http://localhost:8000/mcp"
    assert server.forward_identity is True
    assert server.identity_header == "X-Forwarded-Authorization"
    assert server.service_credential is None


def test_juicefs_custom_name() -> None:
    agent = _agent(name="vol")
    server = agent.tools.mcp_servers[0]
    assert server.name == "vol"
    assert server.identity_header == "X-Forwarded-Authorization"


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
    server = compile_juicefs(JuiceFSConfig(url="http://h/mcp", name="jfs"))
    assert server.forward_identity is True
    assert server.identity_header == "X-Forwarded-Authorization"
    assert server.service_credential is None
    assert server.headers == {}


# --- filter merge -----------------------------------------------------------


def test_merge_filters_unions_and_dedups() -> None:
    base = ToolFilters(include=["juicefs.fs.read"], exclude=["*.delete"])
    extra = ToolFilters(include=["juicefs.fs.read", "juicefs.fs.list"], exclude=["*.move"])
    merged = merge_filters(base, extra)
    assert merged.include == ["juicefs.fs.read", "juicefs.fs.list"]
    assert merged.exclude == ["*.delete", "*.move"]


def test_merge_filters_is_idempotent() -> None:
    base = ToolFilters(include=["a"], exclude=["b"])
    once = merge_filters(base, ToolFilters(include=["c"], exclude=["d"]))
    twice = merge_filters(once, ToolFilters(include=["c"], exclude=["d"]))
    assert once.model_dump() == twice.model_dump()


def test_juicefs_filters_folded_into_tools_filters() -> None:
    agent = AgentConfig.model_validate(
        {
            "name": "fsbot",
            "model": {"provider": "openai-compatible", "model": "x"},
            "pattern": {"type": "simple"},
            "tools": {"filters": {"exclude": ["other.tool"]}},
            "juicefs": {
                "url": "http://h/mcp",
                "filters": {"include": ["juicefs.fs.*"], "exclude": ["juicefs.fs.delete"]},
            },
        }
    )
    assert agent.tools.filters.include == ["juicefs.fs.*"]
    assert agent.tools.filters.exclude == ["other.tool", "juicefs.fs.delete"]
    # Idempotent on revalidation: no duplicate growth.
    again = AgentConfig.model_validate(agent.model_dump())
    assert again.tools.filters.exclude == ["other.tool", "juicefs.fs.delete"]


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


def test_request_headers_call_without_credential_omits_header() -> None:
    server = compile_juicefs(JuiceFSConfig(url="http://h/mcp"))
    assert "X-Forwarded-Authorization" not in _request_headers(server, discovery=False)


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
    server = load_server_config(JWT_EXAMPLE)
    agent = server.agents[0]
    assert agent.slug == "files"
    assert agent.juicefs is not None
    juicefs_servers = [s for s in agent.tools.mcp_servers if s.name == "juicefs"]
    assert len(juicefs_servers) == 1
    compiled = juicefs_servers[0]
    assert compiled.forward_identity is True
    assert compiled.identity_header == "X-Forwarded-Authorization"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
