"""``juicefs:`` agent block — native, ergonomic config for an mcp-juicefs server.

This is sugar over a ``streamable-http`` MCP server entry (see
``config_a2a.juicefs.binding``). It captures the three things that make
mcp-juicefs different from a generic MCP server:

* **identity** is forwarded **per request** (the end user, not a static token);
* a **``default_mount_id``** can be surfaced to the model as its current
  project (a user usually has several volumes);
* a **``service_identity``** lets tool *discovery* (which has no end user in
  context) pass the downstream auth middleware.

Design decisions are recorded in ``specs/juicefs-integration.md``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from config_a2a.config.models import ToolFilters, _Strict


class JuiceFSIdentityConfig(_Strict):
    """How the end-user identity is forwarded to mcp-juicefs.

    v1 targets a trusted network and re-forwards ``X-Forwarded-User``. The
    ``forwarded_user_header`` names the header used **both** when reading the
    inbound A2A request and when emitting the outbound MCP call (re-forward).
    """

    mode: Literal["forwarded_user"] = "forwarded_user"
    forwarded_user_header: str = "X-Forwarded-User"


class JuiceFSConfig(_Strict):
    """Native ``juicefs:`` block on an agent.

    Attributes:
        url: mcp-juicefs streamable-HTTP endpoint (e.g. ``http://host:8000/mcp``).
        name: MCP server name; tools are surfaced as ``<name>.fs.*``. Defaults
            to ``juicefs``.
        identity: identity-forwarding settings.
        default_mount_id: optional "current project" volume injected into the
            system prompt. The agent stays free to switch via
            ``fs.list_allowed_roots``. May also be overridden per message via
            A2A message metadata (``mount_id``).
        service_identity: identity used for tool discovery at load time (no end
            user in context). Without it, ``list_tools`` is rejected by the
            mcp-juicefs auth middleware.
        filters: optional include/exclude tool filters applied to the server.
    """

    url: str
    name: str = "juicefs"
    identity: JuiceFSIdentityConfig = Field(default_factory=JuiceFSIdentityConfig)
    default_mount_id: str | None = None
    service_identity: str | None = None
    filters: ToolFilters = Field(default_factory=ToolFilters)


__all__ = ["JuiceFSConfig", "JuiceFSIdentityConfig"]
