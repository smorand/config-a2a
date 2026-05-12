"""Entry point exposing `config-a2a` as a runnable script."""

from __future__ import annotations

import uvicorn

from config_a2a.settings import get_settings


def main() -> None:
    """Launch the FastAPI application via uvicorn."""
    settings = get_settings()
    uvicorn.run(
        "config_a2a.api:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
