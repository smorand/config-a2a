"""Shared test helpers for the unit suite.

Adds an ``agent_path`` helper that returns the path to a per-example
``agents.yaml`` so tests can stay terse, and a ``load_single_agent`` helper
that yields the (server, agent, prefix) tuple used by most test files.
"""

from __future__ import annotations

from pathlib import Path

from config_a2a.config.loader import load_server_config
from config_a2a.config.models import AgentConfig, ServerConfig
from config_a2a.providers.base import ChatRequest

EXAMPLES = Path(__file__).resolve().parents[2] / "config_examples"


def assert_valid_tool_sequence(request: ChatRequest) -> None:
    """Assert the message history is a valid tool exchange for any provider.

    Every ``tool`` message must immediately follow an ``assistant`` message
    whose ``tool_calls`` declare its ``tool_call_id``. This mirrors the
    constraint OpenAI/Anthropic/Gemini enforce and catches the resume bug where
    a ``tool`` result was appended with no preceding ``assistant`` tool_calls
    turn (a 400 on real providers).
    """
    messages = request.messages
    for i, msg in enumerate(messages):
        if msg.role != "tool":
            continue
        assert i > 0, "tool message cannot be first"
        prev = messages[i - 1]
        assert prev.role == "assistant", f"tool message at {i} not preceded by assistant"
        assert prev.tool_calls, f"assistant before tool message at {i} has no tool_calls"
        assert any(
            tc.id == msg.tool_call_id for tc in prev.tool_calls
        ), f"tool_call_id {msg.tool_call_id!r} not declared by preceding assistant"


def example_yaml(name: str) -> Path:
    return EXAMPLES / name / "agents.yaml"


def load_single_agent(name: str) -> tuple[ServerConfig, AgentConfig, str]:
    """Load the first agent of an example. Returns ``(server, agent, prefix)``.

    ``prefix`` is the URL prefix to use when calling the agent's endpoints.
    """
    server = load_server_config(example_yaml(name))
    if not server.agents:
        raise RuntimeError(f"example {name!r} has no agents")
    agent = server.agents[0]
    assert agent.slug is not None
    return server, agent, f"/agents/{agent.slug}"
