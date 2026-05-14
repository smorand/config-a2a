"""Loader, prompts resolver, env substitution, A2A card."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config_a2a.a2a.card import build_agent_card
from config_a2a.config.loader import ConfigError, load_server_config
from config_a2a.config.prompts import resolve_system_prompt
from tests.unit.conftest import example_yaml


def test_example_simple_loads() -> None:
    server = load_server_config(example_yaml("01-simple"))
    assert server.name == "simple-server"
    assert len(server.agents) == 1
    agent = server.agents[0]
    assert agent.slug == "simple"
    assert agent.pattern.type == "simple"
    assert agent.model.provider == "openai-compatible"
    assert agent.prompts.system_file is not None
    assert Path(agent.prompts.system_file).is_file()


def test_missing_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_server_config(tmp_path / "nope.yaml")


def test_env_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_PROVIDER_KEY", "MY_PROVIDER_KEY")
    yaml_text = """
name: tserver
agents:
  - slug: t
    name: t
    model:
      provider: openai-compatible
      model: a
      api_key_env: ${MY_PROVIDER_KEY}
      base_url: ${MY_PROVIDER_KEY}-base
    pattern:
      type: simple
"""
    target = tmp_path / "server.yaml"
    target.write_text(yaml_text, encoding="utf-8")
    server = load_server_config(target)
    agent = server.agents[0]
    assert agent.model.api_key_env == "MY_PROVIDER_KEY"
    assert agent.model.base_url == "MY_PROVIDER_KEY-base"


def test_unknown_pattern_rejected(tmp_path: Path) -> None:
    target = tmp_path / "bad.yaml"
    target.write_text(
        """
name: tserver
agents:
  - slug: t
    name: t
    model: {provider: openai-compatible, model: a}
    pattern:
      type: bogus
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_server_config(target)


def test_extra_keys_rejected(tmp_path: Path) -> None:
    target = tmp_path / "extra.yaml"
    target.write_text(
        """
name: tserver
unknown: 1
agents:
  - slug: t
    name: t
    model: {provider: openai-compatible, model: a}
    pattern: {type: simple}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_server_config(target)


def test_prompt_resolver_inline_wins(tmp_path: Path) -> None:
    text = resolve_system_prompt("hi", tmp_path / "missing.md", default="default")
    assert text == "hi"


def test_prompt_resolver_file(tmp_path: Path) -> None:
    f = tmp_path / "p.md"
    f.write_text("from file", encoding="utf-8")
    assert resolve_system_prompt(None, f, default="d") == "from file"


def test_prompt_resolver_default() -> None:
    assert resolve_system_prompt(None, None, default="fallback") == "fallback"


def test_agent_card_shape() -> None:
    server = load_server_config(example_yaml("01-simple"))
    agent = server.agents[0]
    card = build_agent_card(agent, "http://localhost:9001/agents/simple", server_card=server.card)
    assert card["name"] == "simple-assistant"
    assert card["capabilities"]["streaming"] is True
    assert any(s["id"] == "chat" for s in card["skills"])
    assert card["url"] == "http://localhost:9001/agents/simple"
    assert "securitySchemes" not in card  # authentication.type == none


# Ensure default env var doesn't leak into other tests.
os.environ.pop("MY_PROVIDER_KEY", None)
