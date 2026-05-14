"""Ephemeral in-process MemoryStore — used by tests and `--check` validation."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from config_a2a.memory.store import MemoryRecord, MemoryStore, Scope, overlap_score


class InMemoryStore(MemoryStore):
    """Lives in process; vanishes on restart. Good for unit tests."""

    def __init__(self) -> None:
        self._records: dict[str, list[MemoryRecord]] = {}
        self._lock = asyncio.Lock()

    async def write(self, record: MemoryRecord, *, agent_slug: str, agent_name: str) -> None:
        del agent_name  # unused — slug is the discriminator
        async with self._lock:
            self._records.setdefault(agent_slug, []).append(record)

    async def search(
        self,
        query: str,
        *,
        agent_slug: str,
        scopes: list[Scope],
        top_k: int,
        user_id: str | None = None,
    ) -> list[MemoryRecord]:
        async with self._lock:
            candidates = self._records.get(agent_slug, [])
        scored: list[MemoryRecord] = []
        for record in candidates:
            if record.scope not in scopes:
                continue
            if user_id and record.scope == "user" and record.user_id and record.user_id != user_id:
                continue
            score = overlap_score(record.text, query)
            if score == 0.0:
                continue
            scored.append(replace(record, score=score))
        scored.sort(key=lambda record: record.score, reverse=True)
        return scored[:top_k]

    async def list_all(self, *, agent_slug: str) -> list[MemoryRecord]:
        async with self._lock:
            return list(self._records.get(agent_slug, []))
