"""Persistence layer: SQLAlchemy + Alembic + repository / store."""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

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


def build_task_store(config: AgentConfig) -> PersistentTaskStore:
    engine = build_engine(config.persistence)
    session_factory = build_session_factory(engine)
    repo = TaskRepository(session_factory, agent_name=config.name)
    return PersistentTaskStore(repo)


def build_session_factory_for(config: AgentConfig):  # noqa: ANN201 — returns async_sessionmaker
    """Build a session factory for the agent's persistence config.

    Used by the memory layer when the memory backend is `sqlite` (the default):
    the same engine/file/schema as task state is reused.
    """
    engine = build_engine(config.persistence)
    return build_session_factory(engine)


__all__ = [
    "ALEMBIC_INI",
    "REPO_ROOT",
    "build_session_factory_for",
    "build_task_store",
    "run_migrations",
]
