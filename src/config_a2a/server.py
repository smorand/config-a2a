"""Server container: registry of agents, mount / unmount / reload operations.

One ``Server`` lives per FastAPI process; it owns the shared persistence engine,
the shared MCP discovery results, and a dict of ``AgentRuntime`` instances keyed
by slug. The FastAPI app is mutated in place when agents are loaded or
unloaded via the admin REST API.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from config_a2a.a2a.routes import create_router
from config_a2a.config.loader import ConfigError
from config_a2a.config.models import AgentConfig, AuthenticationConfig, ServerConfig
from config_a2a.memory import build_orchestrator
from config_a2a.persistence import build_session_factory_for, build_task_store
from config_a2a.persistence.engine import build_engine
from config_a2a.runtime import AgentRuntime

log = logging.getLogger(__name__)


@dataclass
class _RuntimeEntry:
    runtime: AgentRuntime
    router_id: int  # id() of the mounted router for unmount
    loaded_at: float = field(default_factory=time.monotonic)
    last_task_at: float | None = None
    in_flight: int = 0
    status: str = "ready"  # "ready" | "degraded"


@dataclass
class _ReloadOp:
    id: str
    slug: str
    status: str = "pending"  # "pending" | "running" | "completed" | "failed"
    error: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


class Server:
    """Owns the multi-agent runtime registry and mutates a FastAPI app in place."""

    def __init__(self, config: ServerConfig, app: FastAPI) -> None:
        self.config = config
        self.app = app
        self._runtimes: dict[str, _RuntimeEntry] = {}
        self._reloads: dict[str, _ReloadOp] = {}
        self._lock = asyncio.Lock()
        # Single shared engine (server-level persistence). Agents that override
        # `persistence:` get their own; pragmatic compromise to satisfy "single DB"
        # in the common case without breaking the override hook.
        self._engine = build_engine(config.persistence)
        self._session_factory = build_session_factory_for(config.persistence, engine=self._engine)
        self._started_at = time.monotonic()

    # ----------------------------------------------------------------- lookup

    def get_runtime(self, slug: str) -> AgentRuntime | None:
        entry = self._runtimes.get(slug)
        return entry.runtime if entry else None

    def has_agent(self, slug: str) -> bool:
        return slug in self._runtimes

    def list_agents(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        now = time.monotonic()
        for slug, entry in self._runtimes.items():
            cfg = entry.runtime.config
            out.append(
                {
                    "slug": slug,
                    "name": cfg.name,
                    "description": cfg.description,
                    "model": cfg.model.model,
                    "pattern": cfg.pattern.type,
                    "status": entry.status,
                    "uptime_seconds": int(now - entry.loaded_at),
                    "in_flight": entry.in_flight,
                    "last_task_at": entry.last_task_at,
                }
            )
        return out

    def agent_status(self, slug: str) -> dict[str, Any] | None:
        entry = self._runtimes.get(slug)
        if entry is None:
            return None
        return {
            "slug": slug,
            "status": entry.status,
            "in_flight": entry.in_flight,
            "uptime_seconds": int(time.monotonic() - entry.loaded_at),
            "last_task_at": entry.last_task_at,
        }

    def server_status(self) -> dict[str, Any]:
        return {
            "name": self.config.name,
            "version": self.config.version,
            "uptime_seconds": int(time.monotonic() - self._started_at),
            "agents_loaded": len(self._runtimes),
            "in_flight": sum(e.in_flight for e in self._runtimes.values()),
        }

    # -------------------------------------------------------------- lifecycle

    async def load_agent(self, agent: AgentConfig) -> _RuntimeEntry:
        """Atomically build an AgentRuntime, discover MCP tools, mount the router.

        Raises on any failure — no zombie state is left behind.
        """
        async with self._lock:
            assert agent.slug is not None
            if agent.slug in self._runtimes:
                raise ValueError(f"agent slug already loaded: {agent.slug!r}")
            # Fill in defaults if not present (e.g. agent loaded from /admin/agents)
            if agent.persistence is None:
                agent.persistence = self.config.persistence
            if agent.authentication is None:
                agent.authentication = AuthenticationConfig()
            # Build runtime + MCP + memory
            try:
                tasks = build_task_store(agent, session_factory=self._session_factory)
                memory = None
                if agent.memory.enabled:
                    if agent.memory.long_term.store.backend == "sqlite":
                        memory = build_orchestrator(agent, session_factory=self._session_factory)
                    else:
                        memory = build_orchestrator(agent)
                runtime = AgentRuntime(agent, tasks=tasks, memory=memory)
                if agent.tools.mcp_servers:
                    await runtime.discover_tools()
            except Exception as exc:  # broad on purpose — atomic load
                # Best-effort cleanup
                try:
                    if "runtime" in locals():
                        await runtime.aclose()
                except Exception:  # pragma: no cover — cleanup best effort
                    pass
                raise ConfigError(f"failed to load agent {agent.slug!r}: {exc}") from exc

            router = create_router(agent.slug)
            self.app.include_router(router, prefix=f"/agents/{agent.slug}")
            entry = _RuntimeEntry(runtime=runtime, router_id=id(router))
            self._runtimes[agent.slug] = entry
            # Append to canonical config for directory output.
            if agent not in self.config.agents:
                self.config.agents.append(agent)
            return entry

    async def unload_agent(self, slug: str) -> None:
        async with self._lock:
            entry = self._runtimes.pop(slug, None)
            if entry is None:
                raise KeyError(slug)
            # Remove from canonical config list
            self.config.agents = [a for a in self.config.agents if a.slug != slug]
            # Drain in-flight (best-effort: AgentRuntime currently has no cancel),
            # close MCP children, drop routes.
            try:
                await entry.runtime.mcp.aclose()
            except Exception as exc:  # pylint: disable=broad-except
                log.warning("mcp aclose failed for %s: %s", slug, exc)
            try:
                await entry.runtime.aclose()
            except Exception as exc:  # pylint: disable=broad-except
                log.warning("runtime aclose failed for %s: %s", slug, exc)
        # Remove the mounted routes for this slug
        self._strip_routes_for_slug(slug)

    def _strip_routes_for_slug(self, slug: str) -> None:
        prefix = f"/agents/{slug}/"
        prefix_root = f"/agents/{slug}"
        keep = []
        for route in self.app.router.routes:
            path = getattr(route, "path", "")
            if path == prefix_root or path.startswith(prefix):
                continue
            keep.append(route)
        self.app.router.routes[:] = keep

    async def reload_agent(self, slug: str, new_config: AgentConfig) -> None:
        """Hot-reload an agent: unload, then load the replacement config."""
        if not self.has_agent(slug):
            raise KeyError(slug)
        await self.unload_agent(slug)
        await self.load_agent(new_config)

    # ----------------------------------------------------------- async reload

    def schedule_reload(self, slug: str, new_config: AgentConfig, *, op_id: str | None = None) -> _ReloadOp:
        op = _ReloadOp(id=op_id or str(uuid.uuid4()), slug=slug)
        self._reloads[op.id] = op

        async def _runner() -> None:
            op.status = "running"
            try:
                await self.reload_agent(slug, new_config)
                op.status = "completed"
            except Exception as exc:  # pylint: disable=broad-except
                op.status = "failed"
                op.error = str(exc)
            finally:
                op.finished_at = datetime.now(timezone.utc)

        asyncio.create_task(_runner())
        return op

    def get_reload(self, op_id: str) -> _ReloadOp | None:
        return self._reloads.get(op_id)

    # --------------------------------------------------------------- shutdown

    async def aclose(self) -> None:
        for slug in list(self._runtimes.keys()):
            try:
                await self.unload_agent(slug)
            except Exception as exc:  # pragma: no cover — best-effort
                log.warning("unload during shutdown failed for %s: %s", slug, exc)
        try:
            await self._engine.dispose()
        except Exception as exc:  # pragma: no cover — best-effort
            log.warning("engine dispose failed: %s", exc)


__all__ = ["Server"]
