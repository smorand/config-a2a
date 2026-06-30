"""Per-request end-user identity propagation (JWT, the only mechanism).

config-a2a forwards the identity of the **end user** (the person on whose behalf
an agent acts) to downstream MCP servers that enforce per-user access control,
most notably ``mcp-juicefs``. The identity is captured once at the A2A boundary
by an ASGI middleware and stored in :class:`~contextvars.ContextVar`. Outbound
MCP transports read it without threading the value through every call.

The capture is strict and JWT-only, with no per-request fallback: a
``Bearer <jwt>`` credential on ``identity.header`` (default
``X-Forwarded-Authorization``) is signature-verified; the ``email`` claim is
bound as the user and the raw ``Bearer <jwt>`` is bound as the pass-through
credential. A missing or invalid token yields ``401``. When no ``identity:`` is
configured on the server the middleware is a pass-through (no end user is bound).

Pure ASGI middleware is used (not ``BaseHTTPMiddleware``) so the context vars
propagate correctly into the request task.
"""

from __future__ import annotations

import json
from contextvars import ContextVar, Token
from pathlib import Path
from typing import TYPE_CHECKING

import jwt
from starlette.datastructures import Headers

from config_a2a.config.models import ServerIdentityConfig

if TYPE_CHECKING:  # pragma: no cover
    from starlette.types import ASGIApp, Receive, Scope, Send

_current_user: ContextVar[str | None] = ContextVar("config_a2a_current_user", default=None)
_current_credential: ContextVar[str | None] = ContextVar("config_a2a_current_credential", default=None)


def current_user() -> str | None:
    """Return the end-user bound to the in-flight request, or ``None``."""
    return _current_user.get()


def bind_user(person: str | None) -> Token[str | None]:
    """Bind ``person`` for the current context; return the reset token."""
    return _current_user.set(person)


def reset_user(token: Token[str | None]) -> None:
    """Restore the identity context to its previous value."""
    _current_user.reset(token)


def current_credential() -> str | None:
    """Return the raw ``Bearer <jwt>`` to relay downstream (jwt mode), or ``None``."""
    return _current_credential.get()


def bind_credential(credential: str | None) -> Token[str | None]:
    """Bind the pass-through credential for the current context; return the token."""
    return _current_credential.set(credential)


def reset_credential(token: Token[str | None]) -> None:
    """Restore the credential context to its previous value."""
    _current_credential.reset(token)


class IdentityCaptureMiddleware:  # pylint: disable=too-few-public-methods
    """ASGI middleware binding the end-user identity by verifying a Bearer JWT.

    Constructed from the server-wide :class:`ServerIdentityConfig`. It verifies
    the Bearer JWT on ``identity.header``, binds the ``email`` claim plus the raw
    ``Bearer <jwt>`` credential, and answers ``401`` on a missing or invalid
    token. When ``identity`` is ``None`` (no ``identity:`` block configured) the
    middleware is a pass-through: no end user is bound and no request is rejected.
    """

    __slots__ = ("_app", "_identity", "_public_key")

    def __init__(self, app: "ASGIApp", identity: ServerIdentityConfig | None = None) -> None:
        self._app = app
        self._identity = identity
        self._public_key: str | None = None
        if identity is not None:
            self._public_key = Path(identity.public_key_path).read_text(encoding="utf-8")

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        if scope["type"] != "http" or self._identity is None:
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        await self._handle_jwt(headers, scope, receive, send)

    async def _handle_jwt(self, headers: Headers, scope: "Scope", receive: "Receive", send: "Send") -> None:
        identity = self._identity
        assert identity is not None and self._public_key is not None  # constructor guarantees
        authorization = headers.get(identity.header, "")
        if not authorization.lower().startswith("bearer "):
            await _send_unauthorized(send, f"missing Bearer token in {identity.header}")
            return
        token = authorization[len("Bearer ") :].strip()
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=identity.algorithms,
                audience=identity.audience,
                issuer=identity.issuer,
                options={"verify_aud": identity.audience is not None},
            )
        except jwt.PyJWTError as exc:
            await _send_unauthorized(send, f"invalid token: {exc}")
            return
        person = str(claims.get(identity.claim, "")).strip()
        if not person:
            await _send_unauthorized(send, f"token missing '{identity.claim}' claim")
            return
        await self._run(person, f"Bearer {token}", scope, receive, send)

    async def _run(
        self,
        person: str | None,
        credential: str | None,
        scope: "Scope",
        receive: "Receive",
        send: "Send",
    ) -> None:
        user_token = bind_user(person)
        credential_token = bind_credential(credential)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_credential(credential_token)
            reset_user(user_token)


async def _send_unauthorized(send: "Send", detail: str) -> None:
    """Emit a minimal 401 JSON response (missing or invalid Bearer JWT)."""
    body = json.dumps({"error": "unauthenticated", "detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


__all__ = [
    "IdentityCaptureMiddleware",
    "bind_credential",
    "bind_user",
    "current_credential",
    "current_user",
    "reset_credential",
    "reset_user",
]
