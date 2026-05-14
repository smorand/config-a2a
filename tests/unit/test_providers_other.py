"""Iter 6: Anthropic and Google adapter wire formats with respx."""

from __future__ import annotations

import json

import respx
from httpx import Response

from config_a2a.providers.anthropic import AnthropicProvider
from config_a2a.providers.base import ChatMessage, ChatRequest, ToolSpec
from config_a2a.providers.google import GoogleGeminiProvider


async def test_anthropic_serialises_messages_and_tools() -> None:
    provider = AnthropicProvider(model="claude-x", api_key="ak-1", base_url="https://api.anthropic.test/v1")
    try:
        with respx.mock() as router:
            route = router.post("https://api.anthropic.test/v1/messages").mock(
                return_value=Response(
                    200,
                    json={
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "tool_use", "id": "t1", "name": "fs.echo", "input": {"text": "hi"}},
                        ],
                        "usage": {"input_tokens": 3, "output_tokens": 5},
                        "stop_reason": "tool_use",
                    },
                )
            )
            response = await provider.chat(
                ChatRequest(
                    messages=[
                        ChatMessage(role="system", content="be terse"),
                        ChatMessage(role="user", content="hi"),
                    ],
                    tools=[ToolSpec(name="fs.echo", description="x", parameters={"type": "object"})],
                    max_output_tokens=64,
                )
            )
        assert response.content == "hello"
        assert response.tool_calls[0].name == "fs.echo"
        assert response.usage.input_tokens == 3
        sent = json.loads(route.calls.last.request.content)
        assert sent["system"] == "be terse"
        assert sent["max_tokens"] == 64
        assert sent["tools"][0]["input_schema"] == {"type": "object"}
        assert sent["messages"] == [{"role": "user", "content": "hi"}]
    finally:
        await provider.aclose()


async def test_google_serialises_contents_and_system() -> None:
    provider = GoogleGeminiProvider(model="gemini-x", api_key="gk-1", base_url="https://gen.test/v1beta")
    try:
        with respx.mock() as router:
            route = router.post("https://gen.test/v1beta/models/gemini-x:generateContent").mock(
                return_value=Response(
                    200,
                    json={
                        "candidates": [
                            {
                                "content": {
                                    "parts": [
                                        {"text": "bonjour"},
                                        {"functionCall": {"name": "translate", "args": {"q": "hi"}}},
                                    ]
                                }
                            }
                        ],
                        "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 6},
                    },
                )
            )
            response = await provider.chat(
                ChatRequest(
                    messages=[
                        ChatMessage(role="system", content="reply in french"),
                        ChatMessage(role="user", content="hi"),
                    ],
                )
            )
        assert response.content == "bonjour"
        assert response.tool_calls[0].name == "translate"
        sent = json.loads(route.calls.last.request.content)
        assert sent["systemInstruction"]["parts"][0]["text"] == "reply in french"
        assert sent["contents"] == [{"role": "user", "parts": [{"text": "hi"}]}]
        # API key is sent as a query param, not a header.
        assert route.calls.last.request.url.params["key"] == "gk-1"
    finally:
        await provider.aclose()
