"""Per-request end-user identity propagation (mode-aware: forwarded_user | jwt).

config-a2a forwards the identity of the **end user** (the person on whose behalf
an agent acts) to downstream MCP servers that enforce per-user access control,
most notably ``mcp-juicefs``. The identity is captured once at the A2A boundary
by an ASGI middleware and stored in :class:`~contextvars.ContextVar`. Outbound
MCP transports read it without threading the value through every call.

The capture mode is server-wide (one mode per process) and strict, with no
per-request fallback:

* ``forwarded_user``: a trusted header (default ``X-Forwarded-User``) carries the
  bare email. A missing header binds ``None`` (anonymous / discovery).
* ``jwt``: a ``Bearer <jwt>`` credential on ``jwt.header`` (default
  ``X-Forwarded-Authorization``) is signature-verified; the ``email`` claim is
  bound as the user and the raw ``Bearer <jwt>`` is bound as the pass-through
  credential. ``X-Forwarded-User`` is ignored; a missing or invalid token
  yields ``401``.

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

DEFAULT_FORWARDED_USER_HEADER = "X-Forwarded-User"

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
    """ASGI middleware binding the end-user identity per the server-wide mode.

    Constructed from the full :class:`ServerIdentityConfig`. In ``forwarded_user``
    mode it reads ``inbound_header`` verbatim (missing -> ``None``, never ``401``).
    In ``jwt`` mode it verifies the Bearer JWT on ``jwt.header``, binds the
    ``email`` claim plus the raw credential, and answers ``401`` on a missing or
    invalid token (``X-Forwarded-User`` is ignored).
    """

    __slots__ = ("_app", "_identity", "_public_key")

    def __init__(self, app: "ASGIApp", identity: ServerIdentityConfig | None = None) -> None:
        self._app = app
        self._identity = identity if identity is not None else ServerIdentityConfig()
        self._public_key: str | None = None
        if self._identity.mode == "jwt":
            jwt_config = self._identity.jwt
            if jwt_config is None:  # pragma: no cover - guarded by ServerIdentityConfig validator
                raise ValueError("identity.mode is 'jwt' but identity.jwt is not configured")
            self._public_key = Path(jwt_config.public_key_path).read_text(encoding="utf-8")

    async def __call__(self, scope: "Scope", receive: "Receive", send: "Send") -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if self._identity.mode == "jwt":
            await self._handle_jwt(headers, scope, receive, send)
            return
        person = headers.get(self._identity.inbound_header, "").strip() or None
        await self._run(person, None, scope, receive, send)

    async def _handle_jwt(self, headers: Headers, scope: "Scope", receive: "Receive", send: "Send") -> None:
        jwt_config = self._identity.jwt
        assert jwt_config is not None and self._public_key is not None  # constructor guarantees
        authorization = headers.get(jwt_config.header, "")
        if not authorization.lower().startswith("bearer "):
            await _send_unauthorized(send, f"missing Bearer token in {jwt_config.header}")
            return
        token = authorization[len("Bearer ") :].strip()
        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=jwt_config.algorithms,
                audience=jwt_config.audience,
                issuer=jwt_config.issuer,
                options={"verify_aud": jwt_config.audience is not None},
            )
        except jwt.PyJWTError as exc:
            await _send_unauthorized(send, f"invalid token: {exc}")
            return
        person = str(claims.get(jwt_config.claim, "")).strip()
        if not person:
            await _send_unauthorized(send, f"token missing '{jwt_config.claim}' claim")
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
    """Emit a minimal 401 JSON response (jwt mode, missing or invalid token)."""
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
    "DEFAULT_FORWARDED_USER_HEADER",
    "IdentityCaptureMiddleware",
    "bind_credential",
    "bind_user",
    "current_credential",
    "current_user",
    "reset_credential",
    "reset_user",
]
