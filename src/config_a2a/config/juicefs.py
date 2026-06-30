"""``juicefs:`` agent block — native, ergonomic config for an mcp-juicefs server.

This is sugar over a ``streamable-http`` MCP server entry (see
``config_a2a.juicefs.binding``). It captures the two things that make
mcp-juicefs different from a generic MCP server:

* **identity** is forwarded **per request** (the end user, not a static token).
  Identity is entirely server-wide and JWT-based (see ``ServerIdentityConfig``);
  the ``juicefs:`` block carries no identity settings of its own.
* a **``default_mount_id``** can be surfaced to the model as its current
  project (a user usually has several volumes).

Design decisions are recorded in ``specs/juicefs-integration.md``.
"""

from __future__ import annotations

from pydantic import Field

from config_a2a.config.models import ToolFilters, _Strict


class JuiceFSConfig(_Strict):
    """Native ``juicefs:`` block on an agent.

    Attributes:
        url: mcp-juicefs streamable-HTTP endpoint (e.g. ``http://host:8000/mcp``).
        name: MCP server name; tools are surfaced as ``<name>.fs.*``. Defaults
            to ``juicefs``.
        default_mount_id: optional "current project" volume injected into the
            system prompt. The agent stays free to switch via
            ``fs.list_allowed_roots``. May also be overridden per message via
            A2A message metadata (``mount_id``).
        filters: optional include/exclude tool filters applied to the server.

    End-user identity (the inbound JWT verified at the A2A boundary and the
    service token used for tool discovery) comes from the server-wide
    ``identity:`` block, not from here.
    """

    url: str
    name: str = "juicefs"
    default_mount_id: str | None = None
    filters: ToolFilters = Field(default_factory=ToolFilters)


__all__ = ["JuiceFSConfig"]
