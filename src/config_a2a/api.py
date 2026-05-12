"""FastAPI application factory with optional OTel + authentication middleware."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config_a2a import __version__
from config_a2a.a2a.routes import create_router
from config_a2a.config.models import AuthenticationConfig
from config_a2a.observability.otel import setup_otel
from config_a2a.runtime import AgentRuntime

_PUBLIC_PATHS = {"/health", "/.well-known/a2a/agent-card", "/.well-known/agent-card.json", "/.well-known/agent.json"}


class _AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, auth: AuthenticationConfig) -> None:
        super().__init__(app)
        self._auth = auth
        self._expected = os.environ.get(auth.value_env) if auth.value_env else None

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        if request.url.path in _PUBLIC_PATHS or self._auth.type == "none":
            return await call_next(request)
        header = request.headers.get(self._auth.header_name)
        if not header or not self._expected:
            return JSONResponse({"detail": "missing or invalid credentials"}, status_code=401)
        provided = header.removeprefix("Bearer ").strip() if self._auth.type == "bearer" else header
        if provided != self._expected:
            return JSONResponse({"detail": "invalid credentials"}, status_code=401)
        return await call_next(request)


def create_app(runtime: AgentRuntime) -> FastAPI:
    setup_otel(runtime.config)
    app = FastAPI(
        title=runtime.config.name,
        version=runtime.config.version,
        description=runtime.config.description or "A2A agent built by config-a2a",
    )
    app.state.runtime = runtime
    app.state.config_a2a_version = __version__
    if runtime.config.authentication.type != "none":
        app.add_middleware(_AuthMiddleware, auth=runtime.config.authentication)
    app.include_router(create_router())
    return app
