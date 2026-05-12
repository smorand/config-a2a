"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from config_a2a import __version__
from config_a2a.a2a.routes import create_router
from config_a2a.runtime import AgentRuntime


def create_app(runtime: AgentRuntime) -> FastAPI:
    app = FastAPI(
        title=runtime.config.name,
        version=runtime.config.version,
        description=runtime.config.description or "A2A agent built by config-a2a",
    )
    app.state.runtime = runtime
    app.state.config_a2a_version = __version__
    app.include_router(create_router())
    return app
