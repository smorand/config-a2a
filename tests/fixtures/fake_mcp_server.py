"""Minimal stdio MCP server used by Iter 4 tests.

Exposes:
- ``echo`` — read-only, idempotent; returns the input text.
- ``delete_file`` — destructive (annotations.destructiveHint=True); returns the path.
"""

from __future__ import annotations

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool, ToolAnnotations

server = Server("fake-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="echo",
            description="Echoes the provided text.",
            inputSchema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
        ),
        Tool(
            name="delete_file",
            description="Deletes a file (simulated).",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            annotations=ToolAnnotations(destructiveHint=True),
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, str]) -> list[TextContent]:
    if name == "echo":
        return [TextContent(type="text", text=f"echoed: {arguments.get('text', '')}")]
    if name == "delete_file":
        return [TextContent(type="text", text=f"deleted: {arguments.get('path', '')}")]
    raise ValueError(f"unknown tool: {name}")


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
