"""FastAPI router exposing the A2A v1.0 protocol surface."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from config_a2a.a2a.card import build_agent_card
from config_a2a.a2a.envelope import Message, SendMessageRequest, Task, TaskStatus, text_message
from config_a2a.a2a.sse import SseEmitter
from config_a2a.runtime import AgentRuntime, TaskRecord


def _runtime(request: Request) -> AgentRuntime:
    runtime = request.app.state.runtime
    if runtime is None:  # pragma: no cover - safety net
        raise HTTPException(status_code=500, detail="Runtime not initialised")
    return runtime


def _user_text(message: Message) -> str:
    chunks = [part.text for part in message.parts if hasattr(part, "text")]
    return "\n".join(chunks).strip()


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


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


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/.well-known/a2a/agent-card", tags=["a2a"])
    async def agent_card(request: Request, runtime: AgentRuntime = Depends(_runtime)) -> JSONResponse:
        return JSONResponse(build_agent_card(runtime.config, _base_url(request)))

    # Alias paths used by some clients.
    @router.get("/.well-known/agent-card.json", tags=["a2a"])
    async def agent_card_alias(request: Request, runtime: AgentRuntime = Depends(_runtime)) -> JSONResponse:
        return JSONResponse(build_agent_card(runtime.config, _base_url(request)))

    @router.post("/message:send", tags=["a2a"])
    async def send_message(
        payload: SendMessageRequest, runtime: AgentRuntime = Depends(_runtime)
    ) -> JSONResponse:
        record = await runtime.tasks.create(context_id=payload.message.contextId)
        emitter = SseEmitter()
        user_text = _user_text(payload.message)
        await runtime.tasks.append_message(record.id, payload.message)
        # Drain the emitter inline for synchronous send.
        import asyncio

        async def drain() -> None:
            async for _ in emitter.stream():
                pass

        producer = asyncio.create_task(runtime.run_message(user_text, emitter, record))
        consumer = asyncio.create_task(drain())
        await asyncio.gather(producer, consumer)
        return JSONResponse(_task_to_dict(record))

    @router.post("/message:stream", tags=["a2a"])
    async def stream_message(
        payload: SendMessageRequest, runtime: AgentRuntime = Depends(_runtime)
    ) -> StreamingResponse:
        record = await runtime.tasks.create(context_id=payload.message.contextId)
        await runtime.tasks.append_message(record.id, payload.message)
        emitter = SseEmitter()
        user_text = _user_text(payload.message)

        import asyncio

        async def producer() -> None:
            try:
                await runtime.run_message(user_text, emitter, record)
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
    async def get_task(task_id: str, runtime: AgentRuntime = Depends(_runtime)) -> JSONResponse:
        record = await runtime.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return JSONResponse(_task_to_dict(record))

    @router.post("/tasks/{task_id}:cancel", tags=["a2a"])
    async def cancel_task(task_id: str, runtime: AgentRuntime = Depends(_runtime)) -> JSONResponse:
        record = await runtime.tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        canceled = TaskStatus(state="TASK_STATE_CANCELED")
        await runtime.tasks.update_status(task_id, canceled)
        record.status = canceled
        return JSONResponse(_task_to_dict(record))

    @router.get("/tasks", tags=["a2a"])
    async def list_tasks(runtime: AgentRuntime = Depends(_runtime)) -> JSONResponse:
        records = await runtime.tasks.list_recent()
        return JSONResponse({"tasks": [_task_to_dict(r) for r in records]})

    @router.get("/health", tags=["system"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"}, status_code=status.HTTP_200_OK)

    return router
