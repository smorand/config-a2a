"""Anthropic Messages API adapter (https://docs.anthropic.com/en/api/messages)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from config_a2a.providers.base import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LlmProvider,
    ProviderError,
    TokenUsage,
    ToolCall,
)


class AnthropicProvider(LlmProvider):
    """Talks to https://api.anthropic.com/v1/messages."""

    name = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str = "https://api.anthropic.com/v1",
        anthropic_version: str = "2023-06-01",
        timeout_seconds: float = 180.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._version = anthropic_version
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key or "",
            "anthropic-version": self._version,
        }

    @staticmethod
    def _split_messages(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
        system_chunks: list[str] = []
        out: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                system_chunks.append(msg.content)
                continue
            if msg.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.tool_call_id or "",
                                "content": msg.content,
                            }
                        ],
                    }
                )
                continue
            if msg.role == "assistant" and msg.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": [
                            *([{"type": "text", "text": msg.content}] if msg.content else []),
                            *[
                                {
                                    "type": "tool_use",
                                    "id": tc.id,
                                    "name": tc.name,
                                    "input": tc.arguments,
                                }
                                for tc in msg.tool_calls
                            ],
                        ],
                    }
                )
                continue
            out.append({"role": msg.role, "content": msg.content})
        return "\n\n".join(system_chunks), out

    async def chat(self, request: ChatRequest) -> ChatResponse:
        system, messages = self._split_messages(request.messages)
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": messages,
            "max_tokens": request.max_output_tokens or 4096,
        }
        if system:
            payload["system"] = system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.parameters,
                }
                for tool in request.tools
            ]
        payload.update(request.extra)

        try:
            response = await self._client.post(
                f"{self._base_url}/messages",
                json=payload,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic transport error: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"anthropic {response.status_code}: {response.text[:500]}")
        data = response.json()
        content_parts = data.get("content") or []
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in content_parts:
            kind = part.get("type")
            if kind == "text":
                text_chunks.append(part.get("text") or "")
            elif kind == "tool_use":
                tool_calls.append(
                    ToolCall(id=part.get("id") or "", name=part.get("name") or "", arguments=part.get("input") or {})
                )
        usage = data.get("usage") or {}
        return ChatResponse(
            content="".join(text_chunks),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=int(usage.get("input_tokens", 0) or 0),
                output_tokens=int(usage.get("output_tokens", 0) or 0),
            ),
            finish_reason=data.get("stop_reason") or "stop",
            raw=data,
        )


def build_anthropic(*, model: str, api_key_env: str | None, base_url: str | None) -> AnthropicProvider:
    api_key = os.environ.get(api_key_env) if api_key_env else None
    return AnthropicProvider(
        model=model,
        api_key=api_key,
        base_url=base_url or "https://api.anthropic.com/v1",
    )
