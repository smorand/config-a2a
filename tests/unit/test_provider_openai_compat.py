"""Iter 2: openai-compat provider speaks the right wire format."""

from __future__ import annotations

import respx
from httpx import Response

from config_a2a.providers.base import ChatMessage, ChatRequest, ToolSpec
from config_a2a.providers.openai_compat import OpenAiCompatibleProvider


async def test_openai_compat_chat_roundtrip() -> None:
    provider = OpenAiCompatibleProvider(
        model="some/model",
        base_url="https://example.test/v1",
        api_key="sk-test",
        extra_headers={"X-Title": "config-a2a"},
    )
    try:
        with respx.mock(assert_all_called=True) as router:
            route = router.post("https://example.test/v1/chat/completions").mock(
                return_value=Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "hi there"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.0},
                    },
                )
            )
            request = ChatRequest(
                messages=[
                    ChatMessage(role="system", content="be terse"),
                    ChatMessage(role="user", content="ping"),
                ],
                tools=[ToolSpec(name="echo", description="x", parameters={"type": "object"})],
            )
            response = await provider.chat(request)
            assert response.content == "hi there"
            assert response.usage.input_tokens == 3
            assert response.finish_reason == "stop"
            sent = route.calls.last.request
            assert sent.headers["Authorization"] == "Bearer sk-test"
            assert sent.headers["X-Title"] == "config-a2a"
            import json

            body = json.loads(sent.content)
            assert body["model"] == "some/model"
            assert body["messages"][0]["role"] == "system"
            assert body["tools"][0]["function"]["name"] == "echo"
    finally:
        await provider.aclose()


async def test_openai_compat_propagates_errors() -> None:
    provider = OpenAiCompatibleProvider(model="m", base_url="https://example.test/v1", api_key=None)
    try:
        with respx.mock() as router:
            router.post("https://example.test/v1/chat/completions").mock(return_value=Response(500, text="boom"))
            import pytest

            from config_a2a.providers.base import ProviderError

            with pytest.raises(ProviderError):
                await provider.chat(ChatRequest(messages=[ChatMessage(role="user", content="hi")]))
    finally:
        await provider.aclose()
