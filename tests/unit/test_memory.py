"""Memory store + working memory + extractor + runtime hooks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app_for_runtime
from config_a2a.config.models import PersistenceConfig, WorkingMemoryConfig
from config_a2a.memory import build_orchestrator
from config_a2a.memory.extractor import extract_facts
from config_a2a.memory.in_memory_store import InMemoryStore
from config_a2a.memory.store import MemoryRecord, overlap_score
from config_a2a.memory.working import apply_sliding_summary
from config_a2a.providers.base import ChatMessage, ChatRequest, ChatResponse, LlmProvider, TokenUsage
from config_a2a.runtime import AgentRuntime
from tests.unit.conftest import load_single_agent


class _Scripted(LlmProvider):
    name = "scripted"

    def __init__(self, queue: list[ChatResponse]) -> None:
        self._queue = list(queue)
        self.calls: list[ChatRequest] = []

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls.append(request)
        if self._queue:
            return self._queue.pop(0)
        return ChatResponse(content="(default)", usage=TokenUsage())

    async def aclose(self) -> None:
        return None


# ---- store ----------------------------------------------------------------


def test_overlap_score_basic() -> None:
    assert overlap_score("favourite colour is purple", "what is my favourite colour") > 0.0
    assert overlap_score("favourite colour is purple", "weather today") == 0.0
    assert overlap_score("", "anything") == 0.0


async def test_in_memory_store_search_filters_by_scope() -> None:
    store = InMemoryStore()
    await store.write(MemoryRecord(text="user likes purple", scope="user"), agent_slug="a", agent_name="a")
    await store.write(MemoryRecord(text="agent learned to use SI units", scope="agent"), agent_slug="a", agent_name="a")
    hits = await store.search("purple favourite", agent_slug="a", scopes=["user"], top_k=5)
    assert len(hits) == 1
    assert hits[0].scope == "user"
    hits_both = await store.search("purple units learned", agent_slug="a", scopes=["user", "agent"], top_k=5)
    assert {h.scope for h in hits_both} == {"user", "agent"}


async def test_in_memory_store_user_id_isolation() -> None:
    store = InMemoryStore()
    await store.write(
        MemoryRecord(text="alice likes tea", scope="user", user_id="alice"), agent_slug="a", agent_name="a"
    )
    await store.write(
        MemoryRecord(text="bob likes coffee", scope="user", user_id="bob"), agent_slug="a", agent_name="a"
    )
    alice = await store.search("likes drink", agent_slug="a", scopes=["user"], top_k=5, user_id="alice")
    assert all(h.user_id == "alice" for h in alice if h.user_id)


# ---- working memory --------------------------------------------------------


async def test_sliding_summary_triggers_when_window_exceeded() -> None:
    cfg = WorkingMemoryConfig(strategy="sliding_summary", window=4, summarize_every=2)
    provider = _Scripted([ChatResponse(content="user established preferences A, B, C.")])
    messages = [
        ChatMessage(role="system", content="be brief"),
        ChatMessage(role="user", content="m1"),
        ChatMessage(role="assistant", content="r1"),
        ChatMessage(role="user", content="m2"),
        ChatMessage(role="assistant", content="r2"),
        ChatMessage(role="user", content="m3"),
        ChatMessage(role="assistant", content="r3"),
    ]
    summarised = await apply_sliding_summary(messages, config=cfg, provider=provider)
    assert summarised[0].role == "system"
    assert summarised[0].content == "be brief"
    assert summarised[1].role == "system"
    assert summarised[1].content.startswith("[memory:summary]")
    assert len(summarised) == 1 + 1 + cfg.window
    assert summarised[-1].content == "r3"


async def test_sliding_summary_passthrough_when_under_window() -> None:
    cfg = WorkingMemoryConfig(strategy="sliding_summary", window=10, summarize_every=5)
    provider = _Scripted([])
    messages = [
        ChatMessage(role="system", content="x"),
        ChatMessage(role="user", content="y"),
        ChatMessage(role="assistant", content="z"),
    ]
    out = await apply_sliding_summary(messages, config=cfg, provider=provider)
    assert out == messages
    assert provider.calls == []


# ---- extractor -------------------------------------------------------------


async def test_extract_facts_parses_well_formed_json() -> None:
    provider = _Scripted(
        [
            ChatResponse(
                content=json.dumps(
                    {
                        "facts": [
                            {"text": "User's favourite colour is purple", "scope": "user", "tags": ["preference"]},
                            {"text": "Assistant should answer in French", "scope": "user", "tags": ["preference"]},
                        ]
                    }
                )
            )
        ]
    )
    records = await extract_facts(provider, user_text="I'm French, I love purple", assistant_text="Noted.")
    assert len(records) == 2
    assert all(r.scope == "user" for r in records)


async def test_extract_facts_strips_code_fences_and_handles_garbage() -> None:
    provider = _Scripted(
        [
            ChatResponse(content='```json\n{"facts": [{"text": "alpha", "scope": "agent"}]}\n```'),
        ]
    )
    records = await extract_facts(provider, user_text="x", assistant_text="y")
    assert len(records) == 1
    assert records[0].text == "alpha"

    bad_provider = _Scripted([ChatResponse(content="not json at all")])
    assert await extract_facts(bad_provider, user_text="x", assistant_text="y") == []


async def test_extract_facts_forced_scope_overrides_model() -> None:
    provider = _Scripted([ChatResponse(content=json.dumps({"facts": [{"text": "x", "scope": "user"}]}))])
    records = await extract_facts(provider, user_text="x", assistant_text="y", forced_scope="agent")
    assert records[0].scope == "agent"


# ---- runtime hooks --------------------------------------------------------


def _memory_runtime(scripts: list[ChatResponse]) -> tuple[AgentRuntime, _Scripted, str]:
    _, agent, prefix = load_single_agent("08-memory")
    agent.memory.long_term.store.backend = "in_memory"
    provider = _Scripted(scripts)
    runtime = AgentRuntime(agent, provider=provider, memory=build_orchestrator(agent))
    return runtime, provider, prefix


def test_runtime_injects_long_term_memory_on_first_turn() -> None:
    runtime, provider, prefix = _memory_runtime(
        [
            ChatResponse(content="OK, noted."),
            ChatResponse(
                content=json.dumps({"facts": [{"text": "User's favourite colour is purple", "scope": "user"}]})
            ),
            ChatResponse(content="Your favourite colour is purple."),
            ChatResponse(content=json.dumps({"facts": []})),
        ]
    )
    client = TestClient(create_app_for_runtime(runtime))

    r1 = client.post(
        f"{prefix}/message:send",
        json={
            "message": {"messageId": "t1", "role": "ROLE_USER", "parts": [{"text": "My favourite colour is purple"}]}
        },
    ).json()
    assert r1["status"]["state"] == "TASK_STATE_COMPLETED"

    r2 = client.post(
        f"{prefix}/message:send",
        json={"message": {"messageId": "t2", "role": "ROLE_USER", "parts": [{"text": "What is my favourite colour?"}]}},
    ).json()
    assert r2["status"]["state"] == "TASK_STATE_COMPLETED"

    turn2_primary_call = provider.calls[2]
    system_message = turn2_primary_call.messages[0]
    assert system_message.role == "system"
    assert "Relevant memory from past interactions" in system_message.content
    assert "purple" in system_message.content


def test_runtime_no_harvest_when_disabled() -> None:
    _, agent, prefix = load_single_agent("08-memory")
    agent.memory.enabled = False
    provider = _Scripted([ChatResponse(content="hi")])
    runtime = AgentRuntime(agent, provider=provider)
    client = TestClient(create_app_for_runtime(runtime))
    response = client.post(
        f"{prefix}/message:send",
        json={"message": {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    ).json()
    assert response["status"]["state"] == "TASK_STATE_COMPLETED"
    assert len(provider.calls) == 1


# ---- alembic for memory_records -------------------------------------------


def test_memory_table_after_migration(tmp_path: Path) -> None:
    from config_a2a.persistence import build_session_factory_for, run_migrations
    from config_a2a.memory.sqlite_store import SqlAlchemyStore

    _, agent, _ = load_single_agent("08-memory")
    db = tmp_path / "agent.db"
    agent.persistence = PersistenceConfig(url=f"sqlite+aiosqlite:///{db}")
    run_migrations(agent.effective_persistence)
    session_factory = build_session_factory_for(agent)
    store = SqlAlchemyStore(session_factory)
    import asyncio

    assert agent.slug is not None

    async def _run() -> list[MemoryRecord]:
        await store.write(
            MemoryRecord(text="user likes tea", scope="user"),
            agent_slug=agent.slug,
            agent_name=agent.name,
        )
        await store.write(
            MemoryRecord(text="orange is loud", scope="agent"),
            agent_slug=agent.slug,
            agent_name=agent.name,
        )
        results = await store.search(
            "tea preference",
            agent_slug=agent.slug,
            scopes=["user", "agent"],
            top_k=10,
        )
        return results

    results = asyncio.run(_run())
    assert any("tea" in r.text for r in results)


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
