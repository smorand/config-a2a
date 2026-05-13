"""Iter 12 e2e: real OpenRouter, two independent turns, the second must recall.

The flow exercised here is exactly what the memory subsystem is for:

  Turn 1 (contextId=A): "Remember my favourite colour is purple."
    └─ extractor LLM call distils a user-scoped fact and writes it.
  Turn 2 (contextId=B, different task): "What is my favourite colour?"
    └─ read hook injects the fact into the system prompt.
    └─ LLM answers with "purple" without having been told twice.

Gated by RUN_E2E=1 + OPENROUTER_API_KEY.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from config_a2a.api import create_app
from config_a2a.config.loader import load_agent_config
from config_a2a.memory import build_orchestrator
from config_a2a.persistence import build_session_factory_for, build_task_store, run_migrations
from config_a2a.runtime import AgentRuntime

EXAMPLE = Path(__file__).resolve().parents[2] / "config_examples" / "08-memory" / "agent.yaml"

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _require_openrouter() -> None:
    if os.environ.get("RUN_E2E") != "1":
        pytest.skip("set RUN_E2E=1 to run e2e tests")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")


@pytest.fixture()
def fresh_db(tmp_path: Path) -> str:
    """Create an empty SQLite file with the migrated schema (sync; alembic uses asyncio.run)."""
    from config_a2a.config.models import PersistenceConfig

    db = tmp_path / f"memory-{uuid.uuid4().hex[:8]}.db"
    url = f"sqlite+aiosqlite:///{db}"
    run_migrations(PersistenceConfig(backend="sqlite", url=url, run_migrations_on_start=False))
    return url


def _final_text(task_json: dict[str, Any]) -> str:
    parts = (task_json.get("status") or {}).get("message", {}).get("parts") or []
    return "\n".join(p.get("text") or "" for p in parts).strip()


async def _send(client: httpx.AsyncClient, text: str, *, context_id: str, task_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "contextId": context_id,
        "parts": [{"text": text}],
    }
    if task_id is not None:
        payload["taskId"] = task_id
    response = await client.post(
        "/message:send",
        json={"message": payload},
        timeout=httpx.Timeout(120.0),
    )
    response.raise_for_status()
    return response.json()


async def test_memory_carries_a_user_fact_across_two_turns(fresh_db: str) -> None:
    """The headline E2E: turn 1 establishes a fact, turn 2 retrieves it."""
    config = load_agent_config(EXAMPLE)
    config.persistence.url = fresh_db

    tasks = build_task_store(config)
    orchestrator = build_orchestrator(
        config, session_factory=build_session_factory_for(config)
    )
    runtime = AgentRuntime(config, tasks=tasks, memory=orchestrator)
    app = create_app(runtime)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # ----- Turn 1 -----
        turn1 = await _send(
            client,
            "Remember this fact about me: my favourite colour is purple.",
            context_id=str(uuid.uuid4()),
        )
        assert turn1["status"]["state"] == "TASK_STATE_COMPLETED", (
            f"unexpected state turn1: {turn1['status']['state']}"
        )

        # The extractor should have written at least one record mentioning purple.
        assert orchestrator.store is not None
        records = await orchestrator.store.list_all(agent_name=config.name)
        assert records, "memory store is empty after turn 1; harvest hook did not fire"
        joined = " | ".join(r.text.lower() for r in records)
        assert "purple" in joined, f"no record mentions purple; got: {joined!r}"

        # ----- Turn 2 (new context) -----
        turn2 = await _send(
            client,
            "What is my favourite colour? Reply with the colour name only.",
            context_id=str(uuid.uuid4()),
        )
        assert turn2["status"]["state"] == "TASK_STATE_COMPLETED"
        answer = _final_text(turn2).lower()
        assert "purple" in answer, (
            f"memory recall failed; turn-2 answer was: {answer!r}; "
            f"stored memory was: {joined!r}"
        )


async def test_working_memory_summarises_a_long_conversation(fresh_db: str) -> None:
    """Tiny window forces a summary by the third turn; final reply still respects constraints."""
    config = load_agent_config(EXAMPLE)
    config.persistence.url = fresh_db
    config.memory.working.window = 4
    config.memory.working.summarize_every = 2
    runtime = AgentRuntime(
        config,
        tasks=build_task_store(config),
        memory=build_orchestrator(
            config, session_factory=build_session_factory_for(config)
        ),
    )
    app = create_app(runtime)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        context_id = str(uuid.uuid4())
        task_id: str | None = None
        response: dict[str, Any] = {}
        for text in [
            "I'm planning a Saturday picnic for 4 people.",
            "Two of them are strict vegetarians.",
            "I have one hour of prep time.",
            "Suggest a single dish that satisfies all constraints. Be concise.",
        ]:
            response = await _send(client, text, context_id=context_id, task_id=task_id)
            assert response["status"]["state"] == "TASK_STATE_COMPLETED", (
                f"turn failed: {response['status']['state']}"
            )
            task_id = response["id"]
        final = _final_text(response).lower()
        assert "veg" in final, (
            f"final answer ignored the vegetarian constraint: {final!r}"
        )
