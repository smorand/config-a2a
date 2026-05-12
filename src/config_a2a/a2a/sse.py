"""Server-sent event emitter for A2A `message:stream` responses."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class SseEmitter:
    """Buffered async producer that yields SSE-formatted byte strings.

    Producers call `emit(...)` from the executor coroutine; the HTTP layer
    consumes via `stream()`. A heartbeat comment is sent every `heartbeat_seconds`
    to keep intermediate proxies from closing the stream.
    """

    def __init__(self, heartbeat_seconds: float = 15.0) -> None:
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._heartbeat = heartbeat_seconds
        self._closed = False

    async def emit(self, payload: dict[str, Any], event: str | None = None) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        chunk = ""
        if event:
            chunk += f"event: {event}\n"
        chunk += f"data: {body}\n\n"
        await self._queue.put(chunk.encode("utf-8"))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)

    async def stream(self) -> AsyncIterator[bytes]:
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=self._heartbeat)
            except asyncio.TimeoutError:
                yield b": heartbeat\n\n"
                continue
            if item is None:
                return
            yield item
