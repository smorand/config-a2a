"""Iter 4: stdio MCP discovery + tool call + filter + destructiveHint annotation."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from config_a2a.config.models import McpStdioServer, ToolFilters
from config_a2a.mcp.client import McpRegistry
from config_a2a.mcp.stdio import discover_tools

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "fake_mcp_server.py"


def _server(name: str = "fake") -> McpStdioServer:
    return McpStdioServer(
        name=name,
        command=sys.executable,
        args=[str(FIXTURE)],
        env={},
        discovery_timeout_seconds=15.0,
    )


async def test_discover_tools_lists_two() -> None:
    descriptors = await discover_tools(_server())
    names = sorted(d.raw_name for d in descriptors)
    assert names == ["delete_file", "echo"]
    delete = next(d for d in descriptors if d.raw_name == "delete_file")
    assert delete.annotations.get("destructiveHint") is True
    echo = next(d for d in descriptors if d.raw_name == "echo")
    assert echo.annotations.get("readOnlyHint") is True
    assert echo.annotations.get("destructiveHint", False) is False


async def test_registry_call_tool_roundtrip() -> None:
    registry = McpRegistry()
    await registry.discover([_server("fake")], ToolFilters())
    names = sorted(registry.handles)
    assert names == ["fake.delete_file", "fake.echo"]
    result = await registry.call("fake.echo", {"text": "hello"})
    assert result == {"isError": False, "text": "echoed: hello"}


async def test_registry_filters_include_exclude() -> None:
    registry = McpRegistry()
    await registry.discover(
        [_server("fake")],
        ToolFilters(include=["fake.echo"], exclude=[]),
    )
    assert list(registry.handles) == ["fake.echo"]

    registry2 = McpRegistry()
    await registry2.discover(
        [_server("fake")],
        ToolFilters(include=[], exclude=["*delete*"]),
    )
    assert list(registry2.handles) == ["fake.echo"]


async def test_registry_returns_error_for_unknown_tool() -> None:
    registry = McpRegistry()
    await registry.discover([_server("fake")], ToolFilters())
    result = await registry.call("fake.unknown", {})
    assert result["isError"] is True
