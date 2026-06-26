"""Per-request end-user identity propagation.

config-a2a forwards the identity of the **end user** (the person on whose behalf
an agent acts) to downstream MCP servers that enforce per-user access control,
most notably ``mcp-juicefs``. The identity is captured once at the A2A boundary
(an ASGI middleware reads a trusted header, ``X-Forwarded-User`` by default) and
stored in a :class:`~contextvars.ContextVar`. Outbound MCP transports read it
without threading the value through every call.

This mirrors ``mcp_juicefs.identity`` on the producer side: a trusted-network v1
that re-forwards ``X-Forwarded-User``. A future switch to a re-signed JWT would
only change how the value is captured and emitted, not this propagation seam.

Pure ASGI middleware is used (not ``BaseHTTPMiddleware``) so the context var
propagates correctly into the request task.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

from starlette.datastructures import Headers

if TYPE_CHECKING:  # pragma: no cover
    from starlette.types import ASGIApp, Receive, Scope, Send

DEFAULT_FORWARDED_USER_HEADER = "X-Forwarded-User"

_current_user: ContextVar[str | None] = ContextVar("config_a2a_current_user", default=None)


def current_user() -> str | None:
    """Return the end-user bound to the in-flight request, or ``None``."""
    return _current_user.get()


def bind_user(person: str | None) -> Token[str | None]:
    """Bind ``person`` for the current context; return the reset token."""
    return _current_user.set(person)


def reset_user(token: Token[str | None]) -> None:
    """Restore the identity context to its previous value."""
    _current_user.reset(token)


class IdentityCaptureMiddleware:  # pylint: disable=too-few-public-methods
    """ASGI middleware binding the inbound user header to the identity context.

    The value is taken verbatim from a trusted header (default
    ``X-Forwarded-User``). When the header is absent the context is bound to
    ``None`` (discovery / anonymous calls fall back to a service identity).
    """

    __slots__ = ("_app", "_header")

    def __init__(self, app: ASGIApp, header_name: str = DEFAULT_FORWARDED_USER_HEADER) -> None:
        self._app = app
        self._header = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        person = Headers(scope=scope).get(self._header, "").strip() or None
        token = bind_user(person)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_user(token)


__all__ = [
    "DEFAULT_FORWARDED_USER_HEADER",
    "IdentityCaptureMiddleware",
    "bind_user",
    "current_user",
    "reset_user",
]
