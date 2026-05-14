"""Multi-agent loader invariants: slug, inheritance, admin gating."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_a2a.config.loader import ConfigError, load_server_config
from config_a2a.config.models import (
    AgentConfig,
    AuthenticationConfig,
    ModelConfig,
    PersistenceConfig,
    ServerConfig,
    SimplePattern,
    slugify,
)


def test_slugify_basics() -> None:
    assert slugify("Simple Agent") == "simple-agent"
    assert slugify("  My_Cool  Agent  ") == "my-cool-agent"
    assert slugify("Mixed-Case-123") == "mixed-case-123"


def _agent(name: str, slug: str | None = None) -> AgentConfig:
    return AgentConfig(
        slug=slug,
        name=name,
        model=ModelConfig(provider="openai-compatible", model="m"),
        pattern=SimplePattern(),
    )


def test_slug_defaults_to_slugified_name() -> None:
    agent = _agent("My Agent")
    assert agent.slug == "my-agent"


def test_slug_rejected_when_malformed() -> None:
    with pytest.raises(ValueError, match="slug"):
        _agent("Whatever", slug="Bad Slug")


def test_slug_required_when_name_is_empty_slug() -> None:
    with pytest.raises(ValueError):
        _agent("###")


def test_duplicate_slug_rejected() -> None:
    a1 = _agent("first", slug="x")
    a2 = _agent("second", slug="x")
    with pytest.raises(ValueError, match="duplicate"):
        ServerConfig(name="s", agents=[a1, a2])


def test_empty_agents_ok_when_admin_enabled() -> None:
    server = ServerConfig(name="s", agents=[])
    assert server.admin.enabled is True
    assert server.agents == []


def test_empty_agents_rejected_when_admin_disabled() -> None:
    from config_a2a.config.models import AdminConfig

    with pytest.raises(ValueError, match="inert"):
        ServerConfig(name="s", agents=[], admin=AdminConfig(enabled=False))


def test_inheritance_fills_persistence_and_authentication() -> None:
    agent = _agent("agent-one")
    assert agent.persistence is None
    assert agent.authentication is None
    server = ServerConfig(
        name="s",
        persistence=PersistenceConfig(url="sqlite+aiosqlite:///./state/foo.db"),
        agents=[agent],
    )
    # The validator filled in the defaults.
    assert agent.persistence is not None
    assert agent.persistence.url == "sqlite+aiosqlite:///./state/foo.db"
    assert agent.authentication is not None
    assert agent.authentication.type == "none"
    assert isinstance(server.agents[0].persistence, PersistenceConfig)


def test_agent_can_override_persistence() -> None:
    custom = PersistenceConfig(url="sqlite+aiosqlite:///./state/agent-private.db")
    agent = _agent("agent-one")
    agent.persistence = custom
    server = ServerConfig(name="s", agents=[agent])
    # Overrides win.
    assert server.agents[0].persistence is custom


def test_agent_can_override_authentication() -> None:
    auth = AuthenticationConfig(type="bearer", value_env="TOKEN_X")
    agent = _agent("agent-one")
    agent.authentication = auth
    server = ServerConfig(name="s", agents=[agent])
    assert server.agents[0].authentication is auth


def test_load_handoff_example_three_agents() -> None:
    path = Path(__file__).resolve().parents[2] / "config_examples" / "04-handoff" / "server.yaml"
    server = load_server_config(path)
    slugs = [a.slug for a in server.agents]
    assert slugs == ["router", "math", "chat"]


def test_load_coding_agent_example_nine_agents() -> None:
    path = Path(__file__).resolve().parents[2] / "config_examples" / "08-coding-agent" / "server.yaml"
    server = load_server_config(path)
    assert len(server.agents) == 9
    assert {a.slug for a in server.agents} == {
        "classification",
        "dor",
        "comprehension",
        "planning",
        "e2e-writing",
        "implementation",
        "review",
        "pr-creation",
        "orchestrator",
    }


def test_server_card_provider_round_trip() -> None:
    yaml_text = """
name: s
card:
  provider:
    organization: IBM
    url: https://ibm.com
agents:
  - name: a
    model: {provider: openai-compatible, model: m}
    pattern: {type: simple}
"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fp:
        fp.write(yaml_text)
        path = Path(fp.name)
    try:
        server = load_server_config(path)
        assert server.card.provider is not None
        assert server.card.provider.organization == "IBM"
    finally:
        path.unlink()


def test_extra_keys_rejected_on_agent(tmp_path: Path) -> None:
    yaml_text = """
name: s
agents:
  - name: a
    model: {provider: openai-compatible, model: m}
    pattern: {type: simple}
    bogus_field: 1
"""
    path = tmp_path / "server.yaml"
    path.write_text(yaml_text)
    with pytest.raises(ConfigError):
        load_server_config(path)
