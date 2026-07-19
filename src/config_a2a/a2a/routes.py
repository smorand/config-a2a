"""FastAPI router exposing the A2A v1.0 protocol surface for one agent slug.

Each agent gets its own router mounted at ``/agents/<slug>``; ``create_router``
takes the slug so handlers can resolve the right runtime out of the server
registry on the FastAPI app state.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from config_a2a.a2a.card import build_agent_card
from config_a2a.a2a.envelope import Message, SendMessageRequest, Task, TaskStatus, text_message
from config_a2a.a2a.sse import SseEmitter
from config_a2a.runtime import AgentRuntime, TaskRecord


def _user_text(message: Message) -> str:
    chunks = [part.text for part in message.parts if hasattr(part, "text")]
    return "\n".join(chunks).strip()


def _mount_id(message: Message) -> str | None:
    """Per-message JuiceFS ``mount_id`` override carried in A2A metadata.

    Lets another UI / API caller pick the active volume per conversation without
    editing the agent YAML. Falls back to the agent's ``default_mount_id``.
    """
    value = message.metadata.get("mount_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _base_url_for_agent(request: Request, slug: str) -> str:
    root = str(request.base_url).rstrip("/")
    return f"{root}/agents/{slug}"


def _unknown_skill_response(skill_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "unknown skill", "skill_id": skill_id},
    )


def _validate_skill_id(payload: SendMessageRequest, runtime: AgentRuntime) -> JSONResponse | None:
    """Validate ``message.skillId`` against the agent's declared skills.

    Empty / missing ``skillId`` is accepted (default behaviour, layer-1 contract).
    Returns a 400 ``JSONResponse`` if the id is unknown, ``None`` otherwise.
    """
    skill_id = payload.message.skillId
    if not skill_id:
        return None
    declared = {s.id for s in runtime.config.skills}
    if skill_id in declared:
        return None
    return _unknown_skill_response(skill_id)


def _task_to_dict(record: TaskRecord) -> dict[str, Any]:
    task = Task(
        id=record.id,
        contextId=record.context_id,
        status=record.status,
        artifacts=record.artifacts,
        history=record.history,
        metadata=record.metadata,
    )
    return task.model_dump()


def create_router(slug: str) -> APIRouter:
    """Build a per-agent A2A router. Mounted with ``prefix=/agents/<slug>``."""

    def _resolve_runtime(request: Request) -> AgentRuntime:
        server = getattr(request.app.state, "server", None)
        if server is None:  # pragma: no cover — safety net
            raise HTTPException(status_code=500, detail="Server registry not initialised")
        runtime = server.get_runtime(slug)
        if runtime is None:
            raise HTTPException(status_code=404, detail=f"agent {slug!r} not found")
        return runtime

    router = APIRouter()

    @router.get("/.well-known/agent-card.json", tags=["a2a"])
    async def agent_card(
        request: Request,
        runtime: AgentRuntime = Depends(_resolve_runtime),
    ) -> JSONResponse:
        server = request.app.state.server
        return JSONResponse(
            build_agent_card(
                runtime.config,
                _base_url_for_agent(request, slug),
                server_card=server.config.card,
            )
        )

    # Legacy alias for older A2A clients.
    @router.get("/.well-known/a2a/agent-card", tags=["a2a"])
    async def agent_card_legacy(
        request: Request,
        runtime: AgentRuntime = Depends(_resolve_runtime),
    ) -> JSONResponse:
        server = request.app.state.server
        return JSONResponse(
            build_agent_card(
                runtime.config,
                _base_url_for_agent(request, slug),
                server_card=server.config.card,
            )
        )

    async def _resolve_task(payload: SendMessageRequest, runtime: AgentRuntime) -> Any:
        if payload.message.taskId:
            existing = await runtime.tasks.get(payload.message.taskId)
            if existing is None:
                raise HTTPException(status_code=404, detail="taskId not found")
            return existing
        return await runtime.tasks.create(context_id=payload.message.contextId)

    @router.post("/message:send", tags=["a2a"])
    async def send_message(
        payload: SendMessageRequest,
        runtime: AgentRuntime = Depends(_resolve_runtime),
    ) -> JSONResponse:
        invalid = _validate_skill_id(payload, runtime)
        if invalid is not None:
            return invalid
        record = await _resolve_task(payload, runtime)
        emitter = SseEmitter()
        user_text = _user_text(payload.message)
        skill_id = payload.message.skillId
        mount_id = _mount_id(payload.message)
        await runtime.tasks.append_message(record.id, payload.message)
        import asyncio

        async def drain() -> None:
            async for _ in emitter.stream():
                pass

        producer = asyncio.create_task(
            runtime.run_message(user_text, emitter, record, skill_id=skill_id, mount_id=mount_id)
        )
        consumer = asyncio.create_task(drain())
        await asyncio.gather(producer, consumer)
        refreshed = await runtime.tasks.get(record.id) or record
        return JSONResponse({"task": _task_to_dict(refreshed)})

    @router.post("/message:stream", tags=["a2a"], response_model=None)
    async def stream_message(
        payload: SendMessageRequest,
        runtime: AgentRuntime = Depends(_resolve_runtime),
    ) -> StreamingResponse | JSONResponse:
        invalid = _validate_skill_id(payload, runtime)
        if invalid is not None:
            return invalid
        record = await _resolve_task(payload, runtime)
        await runtime.tasks.append_message(record.id, payload.message)
        emitter = SseEmitter()
        user_text = _user_text(payload.message)
        skill_id = payload.message.skillId
        mount_id = _mount_id(payload.message)

        import asyncio

        async def producer() -> None:
            try:
                await runtime.run_message(user_text, emitter, record, skill_id=skill_id, mount_id=mount_id)
            except Exception as exc:  # pylint: disable=broad-except
                failed = TaskStatus(
                    state="TASK_STATE_FAILED",
                    message=text_message("ROLE_AGENT", f"Internal error: {exc}"),
                )
                await runtime.tasks.update_status(record.id, failed)
                await emitter.emit(
                    {
                        "statusUpdate": {
                            "taskId": record.id,
                            "contextId": record.context_id,
                            "status": failed.model_dump(),
                            "final": True,
                        }
                    },
                    event="statusUpdate",
                )
                await emitter.close()

        asyncio.create_task(producer())
        return StreamingResponse(emitter.stream(), media_type="text/event-stream")

    @router.get("/tasks/{task_id}", tags=["a2a"])
    async def get_task(
        task_id: str,
        runtime: AgentRuntime = Depends(_resolve_runtime),
    ) -> JSONResponse:
        record = await runtime.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return JSONResponse(_task_to_dict(record))

    @router.post("/tasks/{task_id}:cancel", tags=["a2a"])
    async def cancel_task(
        task_id: str,
        runtime: AgentRuntime = Depends(_resolve_runtime),
    ) -> JSONResponse:
        record = await runtime.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        canceled = TaskStatus(state="TASK_STATE_CANCELED")
        await runtime.tasks.update_status(task_id, canceled)
        record.status = canceled
        return JSONResponse(_task_to_dict(record))

    @router.get("/tasks", tags=["a2a"])
    async def list_tasks(
        runtime: AgentRuntime = Depends(_resolve_runtime),
    ) -> JSONResponse:
        records = await runtime.tasks.list_recent()
        return JSONResponse({"tasks": [_task_to_dict(r) for r in records]})

    return router


__all__ = ["create_router"]
