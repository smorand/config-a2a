"""E2E memory: real OpenRouter, two turns, the second must recall."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from config_a2a.api import create_app
from config_a2a.config.loader import load_server_config
from config_a2a.persistence import run_migrations

EXAMPLE = Path(__file__).resolve().parents[2] / "config_examples" / "08-memory" / "agents.yaml"

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _require_openrouter() -> None:
    if os.environ.get("RUN_E2E") != "1":
        pytest.skip("set RUN_E2E=1 to run e2e tests")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")


@pytest.fixture()
def fresh_db(tmp_path: Path) -> str:
    from config_a2a.config.models import PersistenceConfig

    db = tmp_path / f"memory-{uuid.uuid4().hex[:8]}.db"
    url = f"sqlite+aiosqlite:///{db}"
    run_migrations(PersistenceConfig(backend="sqlite", url=url, run_migrations_on_start=False))
    return url


def _final_text(task_json: dict[str, Any]) -> str:
    parts = (task_json.get("status") or {}).get("message", {}).get("parts") or []
    return "\n".join(p.get("text") or "" for p in parts).strip()


async def _send(
    client: httpx.AsyncClient,
    prefix: str,
    text: str,
    *,
    context_id: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "contextId": context_id,
        "parts": [{"text": text}],
    }
    if task_id is not None:
        payload["taskId"] = task_id
    response = await client.post(
        f"{prefix}/message:send",
        json={"message": payload},
        timeout=httpx.Timeout(120.0),
    )
    response.raise_for_status()
    return response.json()


async def test_memory_carries_a_user_fact_across_two_turns(fresh_db: str) -> None:
    server_config = load_server_config(EXAMPLE)
    server_config.persistence.url = fresh_db
    app = create_app(server_config)
    server = app.state.server
    runtime = server.get_runtime("memory")
    assert runtime is not None
    prefix = "/agents/memory"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        turn1 = await _send(
            client,
            prefix,
            "Remember this fact about me: my favourite colour is purple.",
            context_id=str(uuid.uuid4()),
        )
        assert turn1["status"]["state"] == "TASK_STATE_COMPLETED"

        assert runtime.memory.store is not None
        records = await runtime.memory.store.list_all(agent_slug="memory")
        assert records
        joined = " | ".join(r.text.lower() for r in records)
        assert "purple" in joined

        turn2 = await _send(
            client,
            prefix,
            "What is my favourite colour? Reply with the colour name only.",
            context_id=str(uuid.uuid4()),
        )
        assert turn2["status"]["state"] == "TASK_STATE_COMPLETED"
        answer = _final_text(turn2).lower()
        assert "purple" in answer


async def test_working_memory_summarises_a_long_conversation(fresh_db: str) -> None:
    server_config = load_server_config(EXAMPLE)
    server_config.persistence.url = fresh_db
    agent = server_config.agents[0]
    agent.memory.working.window = 4
    agent.memory.working.summarize_every = 2
    app = create_app(server_config)
    prefix = f"/agents/{agent.slug}"
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
            response = await _send(client, prefix, text, context_id=context_id, task_id=task_id)
            assert response["status"]["state"] == "TASK_STATE_COMPLETED"
            task_id = response["id"]
        final = _final_text(response).lower()
        assert "veg" in final
