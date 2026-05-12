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


__all__ = ["build_task_store", "run_migrations", "REPO_ROOT", "ALEMBIC_INI"]
