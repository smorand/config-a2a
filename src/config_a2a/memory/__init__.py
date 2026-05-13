"""Memory orchestrator. The runtime owns the WHEN; the store owns the WHERE.

Hooks exposed to AgentRuntime:
  * ``inject_long_term(user_text)`` — pre-pattern, returns a context string to splice into the system prompt.
  * ``maybe_summarise(messages)`` — called by patterns' ``call_llm`` to enforce the working-memory window.
  * ``harvest(user_text, assistant_text)`` — post-pattern (terminal state), distils facts and writes them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config_a2a.config.models import AgentConfig
from config_a2a.memory.extractor import extract_facts
from config_a2a.memory.in_memory_store import InMemoryStore
from config_a2a.memory.sqlite_store import SqlAlchemyStore
from config_a2a.memory.store import MemoryStore
from config_a2a.memory.working import apply_sliding_summary
from config_a2a.providers.base import ChatMessage, LlmProvider

if TYPE_CHECKING:  # pragma: no cover
    pass

log = logging.getLogger(__name__)


class MemoryOrchestrator:
    """Glues config + store + provider into pre/post/inline hooks."""

    def __init__(self, config: AgentConfig, store: MemoryStore | None) -> None:
        self.config = config
        self.store = store

    @property
    def enabled(self) -> bool:
        return self.config.memory.enabled and self.store is not None

    async def inject_long_term(self, user_text: str, *, user_id: str | None = None) -> str:
        """Return a context block to prepend, or '' if nothing relevant / disabled."""
        if not self.enabled or self.store is None:
            return ""
        read_cfg = self.config.memory.long_term.read
        if read_cfg.when == "none":
            return ""
        records = await self.store.search(
            query=user_text,
            agent_name=self.config.name,
            scopes=list(read_cfg.scopes),
            top_k=read_cfg.top_k,
            user_id=user_id,
        )
        if not records:
            return ""
        lines: list[str] = []
        budget = read_cfg.max_chars
        for record in records:
            entry = f"- [{record.scope}] {record.text}"
            if len(entry) + 2 > budget:
                break
            lines.append(entry)
            budget -= len(entry) + 1
        if not lines:
            return ""
        return "Relevant memory from past interactions:\n" + "\n".join(lines)

    async def maybe_summarise(
        self, messages: list[ChatMessage], *, provider: LlmProvider
    ) -> list[ChatMessage]:
        if not self.config.memory.enabled:
            return messages
        return await apply_sliding_summary(
            messages, config=self.config.memory.working, provider=provider
        )

    async def harvest(
        self,
        *,
        user_text: str,
        assistant_text: str,
        provider: LlmProvider,
        user_id: str | None = None,
    ) -> int:
        if not self.enabled or self.store is None:
            return 0
        write_cfg = self.config.memory.long_term.write
        if write_cfg.when != "after_terminal" or write_cfg.extract_with == "none":
            return 0
        forced = None if write_cfg.scope == "infer" else write_cfg.scope
        records = await extract_facts(
            provider,
            user_text=user_text,
            assistant_text=assistant_text,
            forced_scope=forced,
        )
        for record in records:
            if record.scope == "user" and user_id:
                record.user_id = user_id
            await self.store.write(record, agent_name=self.config.name)
        return len(records)


def build_orchestrator(
    config: AgentConfig,
    *,
    session_factory=None,  # noqa: ANN001 — async_sessionmaker if backend == sqlite
) -> MemoryOrchestrator:
    if not config.memory.enabled:
        return MemoryOrchestrator(config, store=None)
    backend = config.memory.long_term.store.backend
    if backend == "in_memory":
        return MemoryOrchestrator(config, store=InMemoryStore())
    if backend == "sqlite":
        if session_factory is None:
            raise ValueError(
                "sqlite memory backend needs a session_factory; pass it from persistence layer"
            )
        return MemoryOrchestrator(config, store=SqlAlchemyStore(session_factory))
    raise ValueError(f"unknown memory backend: {backend}")


__all__ = ["MemoryOrchestrator", "build_orchestrator"]
