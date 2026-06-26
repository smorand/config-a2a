"""FastAPI application factory for the multi-agent server.

One FastAPI process exposes:
  * ``GET /health`` (server liveness)
  * ``GET /.well-known/agent-card.json`` (directory of mounted agents)
  * ``/agents/<slug>/...`` (per-agent A2A routes, auth per-agent)
  * ``/admin/...`` (admin REST surface, optional server-wide auth)
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

if TYPE_CHECKING:  # pragma: no cover
    from config_a2a.runtime import AgentRuntime

from config_a2a import __version__
from config_a2a.a2a.card import build_directory
from config_a2a.config.loader import ConfigError, load_server_config
from config_a2a.config.models import (
    AdminConfig,
    AgentConfig,
    AuthenticationConfig,
    ServerConfig,
)
from config_a2a.identity import DEFAULT_FORWARDED_USER_HEADER, IdentityCaptureMiddleware
from config_a2a.observability.otel import setup_otel
from config_a2a.persistence import run_migrations
from config_a2a.server import Server

_AuthCheck = Callable[[Request], Awaitable[None]]


def _auth_dependency(auth: AuthenticationConfig) -> _AuthCheck:
    """Build a FastAPI dependency enforcing the given auth config."""
    expected = os.environ.get(auth.value_env) if auth.value_env else None

    async def _check(request: Request) -> None:
        if auth.type == "none":
            return
        header = request.headers.get(auth.header_name)
        if not header or not expected:
            raise HTTPException(status_code=401, detail="missing or invalid credentials")
        provided = header.removeprefix("Bearer ").strip() if auth.type == "bearer" else header
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid credentials")

    return _check


def _agent_auth_dependency() -> _AuthCheck:
    """Per-agent auth: resolves the agent from the URL slug and checks its scheme."""

    async def _check(request: Request) -> None:
        slug = request.path_params.get("slug")
        # Path may not include {slug} when called for non-agent routes; skip.
        # We dispatch using the route prefix: extract from URL path.
        path = request.url.path
        if not path.startswith("/agents/"):
            return
        slug_from_path = path.split("/", 3)[2]
        server: Server = request.app.state.server
        runtime = server.get_runtime(slug_from_path)
        if runtime is None:
            return  # the 404 will be raised by the route handler itself
        auth = runtime.config.effective_authentication
        if auth.type == "none":
            return
        expected = os.environ.get(auth.value_env) if auth.value_env else None
        header = request.headers.get(auth.header_name)
        if not header or not expected:
            raise HTTPException(status_code=401, detail="missing or invalid credentials")
        provided = header.removeprefix("Bearer ").strip() if auth.type == "bearer" else header
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid credentials")

        del slug  # quiet linters about unused path param

    return _check


def _resolve_inbound_user_header(agents: list[AgentConfig]) -> str:
    """Pick the inbound ``X-Forwarded-User`` header name from juicefs agents.

    All juicefs agents normally share the same header; the first one wins and
    falls back to the default when no agent declares a ``juicefs:`` block.
    """
    for agent in agents:
        if agent.juicefs is not None:
            return agent.juicefs.identity.forwarded_user_header
    return DEFAULT_FORWARDED_USER_HEADER


def _build_admin_router(admin: AdminConfig) -> APIRouter:
    deps = [Depends(_auth_dependency(admin.authentication))]
    router = APIRouter(prefix="/admin", tags=["admin"], dependencies=deps)

    @router.get("/status")
    async def admin_status(request: Request) -> JSONResponse:
        server: Server = request.app.state.server
        return JSONResponse(server.server_status())

    @router.get("/agents")
    async def list_agents(request: Request) -> JSONResponse:
        server: Server = request.app.state.server
        return JSONResponse({"agents": server.list_agents()})

    @router.post("/agents", status_code=status.HTTP_201_CREATED)
    async def load_agent(request: Request, body: dict[str, Any] = Body(...)) -> JSONResponse:
        server: Server = request.app.state.server
        # Body is either an inline YAML/JSON AgentConfig OR {config_path: "..."}.
        if isinstance(body, dict) and "config_path" in body and len(body) == 1:
            raw_path = body["config_path"]
            path = Path(raw_path)
            if not path.exists():
                raise HTTPException(status_code=404, detail=f"config_path not found: {raw_path}")
            try:
                text = path.read_text(encoding="utf-8")
                data = yaml.safe_load(text)
            except Exception as exc:  # pylint: disable=broad-except
                raise HTTPException(status_code=400, detail=f"failed to read YAML: {exc}") from exc
            if not isinstance(data, dict):
                raise HTTPException(status_code=400, detail="agent YAML must be a mapping")
            agent_payload = data
        else:
            agent_payload = body
        try:
            agent = AgentConfig.model_validate(agent_payload)
        except Exception as exc:  # pylint: disable=broad-except
            raise HTTPException(status_code=400, detail=f"invalid agent config: {exc}") from exc
        assert agent.slug is not None
        if server.has_agent(agent.slug):
            raise HTTPException(status_code=409, detail=f"agent slug already loaded: {agent.slug!r}")
        try:
            entry = await server.load_agent(agent)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(
            {
                "slug": agent.slug,
                "status": entry.status,
                "url": f"/agents/{agent.slug}",
            },
            status_code=status.HTTP_201_CREATED,
        )

    @router.get("/agents/{slug}")
    async def get_agent(slug: str, request: Request) -> JSONResponse:
        server: Server = request.app.state.server
        runtime = server.get_runtime(slug)
        if runtime is None:
            raise HTTPException(status_code=404, detail=f"agent {slug!r} not found")
        cfg = runtime.config
        return JSONResponse(
            {
                "slug": slug,
                "name": cfg.name,
                "version": cfg.version,
                "description": cfg.description,
                "pattern": cfg.pattern.type,
                "model": cfg.model.model,
                "skills": [s.id for s in cfg.skills],
                "status": server.agent_status(slug),
            }
        )

    @router.get("/agents/{slug}/status")
    async def get_agent_status(slug: str, request: Request) -> JSONResponse:
        server: Server = request.app.state.server
        info = server.agent_status(slug)
        if info is None:
            raise HTTPException(status_code=404, detail=f"agent {slug!r} not found")
        return JSONResponse(info)

    @router.delete("/agents/{slug}")
    async def delete_agent(slug: str, request: Request) -> JSONResponse:
        server: Server = request.app.state.server
        if not server.has_agent(slug):
            raise HTTPException(status_code=404, detail=f"agent {slug!r} not found")
        await server.unload_agent(slug)
        return JSONResponse({"slug": slug, "status": "unloaded"})

    @router.post("/agents/{slug}/reloads", status_code=status.HTTP_202_ACCEPTED)
    async def reload_agent(slug: str, request: Request, body: dict[str, Any] = Body(...)) -> JSONResponse:
        server: Server = request.app.state.server
        if not server.has_agent(slug):
            raise HTTPException(status_code=404, detail=f"agent {slug!r} not found")
        if isinstance(body, dict) and "config_path" in body and len(body) == 1:
            try:
                data = yaml.safe_load(Path(body["config_path"]).read_text(encoding="utf-8"))
            except Exception as exc:  # pylint: disable=broad-except
                raise HTTPException(status_code=400, detail=f"failed to read YAML: {exc}") from exc
            payload = data
        else:
            payload = body
        try:
            new_config = AgentConfig.model_validate(payload)
        except Exception as exc:  # pylint: disable=broad-except
            raise HTTPException(status_code=400, detail=f"invalid agent config: {exc}") from exc
        if new_config.slug != slug:
            # Force the slug from the URL — the body is authoritative on everything else.
            new_config.slug = slug
        op = server.schedule_reload(slug, new_config)
        return JSONResponse({"id": op.id, "status": op.status}, status_code=status.HTTP_202_ACCEPTED)

    @router.get("/agents/{slug}/reloads/{op_id}")
    async def get_reload(slug: str, op_id: str, request: Request) -> JSONResponse:
        server: Server = request.app.state.server
        op = server.get_reload(op_id)
        if op is None or op.slug != slug:
            raise HTTPException(status_code=404, detail=f"reload {op_id!r} not found")
        return JSONResponse(
            {
                "id": op.id,
                "slug": op.slug,
                "status": op.status,
                "error": op.error,
                "started_at": op.started_at.isoformat(),
                "finished_at": op.finished_at.isoformat() if op.finished_at else None,
            }
        )

    return router


def _run_sync(coro: Awaitable[Any]) -> Any:
    """Drive ``coro`` to completion from a sync context.

    Works whether or not a parent event loop is already running. ``asyncio.run``
    refuses to nest, which breaks library callers (tests, REPLs, notebooks)
    where pytest-asyncio or another framework has already opened a loop.
    """
    import asyncio
    import threading

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # type: ignore[arg-type]
    # A loop is already running on this thread: punt to a fresh thread with
    # its own loop and block this thread on its completion.
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro)  # type: ignore[arg-type]
        except BaseException as exc:  # pragma: no cover — propagated below
            error["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def create_app(server_config: ServerConfig) -> FastAPI:
    """Build the multi-agent FastAPI app and mount all configured agents.

    The returned app has ``app.state.server: Server`` and a per-agent dependency
    chain. Use ``app.state.server`` for further admin operations after startup.
    """
    setup_otel(server_config)

    # Run alembic before any engine touches the schema. CLI also calls this,
    # but tests and library callers go through ``create_app`` directly, so
    # the migration must live here too.
    if server_config.persistence.run_migrations_on_start:
        try:
            run_migrations(server_config.persistence)
        except Exception as exc:  # pylint: disable=broad-except
            import logging

            logging.getLogger(__name__).warning("alembic upgrade failed: %s", exc)

    app = FastAPI(
        title=server_config.name,
        version=server_config.version,
        description=server_config.description or "config-a2a multi-agent server",
        dependencies=[Depends(_agent_auth_dependency())],
    )
    app.state.config_a2a_version = __version__
    # Capture the end-user identity at the A2A boundary so juicefs (and any
    # identity-forwarding MCP server) can act on the right person per request.
    app.add_middleware(
        IdentityCaptureMiddleware,
        header_name=_resolve_inbound_user_header(server_config.agents),
    )

    server = Server(server_config, app)
    app.state.server = server

    @app.get("/health", tags=["system"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/.well-known/agent-card.json", tags=["a2a"])
    async def directory(request: Request) -> JSONResponse:
        root = str(request.base_url).rstrip("/")
        return JSONResponse(build_directory(server.config, root))

    # Admin surface
    if server_config.admin.enabled:
        app.include_router(_build_admin_router(server_config.admin))

    # Mount per-agent routers eagerly. ``server.load_agent`` is async because
    # MCP discovery may run; we therefore need a sync-friendly way to drive it.
    # Move agents off the config list so ``load_agent`` can re-append without
    # iterating a list it mutates.
    initial_agents = list(server.config.agents)
    server.config.agents.clear()

    async def _bootstrap() -> None:
        for agent in initial_agents:
            await server.load_agent(agent)

    _run_sync(_bootstrap())
    return app


def create_app_from_path(path: Path) -> FastAPI:
    """Convenience for tests and the CLI."""
    return create_app(load_server_config(path))


def create_app_for_runtime(
    runtime: "AgentRuntime",
    *,
    server_config: ServerConfig | None = None,
) -> FastAPI:
    """Build a single-agent FastAPI app, mounting an already-constructed runtime.

    Used by unit tests that need to inject a scripted ``LlmProvider``. The
    server-level config defaults to a one-agent server named after the runtime.
    """
    cfg = runtime.config
    if server_config is None:
        server_config = ServerConfig(
            name=cfg.name,
            version=cfg.version,
            description=cfg.description,
            agents=[],  # we mount the runtime directly below
        )

    setup_otel(server_config)
    app = FastAPI(
        title=server_config.name,
        version=server_config.version,
        description=server_config.description or "config-a2a multi-agent server",
        dependencies=[Depends(_agent_auth_dependency())],
    )
    app.state.config_a2a_version = __version__
    app.add_middleware(
        IdentityCaptureMiddleware,
        header_name=_resolve_inbound_user_header([cfg]),
    )
    server = Server(server_config, app)
    app.state.server = server

    @app.get("/health", tags=["system"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/.well-known/agent-card.json", tags=["a2a"])
    async def directory(request: Request) -> JSONResponse:
        root = str(request.base_url).rstrip("/")
        return JSONResponse(build_directory(server.config, root))

    if server_config.admin.enabled:
        app.include_router(_build_admin_router(server_config.admin))

    # Manually inject the runtime + router (bypass load_agent so we keep
    # the test-provided provider / tasks / memory).
    from config_a2a.a2a.routes import create_router as _create_router
    from config_a2a.server import _RuntimeEntry

    assert cfg.slug is not None
    router = _create_router(cfg.slug)
    app.include_router(router, prefix=f"/agents/{cfg.slug}")
    entry = _RuntimeEntry(runtime=runtime, router_id=id(router))
    server._runtimes[cfg.slug] = entry  # noqa: SLF001 — test seam
    if cfg not in server.config.agents:
        server.config.agents.append(cfg)
    return app


__all__ = ["create_app", "create_app_for_runtime", "create_app_from_path"]
