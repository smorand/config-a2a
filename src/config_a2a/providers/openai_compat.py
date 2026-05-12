"""OpenAI-compatible chat completions adapter (OpenRouter, llama.cpp, vLLM)."""

from __future__ import annotations

import json
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


class OpenAiCompatibleProvider(LlmProvider):
    """Talks to any `{base_url}/chat/completions` that follows OpenAI's schema."""

    name = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None,
        extra_headers: dict[str, str] | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._extra_headers = dict(extra_headers or {})
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        headers.update(self._extra_headers)
        return headers

    @staticmethod
    def _serialise_message(msg: ChatMessage) -> dict[str, Any]:
        out: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.tool_call_id:
            out["tool_call_id"] = msg.tool_call_id
        if msg.name:
            out["name"] = msg.name
        if msg.tool_calls:
            out["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in msg.tool_calls
            ]
        return out

    async def chat(self, request: ChatRequest) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": request.model or self._model,
            "messages": [self._serialise_message(m) for m in request.messages],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        payload.update(request.extra)

        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai-compat transport error: {exc}") from exc

        if response.status_code >= 400:
            raise ProviderError(
                f"openai-compat {response.status_code}: {response.text[:500]}"
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=raw.get("id") or "", name=function.get("name") or "", arguments=arguments)
            )
        usage = data.get("usage") or {}
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                output_tokens=int(usage.get("completion_tokens", 0) or 0),
                cost_usd=usage.get("cost"),
            ),
            finish_reason=choice.get("finish_reason") or "stop",
            raw=data,
        )


def build_openai_compatible(
    *, model: str, base_url: str, api_key_env: str | None, extra_headers: dict[str, str] | None
) -> OpenAiCompatibleProvider:
    """Factory helper that resolves the API key from the environment."""
    api_key = os.environ.get(api_key_env) if api_key_env else None
    return OpenAiCompatibleProvider(
        model=model, base_url=base_url, api_key=api_key, extra_headers=extra_headers
    )
