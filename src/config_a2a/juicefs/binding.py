# pylint: disable=cyclic-import
# The graph edges pylint reports on this file (config.models <-> juicefs.binding, and,
# per a pylint quirk, also patterns <-> patterns.handoff even though this file is not part of
# that cycle) are deliberate: both use a function-scoped (lazy) import specifically to break the
# *runtime* cycle; pylint's static check still reports the edge and always attaches the message
# to this file regardless of which modules are actually involved. See the import-outside-toplevel
# opt-outs in config/models.py and patterns/handoff.py for the actual lazy-import breakpoints.
"""Desugar a ``juicefs:`` block into a concrete MCP streamable-HTTP server.

The runtime stays 100% MCP-over-HTTP: there is no ``libjfs`` / JuiceFS SDK
dependency here, so config-a2a remains cross-platform. All that happens is a
``JuiceFSConfig`` is translated into an ``McpStreamableHttpServer`` with
per-request identity forwarding enabled, plus a small system-prompt fragment
that teaches the model the ``mount_id`` convention.
"""

from __future__ import annotations

from pathlib import Path

from config_a2a.config.juicefs import JuiceFSConfig
from config_a2a.config.models import McpStreamableHttpServer, ServerIdentityConfig, ToolFilters


def compile_juicefs(
    juicefs: JuiceFSConfig,
    *,
    server_identity: ServerIdentityConfig | None = None,
) -> McpStreamableHttpServer:
    """Translate a ``juicefs:`` block into a JWT identity-forwarding MCP server.

    Identity is server-wide and JWT-only. On a tool call the verified
    ``Bearer <jwt>`` of the end user is re-forwarded on ``identity_header``; on
    discovery (no end user) the static service credential (``Bearer <service
    token>``) is used instead. ``server_identity`` supplies the JWT ``header``
    and the ``service_token_path``. When it is omitted (standalone agent
    validation, before the server-level pass folds in ``ServerConfig.identity``)
    the header defaults to ``X-Forwarded-Authorization`` and no service
    credential is set.
    """
    header = "X-Forwarded-Authorization"
    service_credential: str | None = None
    if server_identity is not None:
        header = server_identity.header
        if server_identity.service_token_path:
            token = Path(server_identity.service_token_path).read_text(encoding="utf-8").strip()
            service_credential = f"Bearer {token}"
    return McpStreamableHttpServer(
        name=juicefs.name,
        url=juicefs.url,
        headers={},
        forward_identity=True,
        identity_header=header,
        service_credential=service_credential,
    )


def _dedup(*sources: list[str]) -> list[str]:
    """Concatenate the given pattern lists, dropping duplicates, keeping order."""
    seen: set[str] = set()
    out: list[str] = []
    for source in sources:
        for pattern in source:
            if pattern not in seen:
                seen.add(pattern)
                out.append(pattern)
    return out


def merge_filters(base: ToolFilters, extra: ToolFilters) -> ToolFilters:
    """Union ``extra`` (the ``juicefs.filters``) into ``base`` (``tools.filters``).

    ``ToolFilters`` semantics: ``include`` is an OR allowlist (a tool passes when
    it matches *any* include pattern, or when ``include`` is empty), ``exclude``
    is an OR denylist. The coherent merge is therefore a deduplicated union of
    both lists. The operation is idempotent: re-merging already-merged filters
    yields the same result.
    """
    return ToolFilters(
        include=_dedup(base.include, extra.include),
        exclude=_dedup(base.exclude, extra.exclude),
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


__all__ = ["compile_juicefs", "juicefs_prompt_suffix", "merge_filters"]
