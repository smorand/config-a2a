"""ReAct + Plan & Execute via scripted provider responses."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.guardrails.anti_loop import is_loop
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent


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


def test_anti_loop_helpers() -> None:
    assert is_loop(["echoed: hi"], "echoed: hi") is True
    assert is_loop(["echoed: hi"], "echoed: hello") is False
    assert is_loop(["A long answer..."], "A long answer with more text") is True
    assert is_loop([], "anything") is False


def test_react_completes_without_tools() -> None:
    _, agent, prefix = load_single_agent("02-react")
    agent.tools.mcp_servers = []
    scripted = [ChatResponse(content="42 is the answer.", usage=TokenUsage(input_tokens=1, output_tokens=1))]
    runtime = AgentRuntime(agent, provider=_Scripted(scripted))
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "r1", "role": "ROLE_USER", "parts": [{"text": "what?"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "42" in final["statusUpdate"]["status"]["message"]["parts"][0]["text"]


def test_plan_execute_walks_steps_and_synthesises() -> None:
    _, agent, prefix = load_single_agent("03-plan-execute")
    plan_json = json.dumps(
        {
            "steps": [
                {"id": "s1", "instruction": "first", "done": False},
                {"id": "s2", "instruction": "second", "done": False},
            ]
        }
    )
    scripted = [
        ChatResponse(content=plan_json, usage=TokenUsage(input_tokens=10, output_tokens=10)),
        ChatResponse(content="result of first", usage=TokenUsage(input_tokens=5, output_tokens=5)),
        ChatResponse(content="result of second", usage=TokenUsage(input_tokens=5, output_tokens=5)),
        ChatResponse(content="overall: first + second", usage=TokenUsage(input_tokens=5, output_tokens=5)),
    ]
    runtime = AgentRuntime(agent, provider=_Scripted(scripted))
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "p1", "role": "ROLE_USER", "parts": [{"text": "combine A and B"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    text = final["statusUpdate"]["status"]["message"]["parts"][0]["text"]
    assert "overall" in text


def test_plan_execute_recovers_from_invalid_json() -> None:
    _, agent, prefix = load_single_agent("03-plan-execute")
    plan_bad = "not json at all"
    plan_good = json.dumps({"steps": [{"id": "s1", "instruction": "do it"}]})
    scripted = [
        ChatResponse(content=plan_bad, usage=TokenUsage(input_tokens=1, output_tokens=1)),
        ChatResponse(content=plan_good, usage=TokenUsage(input_tokens=1, output_tokens=1)),
        ChatResponse(content="step result", usage=TokenUsage(input_tokens=1, output_tokens=1)),
        ChatResponse(content="final", usage=TokenUsage(input_tokens=1, output_tokens=1)),
    ]
    runtime = AgentRuntime(agent, provider=_Scripted(scripted))
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "p2", "role": "ROLE_USER", "parts": [{"text": "go"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "final" in final["statusUpdate"]["status"]["message"]["parts"][0]["text"]
