"""Unified MCP client: discovers tools across all configured transports."""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Any

from config_a2a.config.models import (
    McpServer,
    McpSseServer,
    McpStdioServer,
    McpStreamableHttpServer,
    ToolFilters,
)
from config_a2a.mcp.sse import call_tool as call_sse_tool, discover_tools as discover_sse, warn_deprecated
from config_a2a.mcp.stdio import StdioToolDescriptor, call_tool as call_stdio_tool, discover_tools as discover_stdio
from config_a2a.mcp.streamable_http import (
    call_tool as call_streamable_tool,
    discover_tools as discover_streamable,
)
from config_a2a.providers.base import ToolSpec

log = logging.getLogger(__name__)


@dataclass
class McpToolHandle:
    """Everything the runtime needs to surface and dispatch one MCP tool."""

    spec: ToolSpec
    server: McpServer
    raw_name: str  # name on the MCP server (without the `<server>.` prefix)
    descriptor: StdioToolDescriptor


class McpRegistry:
    """Discovers tools at startup and dispatches `call_tool` to the right transport."""

    def __init__(self) -> None:
        self._handles: dict[str, McpToolHandle] = {}

    @property
    def specs(self) -> list[ToolSpec]:
        return [handle.spec for handle in self._handles.values()]

    @property
    def handles(self) -> dict[str, McpToolHandle]:
        return self._handles

    async def discover(self, servers: list[McpServer], filters: ToolFilters) -> None:
        for server in servers:
            try:
                discovered = await _discover_for(server)
            except Exception as exc:  # pylint: disable=broad-except
                log.warning("MCP discovery failed for %s: %s", server.name, exc)
                continue
            for descriptor in discovered:
                if not _passes(descriptor.qualified_name, filters):
                    continue
                spec = ToolSpec(
                    name=descriptor.qualified_name,
                    description=descriptor.description,
                    parameters=descriptor.input_schema or {"type": "object", "properties": {}},
                )
                self._handles[descriptor.qualified_name] = McpToolHandle(
                    spec=spec,
                    server=server,
                    raw_name=descriptor.raw_name,
                    descriptor=descriptor,
                )

    async def aclose(self) -> None:
        """Release any held resources. Current transports are connection-less."""
        self._handles.clear()

    async def call(self, qualified_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handle = self._handles.get(qualified_name)
        if handle is None:
            return {"isError": True, "text": f"tool '{qualified_name}' is not registered"}
        server = handle.server
        if isinstance(server, McpStdioServer):
            return await call_stdio_tool(server, handle.raw_name, arguments)
        if isinstance(server, McpStreamableHttpServer):
            return await call_streamable_tool(server, handle.raw_name, arguments)
        if isinstance(server, McpSseServer):
            return await call_sse_tool(server, handle.raw_name, arguments)
        return {"isError": True, "text": f"unknown transport: {server.transport}"}


async def _discover_for(server: McpServer) -> list[StdioToolDescriptor]:
    if isinstance(server, McpStdioServer):
        return await discover_stdio(server)
    if isinstance(server, McpStreamableHttpServer):
        return await discover_streamable(server)
    if isinstance(server, McpSseServer):
        warn_deprecated(server.name)
        return await discover_sse(server)
    return []


def _passes(qualified_name: str, filters: ToolFilters) -> bool:
    if filters.include and not any(fnmatch.fnmatch(qualified_name, pattern) for pattern in filters.include):
        return False
    if any(fnmatch.fnmatch(qualified_name, pattern) for pattern in filters.exclude):
        return False
    return True
