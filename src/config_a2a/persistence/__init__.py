"""Persistence layer: SQLAlchemy + Alembic + repository / store."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from config_a2a.config.models import AgentConfig, PersistenceConfig
from config_a2a.persistence.engine import build_engine, build_session_factory
from config_a2a.persistence.repository import TaskRepository
from config_a2a.persistence.store import PersistentTaskStore

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def run_migrations(persistence: PersistenceConfig) -> None:
    """Run `alembic upgrade head` against the configured database URL."""
    cfg = AlembicConfig(str(ALEMBIC_INI))
    os.environ["CONFIG_A2A_DATABASE_URL"] = persistence.url
    command.upgrade(cfg, "head")


def build_task_store(
    config: AgentConfig,
    *,
    engine: AsyncEngine | None = None,
    session_factory: async_sessionmaker | None = None,
) -> PersistentTaskStore:
    """Build a task store for ``config``.

    A shared ``engine`` / ``session_factory`` can be passed by the multi-agent
    server so every agent reads from one database connection pool. If both are
    omitted (single-agent test path), the agent's ``persistence`` block is used
    to build a private engine.
    """
    if session_factory is None:
        own_engine = engine or build_engine(config.effective_persistence)
        session_factory = build_session_factory(own_engine)
    assert config.slug is not None
    repo = TaskRepository(session_factory, agent_slug=config.slug, agent_name=config.name)
    return PersistentTaskStore(repo)


def build_session_factory_for(
    config: AgentConfig | PersistenceConfig,
    *,
    engine: AsyncEngine | None = None,
) -> async_sessionmaker:
    """Build a session factory for either an agent or a bare persistence block."""
    if engine is None:
        if isinstance(config, PersistenceConfig):
            engine = build_engine(config)
        else:
            engine = build_engine(config.effective_persistence)
    return build_session_factory(engine)


__all__ = [
    "ALEMBIC_INI",
    "REPO_ROOT",
    "build_session_factory_for",
    "build_task_store",
    "run_migrations",
]
