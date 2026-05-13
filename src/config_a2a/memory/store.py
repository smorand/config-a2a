"""MemoryStore ABC and shared record types."""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

Scope = Literal["user", "agent"]


@dataclass
class MemoryRecord:
    text: str
    scope: Scope = "agent"
    tags: list[str] = field(default_factory=list)
    user_id: str | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    score: float = 0.0  # populated by search()


_TOK = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> set[str]:
    """Lower-case alphanumeric tokens. Cheap fallback retrieval signal."""
    return {match.group(0).lower() for match in _TOK.finditer(text) if len(match.group(0)) > 2}


def overlap_score(record: str, query: str) -> float:
    """Token-overlap retrieval score in [0, 1]. Recall-biased for short queries."""
    q = tokenize(query)
    if not q:
        return 0.0
    r = tokenize(record)
    if not r:
        return 0.0
    return len(r & q) / len(q)


class MemoryStore(ABC):
    """Persistence interface for cross-task memory records.

    Implementations should be agent-scoped: one store per agent process.
    The runtime owns the call timing (read/write hooks); the store only
    promises durable storage + relevance-ranked search.
    """

    @abstractmethod
    async def write(self, record: MemoryRecord, *, agent_name: str) -> None: ...

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        agent_name: str,
        scopes: list[Scope],
        top_k: int,
        user_id: str | None = None,
    ) -> list[MemoryRecord]: ...

    @abstractmethod
    async def list_all(self, *, agent_name: str) -> list[MemoryRecord]: ...

    async def aclose(self) -> None:  # pragma: no cover — default no-op
        return None
