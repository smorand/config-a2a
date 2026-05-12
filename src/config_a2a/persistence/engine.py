"""Async engine + session factory keyed off the YAML persistence block."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from config_a2a.config.models import PersistenceConfig


def _ensure_sqlite_dir(url: str) -> None:
    """For sqlite+aiosqlite:///./state/foo.db, make sure ./state exists."""
    marker = "sqlite+aiosqlite:///"
    if url.startswith(marker):
        path = Path(url[len(marker) :])
        if path.parts and path.parts[0] != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)


def build_engine(config: PersistenceConfig) -> AsyncEngine:
    _ensure_sqlite_dir(config.url)
    return create_async_engine(config.url, future=True, pool_pre_ping=True)


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
