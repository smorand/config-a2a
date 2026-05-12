"""Tests for the YAML configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_a2a.loader import ConfigLoadError, load_agent_config


def test_load_example_agent() -> None:
    """The bundled example agent must load and validate cleanly."""
    config = load_agent_config(Path("examples/agent.yaml"))
    assert config.name == "example-agent"
    assert len(config.skills) == 2
    assert config.skills[0].name == "greet"


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigLoadError):
        load_agent_config(tmp_path / "missing.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: [unterminated", encoding="utf-8")
    with pytest.raises(ConfigLoadError):
        load_agent_config(bad)
