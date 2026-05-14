"""End-to-end tool use + INPUT_REQUIRED + resume through the Simple pattern."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.config.models import AgentConfig, McpStdioServer, ToolFilters
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage, ToolCall
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_mcp_server.py"


class _ScriptedProvider(LlmProvider):
    name = "scripted"

    def __init__(self, scripted: list[ChatResponse]) -> None:
        self._queue = list(scripted)
        self.calls: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if not self._queue:
            return ChatResponse(content="(default)", usage=TokenUsage())
        return self._queue.pop(0)

    async def aclose(self) -> None:
        return None


def _server_block() -> McpStdioServer:
    return McpStdioServer(
        name="fake",
        command=sys.executable,
        args=[str(FIXTURE)],
        discovery_timeout_seconds=15.0,
    )


async def _build_runtime(
    scripted: list[ChatResponse], *, filters: ToolFilters | None = None
) -> tuple[AgentRuntime, str]:
    _, agent, prefix = load_single_agent("01-simple")
    cfg: AgentConfig = agent
    cfg.tools.mcp_servers = [_server_block()]
    if filters:
        cfg.tools.filters = filters
    runtime = AgentRuntime(cfg, provider=_ScriptedProvider(scripted))
    await runtime.discover_tools()
    return runtime, prefix


def _final_status(body: str) -> dict[str, Any]:
    blocks = [b for b in body.split("\n\n") if b.strip()]
    return next(
        json.loads(line[len("data: ") :])
        for block in reversed(blocks)
        for line in block.splitlines()
        if line.startswith("data: ") and "statusUpdate" in line
    )


async def test_tool_call_and_complete() -> None:
    scripted = [
        ChatResponse(
            content="",
            tool_calls=[ToolCall(id="tc-1", name="fake.echo", arguments={"text": "hi"})],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        ),
        ChatResponse(content="echo done", usage=TokenUsage(input_tokens=2, output_tokens=2)),
    ]
    runtime, prefix = await _build_runtime(scripted)
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "go"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final_status(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "echo done" in final["statusUpdate"]["status"]["message"]["parts"][0]["text"]


async def test_destructive_tool_triggers_input_required_and_resumes() -> None:
    scripted = [
        ChatResponse(
            content="",
            tool_calls=[ToolCall(id="tc-d", name="fake.delete_file", arguments={"path": "/tmp/x"})],
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        ),
        ChatResponse(content="all gone", usage=TokenUsage(input_tokens=2, output_tokens=2)),
    ]
    runtime, prefix = await _build_runtime(scripted)
    client = TestClient(create_app_for_runtime(runtime))

    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "wipe it"}]}},
    ) as response:
        body = "".join(response.iter_text())
    first = _final_status(body)
    assert first["statusUpdate"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert first["statusUpdate"]["metadata"]["kind"] == "confirm_tool"
    assert first["statusUpdate"]["metadata"]["tool_name"] == "fake.delete_file"
    task_id = first["statusUpdate"]["taskId"]

    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={
            "message": {
                "messageId": "m2",
                "role": "ROLE_USER",
                "taskId": task_id,
                "parts": [{"text": "yes"}],
            }
        },
    ) as response:
        body = "".join(response.iter_text())
    second = _final_status(body)
    assert second["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


@pytest.mark.parametrize(
    "filters_kw,expected",
    [
        (dict(include=["fake.echo"]), {"fake.echo"}),
        (dict(exclude=["*delete*"]), {"fake.echo"}),
    ],
)
async def test_filters_apply(filters_kw: dict[str, Any], expected: set[str]) -> None:
    runtime, _ = await _build_runtime([], filters=ToolFilters(**filters_kw))
    assert {h.spec.name for h in runtime.mcp.handles.values()} == expected
