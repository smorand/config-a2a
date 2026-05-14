"""SQLAlchemy task store + alembic migration + resume contract."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_a2a.a2a.envelope import TaskStatus, text_message
from config_a2a.api import create_app_for_runtime
from config_a2a.config.models import AgentConfig, PersistenceConfig
from config_a2a.guardrails.confirmations import (
    confirm_metadata,
    confirm_prompt,
    is_approval,
    is_denial,
    policy_for,
)
from config_a2a.persistence import build_task_store, run_migrations
from config_a2a.persistence.engine import build_engine, build_session_factory
from config_a2a.persistence.repository import TaskRepository
from config_a2a.persistence.store import PersistentTaskStore
from config_a2a.providers.base import ChatRequest, ChatResponse, LlmProvider
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent


def _sqlite_agent(tmp_path: Path) -> AgentConfig:
    _, agent, _ = load_single_agent("01-simple")
    db_path = tmp_path / "agent.db"
    agent.persistence = PersistenceConfig(url=f"sqlite+aiosqlite:///{db_path}")
    return agent


@pytest.fixture()
def migrated_agent(tmp_path: Path) -> AgentConfig:
    agent = _sqlite_agent(tmp_path)
    run_migrations(agent.effective_persistence)
    return agent


class _StubProvider(LlmProvider):
    name = "stub"

    async def chat(self, request: ChatRequest) -> ChatResponse:  # noqa: ARG002
        return ChatResponse(content="ok")

    async def aclose(self) -> None:
        return None


async def test_persistent_store_roundtrip(migrated_agent: AgentConfig) -> None:
    store: PersistentTaskStore = build_task_store(migrated_agent)
    record = await store.create()
    assert record.state == "TASK_STATE_SUBMITTED"
    await store.append_message(record.id, text_message("ROLE_USER", "hi"))
    await store.update_status(
        record.id,
        TaskStatus(state="TASK_STATE_WORKING"),
        pending_action={"kind": "confirm_tool", "tool_name": "fs.delete"},
    )
    refreshed = await store.get(record.id)
    assert refreshed is not None
    assert refreshed.state == "TASK_STATE_WORKING"
    assert refreshed.pending_action and refreshed.pending_action["tool_name"] == "fs.delete"
    assert len(refreshed.history) == 1
    await store.update_status(record.id, TaskStatus(state="TASK_STATE_COMPLETED"), clear_pending=True)
    again = await store.get(record.id)
    assert again is not None
    assert again.pending_action is None


async def test_recent_tasks_limit(migrated_agent: AgentConfig) -> None:
    store = build_task_store(migrated_agent)
    for _ in range(3):
        await store.create()
    recent = await store.list_recent(limit=10)
    assert len(recent) == 3


def test_alembic_migration_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    os.environ["CONFIG_A2A_DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    try:
        agent = _sqlite_agent(tmp_path)
        run_migrations(agent.effective_persistence)
        import asyncio

        async def _check() -> list[str]:
            engine = build_engine(agent.effective_persistence)
            factory = build_session_factory(engine)
            assert agent.slug is not None
            repo = TaskRepository(factory, agent_slug=agent.slug, agent_name=agent.name)
            row = await repo.create_task()
            assert row.id
            tasks = await repo.list_recent_tasks()
            await engine.dispose()
            return [t.id for t in tasks]

        ids = asyncio.run(_check())
        assert ids
    finally:
        os.environ.pop("CONFIG_A2A_DATABASE_URL", None)


def test_resume_with_taskid_appends_message(tmp_path: Path, migrated_agent: AgentConfig) -> None:
    store = build_task_store(migrated_agent)
    runtime = AgentRuntime(migrated_agent, provider=_StubProvider(), tasks=store)
    client = TestClient(create_app_for_runtime(runtime))
    assert migrated_agent.slug is not None
    prefix = f"/agents/{migrated_agent.slug}"
    first = client.post(
        f"{prefix}/message:send",
        json={"message": {"messageId": "m-1", "role": "ROLE_USER", "parts": [{"text": "hello"}]}},
    ).json()
    task_id = first["id"]
    second = client.post(
        f"{prefix}/message:send",
        json={
            "message": {
                "messageId": "m-2",
                "role": "ROLE_USER",
                "taskId": task_id,
                "parts": [{"text": "follow-up"}],
            }
        },
    ).json()
    assert second["id"] == task_id


def test_resume_unknown_taskid_404(migrated_agent: AgentConfig) -> None:
    store = build_task_store(migrated_agent)
    runtime = AgentRuntime(migrated_agent, provider=_StubProvider(), tasks=store)
    client = TestClient(create_app_for_runtime(runtime))
    assert migrated_agent.slug is not None
    prefix = f"/agents/{migrated_agent.slug}"
    response = client.post(
        f"{prefix}/message:send",
        json={
            "message": {
                "messageId": "m-x",
                "role": "ROLE_USER",
                "taskId": "does-not-exist",
                "parts": [{"text": "hi"}],
            }
        },
    )
    assert response.status_code == 404


def test_confirm_metadata_shape() -> None:
    md = confirm_metadata("fs.delete", "call-2", {"path": "/tmp/x"})
    assert md == {
        "kind": "confirm_tool",
        "tool_name": "fs.delete",
        "arguments": {"path": "/tmp/x"},
        "tool_call_id": "call-2",
    }
    assert json.loads(json.dumps(md)) == md
    prompt = confirm_prompt("fs.delete", {"path": "/tmp/x"})
    assert "fs.delete" in prompt and "path" in prompt
    assert is_approval("yes") and is_approval("APPROVE")
    assert is_denial("no") and not is_denial("maybe")


def test_policy_for_per_tool_override() -> None:
    from config_a2a.config.models import ConfirmationsConfig

    cfg = ConfirmationsConfig(destructive_hint="prompt", per_tool={"fs.delete": "auto_deny"})
    assert policy_for(cfg, "fs.delete") == "auto_deny"
    assert policy_for(cfg, "fs.read") == "prompt"
