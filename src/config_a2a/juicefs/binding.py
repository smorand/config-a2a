"""Desugar a ``juicefs:`` block into a concrete MCP streamable-HTTP server.

The runtime stays 100% MCP-over-HTTP: there is no ``libjfs`` / JuiceFS SDK
dependency here, so config-a2a remains cross-platform. All that happens is a
``JuiceFSConfig`` is translated into an ``McpStreamableHttpServer`` with
per-request identity forwarding enabled, plus a small system-prompt fragment
that teaches the model the ``mount_id`` convention.
"""

from __future__ import annotations

from config_a2a.config.juicefs import JuiceFSConfig
from config_a2a.config.models import McpStreamableHttpServer


def compile_juicefs(juicefs: JuiceFSConfig) -> McpStreamableHttpServer:
    """Translate a ``juicefs:`` block into an identity-forwarding MCP server."""
    return McpStreamableHttpServer(
        name=juicefs.name,
        url=juicefs.url,
        headers={},
        forward_identity=True,
        identity_header=juicefs.identity.forwarded_user_header,
        service_identity=juicefs.service_identity,
    )


def juicefs_prompt_suffix(*, default_mount_id: str | None) -> str:
    """Return the system-prompt fragment teaching the ``mount_id`` convention.

    When ``default_mount_id`` is set it is presented as the model's *current
    project*; the model stays free to switch to any other accessible volume.
    """
    lines = [
        "## JuiceFS file storage",
        (
            "File tools are exposed under the `fs.*` namespace and operate on a "
            "JuiceFS volume identified by an explicit `mount_id` argument. A user "
            "may have several volumes (personal, per-project, ...)."
        ),
        (
            "If you do not know which `mount_id` to use, call `fs.list_allowed_roots` "
            "to list the volumes you can access, then use the right one or ask the user."
        ),
    ]
    if default_mount_id:
        lines.append(
            f'Your current project is `mount_id = "{default_mount_id}"`; use it for '
            "`fs.*` calls unless the user asks for another volume."
        )
    return "\n".join(lines)


__all__ = ["compile_juicefs", "juicefs_prompt_suffix"]
