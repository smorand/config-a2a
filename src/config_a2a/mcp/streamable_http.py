"""MCP streamable-HTTP transport (spec 2025-03-26 — replaces deprecated SSE)."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from config_a2a.config.models import McpStreamableHttpServer
from config_a2a.identity import current_credential
from config_a2a.mcp.stdio import StdioToolDescriptor


def _request_headers(server: McpStreamableHttpServer, *, discovery: bool) -> dict[str, str]:
    """Build outbound headers, injecting the forwarded JWT credential when enabled.

    On a tool *call* the current end user's pass-through ``Bearer <jwt>`` is
    forwarded; during *discovery* there is no end user, so the static
    ``service_credential`` (``Bearer <service token>``) is used so ``list_tools``
    passes the downstream auth middleware. A ``None`` value sets no header.
    """
    headers = dict(server.headers)
    if not server.forward_identity:
        return headers
    value = server.service_credential if discovery else current_credential()
    if value:
        headers[server.identity_header] = value
    return headers


@asynccontextmanager
async def _open_session(server: McpStreamableHttpServer, *, discovery: bool = False) -> AsyncIterator[ClientSession]:
    headers = _request_headers(server, discovery=discovery)
    async with streamablehttp_client(url=server.url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def discover_tools(server: McpStreamableHttpServer) -> list[StdioToolDescriptor]:
    async def _list() -> list[StdioToolDescriptor]:
        async with _open_session(server, discovery=True) as session:
            response = await session.list_tools()
        return [_descriptor(server.name, tool) for tool in response.tools]

    return await asyncio.wait_for(_list(), timeout=15.0)


async def call_tool(server: McpStreamableHttpServer, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with _open_session(server) as session:
        result = await session.call_tool(tool_name, arguments=arguments)
    chunks: list[str] = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            chunks.append(text)
    return {"isError": bool(getattr(result, "isError", False)), "text": "\n".join(chunks)}


def _descriptor(server_name: str, tool: Any) -> StdioToolDescriptor:
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
    return StdioToolDescriptor(
        qualified_name=f"{server_name}.{tool.name}",
        raw_name=tool.name,
        server_name=server_name,
        description=getattr(tool, "description", "") or "",
        input_schema=schema if isinstance(schema, dict) else {},
        annotations=annotations_dict,
    )
