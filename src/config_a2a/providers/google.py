"""Google Generative Language (Gemini) API-key adapter."""

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
    ToolNameCodec,
)


class GoogleGeminiProvider(LlmProvider):
    """Posts to https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent."""

    name = "google"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds: float = 180.0,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _to_contents(messages: list[ChatMessage], codec: ToolNameCodec) -> tuple[str, list[dict[str, Any]]]:
        system_chunks: list[str] = []
        contents: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                system_chunks.append(msg.content)
                continue
            role = "user" if msg.role in ("user", "tool") else "model"
            parts: list[dict[str, Any]] = []
            if msg.role == "assistant" and msg.tool_calls:
                if msg.content:
                    parts.append({"text": msg.content})
                for tc in msg.tool_calls:
                    parts.append({"functionCall": {"name": codec.to_wire(tc.name), "args": tc.arguments}})
            elif msg.role == "tool":
                parts.append(
                    {
                        "functionResponse": {
                            "name": codec.to_wire(msg.name or ""),
                            "response": {"content": msg.content},
                        }
                    }
                )
            else:
                parts.append({"text": msg.content})
            contents.append({"role": role, "parts": parts})
        return "\n\n".join(system_chunks), contents

    async def chat(self, request: ChatRequest) -> ChatResponse:
        codec = ToolNameCodec(request.tools)
        system, contents = self._to_contents(request.messages, codec)
        model = request.model or self._model
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"role": "system", "parts": [{"text": system}]}
        generation_config: dict[str, Any] = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_output_tokens
        if generation_config:
            payload["generationConfig"] = generation_config
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": codec.to_wire(tool.name),
                            "description": tool.description,
                            "parameters": tool.parameters,
                        }
                        for tool in request.tools
                    ]
                }
            ]
        payload.update(request.extra)

        url = f"{self._base_url}/models/{model}:generateContent"
        params = {"key": self._api_key} if self._api_key else {}
        try:
            response = await self._client.post(url, json=payload, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"google transport error: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"google {response.status_code}: {response.text[:500]}")
        data = response.json()
        candidates = data.get("candidates") or []
        text_chunks: list[str] = []
        tool_calls: list[ToolCall] = []
        for candidate in candidates:
            for part in (candidate.get("content") or {}).get("parts") or []:
                if "text" in part:
                    text_chunks.append(part["text"])
                elif "functionCall" in part:
                    call = part["functionCall"]
                    qualified = codec.from_wire(call.get("name", ""))
                    tool_calls.append(ToolCall(id=qualified, name=qualified, arguments=call.get("args") or {}))
        usage = data.get("usageMetadata") or {}
        return ChatResponse(
            content="".join(text_chunks),
            tool_calls=tool_calls,
            usage=TokenUsage(
                input_tokens=int(usage.get("promptTokenCount", 0) or 0),
                output_tokens=int(usage.get("candidatesTokenCount", 0) or 0),
            ),
            finish_reason="stop",
            raw=data,
        )


def build_google(*, model: str, api_key_env: str | None, base_url: str | None) -> GoogleGeminiProvider:
    api_key = os.environ.get(api_key_env) if api_key_env else None
    return GoogleGeminiProvider(
        model=model,
        api_key=api_key,
        base_url=base_url or "https://generativelanguage.googleapis.com/v1beta",
    )
