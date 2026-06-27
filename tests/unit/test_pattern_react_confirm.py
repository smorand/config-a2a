"""ReAct destructive-tool confirmation: policy_for is honoured + resume works.

Mirrors the Simple-pattern behaviour: ``auto_approve`` runs the destructive
tool with no prompt, ``auto_deny`` refuses, ``prompt`` suspends with
``INPUT_REQUIRED`` and an approval re-executes the pending call, and
``per_tool`` overrides win. Uses the stdio ``fake_mcp_server`` (``delete_file``
is annotated ``destructiveHint``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.config.models import AgentConfig, ConfirmationsConfig, McpStdioServer
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage, ToolCall
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_mcp_server.py"


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


def _server_block() -> McpStdioServer:
    return McpStdioServer(name="fake", command=sys.executable, args=[str(FIXTURE)], discovery_timeout_seconds=15.0)


async def _build_runtime(
    scripted: list[ChatResponse], *, confirmations: ConfirmationsConfig
) -> tuple[AgentRuntime, str, _Scripted]:
    _, agent, prefix = load_single_agent("02-react")
    cfg: AgentConfig = agent
    cfg.tools.mcp_servers = [_server_block()]
    cfg.confirmations = confirmations
    provider = _Scripted(scripted)
    runtime = AgentRuntime(cfg, provider=provider)
    await runtime.discover_tools()
    return runtime, prefix, provider


def _final_status(body: str) -> dict[str, Any]:
    blocks = [b for b in body.split("\n\n") if b.strip()]
    return next(
        json.loads(line[len("data: ") :])
        for block in reversed(blocks)
        for line in block.splitlines()
        if line.startswith("data: ") and "statusUpdate" in line
    )


def _delete_call() -> ChatResponse:
    return ChatResponse(
        content="",
        tool_calls=[ToolCall(id="d1", name="fake.delete_file", arguments={"path": "/tmp/x"})],
        usage=TokenUsage(input_tokens=1, output_tokens=1),
    )


async def test_react_auto_approve_runs_destructive_tool_without_prompt() -> None:
    scripted = [_delete_call(), ChatResponse(content="done", usage=TokenUsage(input_tokens=1, output_tokens=1))]
    runtime, prefix, provider = await _build_runtime(
        scripted, confirmations=ConfirmationsConfig(destructive_hint="auto_approve")
    )
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "delete it"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final_status(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    # The tool actually ran: its result reached the model on the second turn.
    second_turn = provider.calls[1]
    assert any(m.role == "tool" and "deleted: /tmp/x" in m.content for m in second_turn.messages)


async def test_react_auto_deny_refuses_without_running() -> None:
    scripted = [_delete_call(), ChatResponse(content="ok noted", usage=TokenUsage(input_tokens=1, output_tokens=1))]
    runtime, prefix, provider = await _build_runtime(
        scripted, confirmations=ConfirmationsConfig(destructive_hint="auto_deny")
    )
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "delete it"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final_status(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    second_turn = provider.calls[1]
    tool_msgs = [m for m in second_turn.messages if m.role == "tool"]
    assert tool_msgs and "denied by policy" in tool_msgs[-1].content
    assert all("deleted:" not in m.content for m in tool_msgs)


async def test_react_prompt_then_approve_executes_pending_call() -> None:
    scripted = [_delete_call(), ChatResponse(content="all gone", usage=TokenUsage(input_tokens=1, output_tokens=1))]
    runtime, prefix, provider = await _build_runtime(
        scripted, confirmations=ConfirmationsConfig(destructive_hint="prompt")
    )
    client = TestClient(create_app_for_runtime(runtime))

    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "wipe it"}]}},
    ) as response:
        body = "".join(response.iter_text())
    first = _final_status(body)
    assert first["statusUpdate"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert first["statusUpdate"]["metadata"]["tool_name"] == "fake.delete_file"
    task_id = first["statusUpdate"]["taskId"]

    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m2", "role": "ROLE_USER", "taskId": task_id, "parts": [{"text": "yes"}]}},
    ) as response:
        body = "".join(response.iter_text())
    second = _final_status(body)
    assert second["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    # The pending destructive call was actually re-executed on resume.
    resume_turn = provider.calls[-1]
    assert any(m.role == "tool" and "deleted: /tmp/x" in m.content for m in resume_turn.messages)


async def test_react_prompt_then_deny_cancels() -> None:
    scripted = [_delete_call()]
    runtime, prefix, _ = await _build_runtime(scripted, confirmations=ConfirmationsConfig(destructive_hint="prompt"))
    client = TestClient(create_app_for_runtime(runtime))

    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "wipe it"}]}},
    ) as response:
        body = "".join(response.iter_text())
    task_id = _final_status(body)["statusUpdate"]["taskId"]

    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m2", "role": "ROLE_USER", "taskId": task_id, "parts": [{"text": "no"}]}},
    ) as response:
        body = "".join(response.iter_text())
    second = _final_status(body)
    assert second["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "Cancelled" in second["statusUpdate"]["status"]["message"]["parts"][0]["text"]


async def test_react_per_tool_override_forces_prompt() -> None:
    # Global default auto_approve, but this specific tool requires confirmation.
    scripted = [_delete_call(), ChatResponse(content="done", usage=TokenUsage(input_tokens=1, output_tokens=1))]
    runtime, prefix, _ = await _build_runtime(
        scripted,
        confirmations=ConfirmationsConfig(destructive_hint="auto_approve", per_tool={"fake.delete_file": "prompt"}),
    )
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "delete it"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final_status(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    assert final["statusUpdate"]["metadata"]["tool_name"] == "fake.delete_file"
