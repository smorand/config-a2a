"""Stdio MCP transport: spawn a child process, run discovery, call tools.

Mirrors `lcnc-a2a/mcp_client/stdio.py`:

- environment is scrubbed; only PATH from the parent is forwarded, plus any
  variables the caller passed in `env`,
- discovery is bounded by `discovery_timeout_seconds` (default 10 s),
- spawned PIDs are tracked so tests can assert clean shutdown.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from config_a2a.config.models import McpStdioServer

RECENT_SPAWNED_PIDS: list[int] = []


@dataclass
class StdioToolDescriptor:
    """One MCP tool exposed by a stdio server."""

    qualified_name: str  # "<server>.<tool>"
    raw_name: str  # name on the MCP server
    server_name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]


def _scrub_env(extra: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    env.update(extra)
    return env


@asynccontextmanager
async def _open_session(server: McpStdioServer) -> AsyncIterator[ClientSession]:
    params = StdioServerParameters(command=server.command, args=server.args, env=_scrub_env(server.env))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def discover_tools(server: McpStdioServer) -> list[StdioToolDescriptor]:
    """List tools exposed by ``server`` with a hard ``discovery_timeout_seconds`` cap."""

    async def _list() -> list[StdioToolDescriptor]:
        async with _open_session(server) as session:
            response = await session.list_tools()
            tools = response.tools
        out: list[StdioToolDescriptor] = []
        for tool in tools:
            schema = getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
            annotations_obj = getattr(tool, "annotations", None)
            annotations_dict: dict[str, Any] = {}
            if annotations_obj is not None:
                if isinstance(annotations_obj, dict):
                    annotations_dict = dict(annotations_obj)
                else:
                    for key in ("destructiveHint", "idempotentHint", "readOnlyHint", "openWorldHint"):
                        value = getattr(annotations_obj, key, None)
                        if value is not None:
                            annotations_dict[key] = bool(value)
            out.append(
                StdioToolDescriptor(
                    qualified_name=f"{server.name}.{tool.name}",
                    raw_name=tool.name,
                    server_name=server.name,
                    description=getattr(tool, "description", "") or "",
                    input_schema=schema if isinstance(schema, dict) else {},
                    annotations=annotations_dict,
                )
            )
        return out

    return await asyncio.wait_for(_list(), timeout=server.discovery_timeout_seconds)


async def call_tool(server: McpStdioServer, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke ``tool_name`` on ``server`` and return a JSON-serialisable result."""
    async with _open_session(server) as session:
        result = await session.call_tool(tool_name, arguments=arguments)
    # The mcp SDK returns a CallToolResult; flatten text content into a single string.
    chunks: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            chunks.append(text)
    return {
        "isError": bool(getattr(result, "isError", False)),
        "text": "\n".join(chunks),
    }
