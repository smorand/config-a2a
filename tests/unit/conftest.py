"""Shared test helpers for the unit suite.

Adds an ``agent_path`` helper that returns the path to a per-example
``server.yaml`` so tests can stay terse, and a ``load_single_agent`` helper
that yields the (server, agent, prefix) tuple used by most test files.
"""

from __future__ import annotations

from pathlib import Path

from config_a2a.config.loader import load_server_config
from config_a2a.config.models import AgentConfig, ServerConfig

EXAMPLES = Path(__file__).resolve().parents[2] / "config_examples"


def example_yaml(name: str) -> Path:
    return EXAMPLES / name / "server.yaml"


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
