"""Debate + Tree of Thoughts via scripted provider."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent


class _Cycling(LlmProvider):
    name = "cycling"

    def __init__(self, queue: list[ChatResponse]) -> None:
        self._queue = list(queue)
        self.calls: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if self._queue:
            return self._queue.pop(0)
        return ChatResponse(content="ok", usage=TokenUsage())

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


def test_debate_completes_with_verdict() -> None:
    _, agent, prefix = load_single_agent("06-debate")
    scripts = [ChatResponse(content=f"argument-{i}", usage=TokenUsage()) for i in range(4)]
    scripts.append(ChatResponse(content="pro wins because of argument-0", usage=TokenUsage()))
    runtime = AgentRuntime(agent, provider=_Cycling(scripts))
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "d1", "role": "ROLE_USER", "parts": [{"text": "should cats be staff?"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "wins" in final["statusUpdate"]["status"]["message"]["parts"][0]["text"]


def test_tree_of_thoughts_picks_highest_scored_path() -> None:
    _, agent, prefix = load_single_agent("07-tree-of-thoughts")
    queue: list[ChatResponse] = []
    queue.extend(ChatResponse(content=f"d1-cand-{i}") for i in range(3))
    queue.extend(ChatResponse(content=f"{8 - i}: rationale") for i in range(3))
    queue.extend(ChatResponse(content=f"d2-cand-{i}") for i in range(6))
    queue.extend(ChatResponse(content=f"{9 - i}: rationale") for i in range(6))
    queue.append(ChatResponse(content="picked the strongest path"))
    runtime = AgentRuntime(agent, provider=_Cycling(queue))
    client = TestClient(create_app_for_runtime(runtime))
    with client.stream(
        "POST",
        f"{prefix}/message:stream",
        json={"message": {"messageId": "t1", "role": "ROLE_USER", "parts": [{"text": "design X"}]}},
    ) as response:
        body = "".join(response.iter_text())
    final = _final(body)
    assert final["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
    assert "strongest path" in final["statusUpdate"]["status"]["message"]["parts"][0]["text"]
