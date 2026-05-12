"""YAML configuration loader for A2A agents."""

from __future__ import annotations

from pathlib import Path

import yaml

from config_a2a.models import AgentConfig


class ConfigLoadError(Exception):
    """Raised when an agent configuration cannot be loaded or parsed."""


def load_agent_config(path: Path) -> AgentConfig:
    """Load and validate an agent configuration from a YAML file."""
    if not path.exists():
        raise ConfigLoadError(f"Configuration file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigLoadError(f"Configuration root must be a mapping, got {type(raw).__name__}")

    return AgentConfig.model_validate(raw)
