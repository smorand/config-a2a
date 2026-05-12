"""YAML loader with ${ENV} substitution and relative-path resolution."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from config_a2a.config.models import AgentConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


class ConfigError(Exception):
    """Raised when a configuration file cannot be loaded or validated."""


def _substitute_env(value: Any) -> Any:
    """Recursively expand ${VAR} references inside string leaves."""
    if isinstance(value, str):

        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            return os.environ.get(name, match.group(0))

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [_substitute_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _substitute_env(item) for key, item in value.items()}
    return value


def _resolve_paths(value: Any, base_dir: Path) -> Any:
    """Make every `*_file` and `agent_ref` leaf absolute against ``base_dir``."""
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}
        for key, child in value.items():
            child = _resolve_paths(child, base_dir)
            if isinstance(child, str) and (key.endswith("_file") or key in {"agent_ref", "jsonl_path"}):
                candidate = Path(child)
                if not candidate.is_absolute():
                    candidate = (base_dir / candidate).resolve()
                resolved[key] = str(candidate)
            else:
                resolved[key] = child
        return resolved
    if isinstance(value, list):
        return [_resolve_paths(item, base_dir) for item in value]
    return value


def load_agent_config(path: Path) -> AgentConfig:
    """Load and validate an agent configuration from ``path``."""
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Top-level YAML must be a mapping in {path}, got {type(raw).__name__}")
    base_dir = path.parent.resolve()
    raw = _substitute_env(raw)
    raw = _resolve_paths(raw, base_dir)
    try:
        return AgentConfig.model_validate(raw)
    except Exception as exc:  # pylint: disable=broad-except
        raise ConfigError(f"Invalid configuration in {path}: {exc}") from exc
