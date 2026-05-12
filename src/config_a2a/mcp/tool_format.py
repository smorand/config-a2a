"""Translate MCP tool descriptors to provider-specific tool schemas."""

from __future__ import annotations

from typing import Any

from config_a2a.providers.base import ToolSpec


def to_openai_tool(tool_name: str, mcp_tool: Any) -> ToolSpec:
    """Convert an MCP `Tool` (from the mcp SDK) into an OpenAI-shaped `ToolSpec`."""
    parameters = getattr(mcp_tool, "inputSchema", None) or {"type": "object", "properties": {}}
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    description = getattr(mcp_tool, "description", "") or ""
    return ToolSpec(name=tool_name, description=description, parameters=parameters)


def annotation(mcp_tool: Any, key: str, default: bool = False) -> bool:
    """Return `annotations.<key>` from an MCP tool (e.g. destructiveHint)."""
    annotations = getattr(mcp_tool, "annotations", None)
    if annotations is None:
        return default
    raw = getattr(annotations, key, None)
    if raw is None and isinstance(annotations, dict):
        raw = annotations.get(key)
    return bool(default if raw is None else raw)
