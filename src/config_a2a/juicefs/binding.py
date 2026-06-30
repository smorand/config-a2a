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
    """Translate a ``juicefs:`` block into an identity-forwarding MCP server.

    ``server_identity`` carries the server-wide mode. When it is ``None`` or in
    ``forwarded_user`` mode the bare end-user email is re-forwarded on the
    ``forwarded_user_header`` (discovery falls back to ``service_identity``). In
    ``jwt`` mode the verified ``Bearer <jwt>`` credential is forwarded on the
    JWT header instead, and discovery uses the static service token.
    """
    if server_identity is not None and server_identity.mode == "jwt":
        jwt_config = server_identity.jwt
        assert jwt_config is not None  # guaranteed by ServerIdentityConfig validator
        service_credential: str | None = None
        if jwt_config.service_token_path:
            token = Path(jwt_config.service_token_path).read_text(encoding="utf-8").strip()
            service_credential = f"Bearer {token}"
        return McpStreamableHttpServer(
            name=juicefs.name,
            url=juicefs.url,
            headers={},
            forward_identity=True,
            identity_mode="jwt",
            identity_header=jwt_config.header,
            service_credential=service_credential,
        )
    return McpStreamableHttpServer(
        name=juicefs.name,
        url=juicefs.url,
        headers={},
        forward_identity=True,
        identity_mode="forwarded_user",
        identity_header=juicefs.identity.forwarded_user_header,
        service_identity=juicefs.service_identity,
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
