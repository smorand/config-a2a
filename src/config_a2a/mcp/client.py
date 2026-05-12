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
from config_a2a.mcp.stdio import StdioToolDescriptor, call_tool as call_stdio_tool, discover_tools as discover_stdio
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

    async def call(self, qualified_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        handle = self._handles.get(qualified_name)
        if handle is None:
            return {"isError": True, "text": f"tool '{qualified_name}' is not registered"}
        server = handle.server
        if isinstance(server, McpStdioServer):
            return await call_stdio_tool(server, handle.raw_name, arguments)
        return {
            "isError": True,
            "text": f"transport '{server.transport}' not implemented yet (planned in a later iteration)",
        }


async def _discover_for(server: McpServer) -> list[StdioToolDescriptor]:
    if isinstance(server, McpStdioServer):
        return await discover_stdio(server)
    if isinstance(server, (McpStreamableHttpServer, McpSseServer)):
        log.warning(
            "MCP transport '%s' not implemented yet (planned in a later iteration)",
            server.transport,
        )
        return []
    return []


def _passes(qualified_name: str, filters: ToolFilters) -> bool:
    if filters.include and not any(fnmatch.fnmatch(qualified_name, pattern) for pattern in filters.include):
        return False
    if any(fnmatch.fnmatch(qualified_name, pattern) for pattern in filters.exclude):
        return False
    return True
