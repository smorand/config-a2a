"""FastAPI application exposing the A2A agent over HTTP."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException

from config_a2a import __version__
from config_a2a.loader import ConfigLoadError, load_agent_config
from config_a2a.models import AgentConfig
from config_a2a.settings import Settings, get_settings


def create_app() -> FastAPI:
    """Application factory used by uvicorn and tests."""
    app = FastAPI(
        title="config-a2a",
        version=__version__,
        description="Create advanced A2A agents using YAML configuration files",
    )

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/agent", response_model=AgentConfig, tags=["agent"])
    async def get_agent(settings: Settings = Depends(get_settings)) -> AgentConfig:
        try:
            return load_agent_config(settings.config_path)
        except ConfigLoadError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()
