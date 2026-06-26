"""Native JuiceFS support: desugar a ``juicefs:`` block into an MCP server."""

from __future__ import annotations

from config_a2a.juicefs.binding import compile_juicefs, juicefs_prompt_suffix

__all__ = ["compile_juicefs", "juicefs_prompt_suffix"]
