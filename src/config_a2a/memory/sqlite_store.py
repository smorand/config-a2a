"""SQLAlchemy-backed MemoryStore. Defaults to the agent's persistence URL."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from config_a2a.memory.store import MemoryRecord, MemoryStore, Scope, overlap_score
from config_a2a.persistence.models import MemoryRow


class SqlAlchemyStore(MemoryStore):
    """Stores memory records in the existing tasks DB (one table per backend).

    Search is a token-overlap rank over rows filtered by `agent_name` + `scope`;
    works on both SQLite and Postgres with zero extension. Vector retrieval is
    a future hook (see `.agent_docs/memory.md`).
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def write(self, record: MemoryRecord, *, agent_name: str) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                MemoryRow(
                    id=record.id,
                    agent_name=agent_name,
                    scope=record.scope,
                    user_id=record.user_id,
                    text=record.text,
                    tags=list(record.tags or []),
                    created_at=record.created_at,
                )
            )

    async def search(
        self,
        query: str,
        *,
        agent_name: str,
        scopes: list[Scope],
        top_k: int,
        user_id: str | None = None,
    ) -> list[MemoryRecord]:
        async with self._session_factory() as session:
            stmt = select(MemoryRow).where(
                MemoryRow.agent_name == agent_name,
                MemoryRow.scope.in_(list(scopes)),
            )
            rows = list(await session.scalars(stmt))
        scored: list[MemoryRecord] = []
        for row in rows:
            if user_id and row.scope == "user" and row.user_id and row.user_id != user_id:
                continue
            score = overlap_score(row.text, query)
            if score == 0.0:
                continue
            scored.append(
                MemoryRecord(
                    id=row.id,
                    text=row.text,
                    scope=row.scope,  # type: ignore[arg-type]
                    tags=list(row.tags or []),
                    user_id=row.user_id,
                    created_at=row.created_at,
                    score=score,
                )
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    async def list_all(self, *, agent_name: str) -> list[MemoryRecord]:
        async with self._session_factory() as session:
            rows = list(await session.scalars(select(MemoryRow).where(MemoryRow.agent_name == agent_name)))
        return [
            MemoryRecord(
                id=row.id,
                text=row.text,
                scope=row.scope,  # type: ignore[arg-type]
                tags=list(row.tags or []),
                user_id=row.user_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
