"""Legacy MCP SSE transport.

This transport is deprecated by the 2025-03-26 MCP spec in favour of
streamable HTTP. We support it here for compatibility with older servers
(Keboola until 2026-04-01, Atlassian until 2026-06-30) and emit a warning
when an agent is configured to use it.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.sse import sse_client

from config_a2a.config.models import McpSseServer
from config_a2a.mcp.stdio import StdioToolDescriptor, call_tool_via_session, list_tools_via_session

log = logging.getLogger(__name__)


def warn_deprecated(server_name: str) -> None:
    message = (
        f"MCP server '{server_name}' uses the deprecated SSE transport. "
        "Migrate to streamable-http (MCP spec 2025-03-26)."
    )
    log.warning(message)
    warnings.warn(message, DeprecationWarning, stacklevel=2)


@asynccontextmanager
async def _open_session(server: McpSseServer) -> AsyncIterator[ClientSession]:
    async with sse_client(url=server.url, headers=dict(server.headers)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def discover_tools(server: McpSseServer) -> list[StdioToolDescriptor]:
    warn_deprecated(server.name)

    async def _list() -> list[StdioToolDescriptor]:
        async with _open_session(server) as session:
            return await list_tools_via_session(server.name, session)

    return await asyncio.wait_for(_list(), timeout=15.0)


async def call_tool(server: McpSseServer, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _open_session(server) as session:
        return await call_tool_via_session(session, tool_name, arguments)
