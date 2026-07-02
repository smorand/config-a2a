"""Server-wide JWT identity (the only mechanism): inbound verification and relay.

Mirrors ``tests/unit/test_juicefs.py`` patterns. Tokens are minted with the
web-a2a private key and verified with the config-a2a public key (the validated
wire contract: RS256, ``iss=web-a2a``, identity claim ``email``, no audience).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import jwt
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from config_a2a.config.juicefs import JuiceFSConfig
from config_a2a.config.models import McpStreamableHttpServer, ServerConfig, ServerIdentityConfig
from config_a2a.identity import (
    IdentityCaptureMiddleware,
    bind_credential,
    current_credential,
    current_user,
    reset_credential,
)
from config_a2a.juicefs.binding import compile_juicefs
from config_a2a.mcp.streamable_http import _request_headers

_KEYS = Path("/Users/sebastien/projects/perso/config-a2a/.keys")
PUBLIC_KEY = _KEYS / "jwt.pub"
SERVICE_JWT = _KEYS / "service.jwt"
PRIVATE_KEY = Path("/Users/sebastien/projects/perso/web-a2a/.keys/jwt.key")

pytestmark = pytest.mark.skipif(
    not (PUBLIC_KEY.exists() and PRIVATE_KEY.exists()),
    reason="JWT key material not present",
)


def _mint(**claims: Any) -> str:
    payload: dict[str, Any] = {"email": "alice@example.com", "iss": "web-a2a"}
    payload.update(claims)
    return jwt.encode(payload, PRIVATE_KEY.read_text(encoding="utf-8"), algorithm="RS256")


def _jwt_identity(**overrides: Any) -> ServerIdentityConfig:
    kwargs: dict[str, Any] = {"public_key_path": str(PUBLIC_KEY)}
    kwargs.update(overrides)
    return ServerIdentityConfig(**kwargs)


def _probe_app(identity: ServerIdentityConfig) -> Starlette:
    async def whoami(_request: Request) -> JSONResponse:
        return JSONResponse({"user": current_user(), "credential": current_credential()})

    async def public(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/agents/files/tasks", whoami),  # a protected A2A action path
            Route("/agents/files/.well-known/agent-card.json", public),  # public discovery
            Route("/health", public),  # public, outside /agents/
        ]
    )
    app.add_middleware(IdentityCaptureMiddleware, identity=identity)
    return app


# --- model validation -------------------------------------------------------


def test_identity_requires_public_key() -> None:
    with pytest.raises(ValueError, match="public_key_path"):
        ServerIdentityConfig()


def test_jwt_config_defaults_match_wire_contract() -> None:
    cfg = ServerIdentityConfig(public_key_path=str(PUBLIC_KEY))
    assert cfg.header == "X-Forwarded-Authorization"
    assert cfg.algorithms == ["RS256"]
    assert cfg.issuer == "web-a2a"
    assert cfg.audience is None
    assert cfg.claim == "email"


# --- inbound verification (middleware) --------------------------------------


def test_valid_token_binds_user_and_credential() -> None:
    token = _mint(email="alice@example.com")
    client = TestClient(_probe_app(_jwt_identity()))
    resp = client.get("/agents/files/tasks", headers={"X-Forwarded-Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "alice@example.com"
    assert body["credential"] == f"Bearer {token}"


def test_wrong_issuer_rejected() -> None:
    token = _mint(iss="someone-else")
    client = TestClient(_probe_app(_jwt_identity()))
    resp = client.get("/agents/files/tasks", headers={"X-Forwarded-Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_missing_bearer_yields_401() -> None:
    client = TestClient(_probe_app(_jwt_identity()))
    assert client.get("/agents/files/tasks").status_code == 401


def test_forwarded_user_ignored_in_jwt_mode() -> None:
    token = _mint(email="alice@example.com")
    client = TestClient(_probe_app(_jwt_identity()))
    resp = client.get(
        "/agents/files/tasks",
        headers={
            "X-Forwarded-Authorization": f"Bearer {token}",
            "X-Forwarded-User": "mallory@example.com",
        },
    )
    assert resp.json()["user"] == "alice@example.com"


def test_forwarded_user_does_not_bypass_jwt() -> None:
    # No Bearer token, only X-Forwarded-User: must 401 (no fallback bypass).
    client = TestClient(_probe_app(_jwt_identity()))
    resp = client.get("/agents/files/tasks", headers={"X-Forwarded-User": "mallory@example.com"})
    assert resp.status_code == 401


def test_agent_card_is_public() -> None:
    # The agent card (discovery) must be reachable without a token, so clients can
    # add the agent before any end user is involved.
    client = TestClient(_probe_app(_jwt_identity()))
    assert client.get("/agents/files/.well-known/agent-card.json").status_code == 200


def test_non_agent_path_is_public() -> None:
    # Paths outside /agents/ (health, admin, root directory) are not gated here.
    client = TestClient(_probe_app(_jwt_identity()))
    assert client.get("/health").status_code == 200


# --- outbound relay (_request_headers) --------------------------------------


def _jwt_server() -> McpStreamableHttpServer:
    identity = _jwt_identity(service_token_path=str(SERVICE_JWT)) if SERVICE_JWT.exists() else _jwt_identity()
    return compile_juicefs(JuiceFSConfig(url="http://h/mcp"), server_identity=identity)


def test_compile_juicefs_jwt_mode() -> None:
    server = _jwt_server()
    assert server.forward_identity is True
    assert server.identity_header == "X-Forwarded-Authorization"
    if SERVICE_JWT.exists():
        assert server.service_credential is not None
        assert server.service_credential.startswith("Bearer ")


@pytest.mark.skipif(not SERVICE_JWT.exists(), reason="service token not present")
def test_discovery_forwards_service_credential() -> None:
    server = _jwt_server()
    headers = _request_headers(server, discovery=True)
    assert headers["X-Forwarded-Authorization"] == server.service_credential


def test_call_forwards_pass_through_credential() -> None:
    server = _jwt_server()
    token = bind_credential("Bearer end-user-jwt")
    try:
        headers = _request_headers(server, discovery=False)
    finally:
        reset_credential(token)
    assert headers["X-Forwarded-Authorization"] == "Bearer end-user-jwt"


def test_call_without_credential_omits_header() -> None:
    server = _jwt_server()
    assert current_credential() is None
    assert "X-Forwarded-Authorization" not in _request_headers(server, discovery=False)


# --- server-level desugaring with jwt identity ------------------------------


def test_server_config_compiles_juicefs_in_jwt_mode() -> None:
    server = ServerConfig.model_validate(
        {
            "name": "s",
            "identity": {"public_key_path": str(PUBLIC_KEY), "service_token_path": str(SERVICE_JWT)},
            "agents": [
                {
                    "name": "fsbot",
                    "model": {"provider": "openai-compatible", "model": "x"},
                    "pattern": {"type": "simple"},
                    "juicefs": {"url": "http://h/mcp"},
                }
            ],
        }
    )
    juicefs_servers = [s for s in server.agents[0].tools.mcp_servers if s.name == "juicefs"]
    assert len(juicefs_servers) == 1
    compiled = juicefs_servers[0]
    assert compiled.forward_identity is True
    assert compiled.identity_header == "X-Forwarded-Authorization"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
