"""Resolve `prompt:` inline vs `prompt_file:` references to plain strings."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class _HasPromptFields(Protocol):
    prompt: str | None
    prompt_file: Path | None


def resolve_prompt(holder: _HasPromptFields | None, default: str = "") -> str:
    """Return inline prompt if set, otherwise load the file, otherwise default."""
    if holder is None:
        return default
    if holder.prompt is not None:
        return holder.prompt
    if holder.prompt_file is not None:
        return Path(holder.prompt_file).read_text(encoding="utf-8")
    return default


def resolve_system_prompt(system: str | None, system_file: Path | None, default: str = "") -> str:
    """Same logic for the top-level prompts.system / system_file pair."""
    if system is not None:
        return system
    if system_file is not None:
        return Path(system_file).read_text(encoding="utf-8")
    return default
