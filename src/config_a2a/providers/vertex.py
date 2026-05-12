"""VertexAI Gemini adapter using Application Default Credentials (ADC).

We re-use the same wire format as Google's Generative Language API but hit
the Vertex endpoint and authenticate with a short-lived OAuth access token
minted from ADC (https://cloud.google.com/docs/authentication/application-default-credentials).
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — gcloud is the supported ADC source on macOS
from typing import Any

import httpx

from config_a2a.providers.base import (
    ChatRequest,
    ChatResponse,
    LlmProvider,
    ProviderError,
)
from config_a2a.providers.google import GoogleGeminiProvider


class VertexGeminiProvider(LlmProvider):
    """POSTs to Vertex AI's ``:generateContent`` endpoint with a bearer token."""

    name = "vertex"

    def __init__(
        self,
        *,
        model: str,
        project: str,
        location: str,
        credentials_path: str | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if not project or not location:
            raise ProviderError("vertex provider requires `project` and `location`")
        self._model = model
        self._project = project
        self._location = location
        self._credentials_path = credentials_path
        if credentials_path:
            os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", credentials_path)
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _token(self) -> str:
        """Mint an access token. Tries google.auth; falls back to `gcloud`."""
        try:
            from google.auth import default  # type: ignore[import-not-found]
            from google.auth.transport.requests import Request  # type: ignore[import-not-found]

            creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(Request())
            return creds.token  # type: ignore[no-any-return]
        except Exception:  # pylint: disable=broad-except
            try:
                out = subprocess.check_output(  # nosec B603, B607
                    ["gcloud", "auth", "application-default", "print-access-token"],
                    text=True,
                )
                return out.strip()
            except Exception as exc:  # pylint: disable=broad-except
                raise ProviderError(f"could not obtain ADC token: {exc}") from exc

    async def chat(self, request: ChatRequest) -> ChatResponse:
        # Reuse Google's payload shaping; just change endpoint + auth.
        system, contents = GoogleGeminiProvider._to_contents(request.messages)
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
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.parameters,
                        }
                        for tool in request.tools
                    ]
                }
            ]
        payload.update(request.extra)

        url = (
            f"https://{self._location}-aiplatform.googleapis.com/v1/projects/"
            f"{self._project}/locations/{self._location}/publishers/google/models/"
            f"{request.model or self._model}:generateContent"
        )
        token = self._token()
        try:
            response = await self._client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"vertex transport error: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"vertex {response.status_code}: {response.text[:500]}")
        # Re-use Google's parsing by hand-rolling a small shim.
        data = response.json()
        text_chunks: list[str] = []
        from config_a2a.providers.base import ToolCall, TokenUsage  # local to avoid cycles

        tool_calls: list[ToolCall] = []
        for candidate in data.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                if "text" in part:
                    text_chunks.append(part["text"])
                elif "functionCall" in part:
                    call = part["functionCall"]
                    tool_calls.append(
                        ToolCall(id=call.get("name", ""), name=call.get("name", ""), arguments=call.get("args") or {})
                    )
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


def build_vertex(
    *,
    model: str,
    project: str | None,
    location: str | None,
    credentials_path: str | None,
) -> VertexGeminiProvider:
    if not project or not location:
        raise ProviderError("vertex provider requires `project` and `location` in the YAML model block")
    return VertexGeminiProvider(
        model=model,
        project=project,
        location=location,
        credentials_path=credentials_path,
    )


__all__ = ["VertexGeminiProvider", "build_vertex", "json"]  # `json` re-export silences mypy on import-only modules
