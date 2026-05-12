"""Iter 1: config loader, prompts resolver, env substitution, A2A card."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config_a2a.a2a.card import build_agent_card
from config_a2a.config.loader import ConfigError, load_agent_config
from config_a2a.config.prompts import resolve_system_prompt

EXAMPLE = Path(__file__).resolve().parents[2] / "config_examples" / "01-simple" / "agent.yaml"


def test_example_simple_loads() -> None:
    config = load_agent_config(EXAMPLE)
    assert config.name == "simple-assistant"
    assert config.pattern.type == "simple"
    assert config.model.provider == "openai-compatible"
    # prompts.system_file is resolved to an absolute path.
    assert config.prompts.system_file is not None
    assert Path(config.prompts.system_file).is_file()


def test_missing_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_agent_config(tmp_path / "nope.yaml")


def test_env_substitution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_PROVIDER_KEY", "MY_PROVIDER_KEY")  # value of the env var; the YAML literal is ${...}
    yaml_text = """
name: t
model:
  provider: openai-compatible
  model: a
  api_key_env: ${MY_PROVIDER_KEY}
  base_url: ${MY_PROVIDER_KEY}-base
pattern:
  type: simple
"""
    target = tmp_path / "agent.yaml"
    target.write_text(yaml_text, encoding="utf-8")
    config = load_agent_config(target)
    assert config.model.api_key_env == "MY_PROVIDER_KEY"
    assert config.model.base_url == "MY_PROVIDER_KEY-base"


def test_unknown_pattern_rejected(tmp_path: Path) -> None:
    target = tmp_path / "bad.yaml"
    target.write_text(
        "name: t\nmodel: {provider: openai-compatible, model: a}\npattern:\n  type: bogus\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_agent_config(target)


def test_extra_keys_rejected(tmp_path: Path) -> None:
    target = tmp_path / "extra.yaml"
    target.write_text(
        "name: t\nmodel: {provider: openai-compatible, model: a}\npattern: {type: simple}\nunknown: 1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_agent_config(target)


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
    config = load_agent_config(EXAMPLE)
    card = build_agent_card(config, "http://localhost:9001/")
    assert card["name"] == "simple-assistant"
    assert card["capabilities"]["streaming"] is True
    assert any(s["id"] == "chat" for s in card["skills"])
    assert card["url"] == "http://localhost:9001"
    assert "securitySchemes" not in card  # authentication.type == none

# Ensure default env var doesn't leak into other tests.
os.environ.pop("MY_PROVIDER_KEY", None)
