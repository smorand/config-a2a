"""Provider-boundary tool-name sanitization (dotted MCP names -> wire-safe).

Every major provider constrains function names to ``^[a-zA-Z0-9_-]+$`` (<=64
chars). Internally config-a2a keeps dotted qualified names (``juicefs.fs.read``);
they are sanitized only when serializing the outbound request and remapped back
when parsing the response.
"""

from __future__ import annotations

import json

import respx
from httpx import Response

from config_a2a.config.models import ConfirmationsConfig
from config_a2a.guardrails.confirmations import policy_for
from config_a2a.providers.anthropic import AnthropicProvider
from config_a2a.providers.base import (
    ChatMessage,
    ChatRequest,
    ToolCall,
    ToolNameCodec,
    ToolSpec,
    sanitize_tool_name,
)
from config_a2a.providers.openai_compat import OpenAiCompatibleProvider

# --- helper -----------------------------------------------------------------


def test_sanitize_replaces_dots_with_underscore() -> None:
    assert sanitize_tool_name("juicefs.fs.read") == "juicefs_fs_read"


def test_sanitize_keeps_already_valid_names() -> None:
    assert sanitize_tool_name("fs_read") == "fs_read"
    assert sanitize_tool_name("server-1_tool") == "server-1_tool"


def test_sanitize_replaces_all_illegal_chars() -> None:
    assert sanitize_tool_name("a.b:c/d e") == "a_b_c_d_e"


def test_sanitize_truncates_to_64() -> None:
    long = "x." * 40  # 80 chars, dots become underscores
    out = sanitize_tool_name(long)
    assert len(out) == 64


# --- codec ------------------------------------------------------------------


def test_codec_round_trip() -> None:
    codec = ToolNameCodec([ToolSpec(name="juicefs.fs.read", description="", parameters={})])
    assert codec.to_wire("juicefs.fs.read") == "juicefs_fs_read"
    assert codec.from_wire("juicefs_fs_read") == "juicefs.fs.read"


def test_codec_unknown_from_wire_is_passthrough() -> None:
    codec = ToolNameCodec([])
    assert codec.from_wire("whatever") == "whatever"


def test_codec_to_wire_registers_unknown_history_tool() -> None:
    codec = ToolNameCodec([])  # tool not in this request's tool list
    wire = codec.to_wire("juicefs.fs.write")
    assert wire == "juicefs_fs_write"
    assert codec.from_wire(wire) == "juicefs.fs.write"


def test_codec_disambiguates_collisions() -> None:
    codec = ToolNameCodec(
        [
            ToolSpec(name="juicefs.fs.read", description="", parameters={}),
            ToolSpec(name="juicefs:fs:read", description="", parameters={}),
        ]
    )
    a = codec.to_wire("juicefs.fs.read")
    b = codec.to_wire("juicefs:fs:read")
    assert a == "juicefs_fs_read"
    assert b != a  # disambiguated
    # Both remap back to their distinct qualified names.
    assert codec.from_wire(a) == "juicefs.fs.read"
    assert codec.from_wire(b) == "juicefs:fs:read"


# --- openai_compat end-to-end ----------------------------------------------


async def test_openai_compat_sanitizes_outbound_and_remaps_response() -> None:
    provider = OpenAiCompatibleProvider(model="m", base_url="https://ex.test/v1", api_key="sk")
    try:
        with respx.mock() as router:
            route = router.post("https://ex.test/v1/chat/completions").mock(
                return_value=Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "type": "function",
                                            "function": {"name": "juicefs_fs_read", "arguments": "{}"},
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                )
            )
            response = await provider.chat(
                ChatRequest(
                    messages=[ChatMessage(role="user", content="read it")],
                    tools=[ToolSpec(name="juicefs.fs.read", description="x", parameters={"type": "object"})],
                )
            )
            sent = json.loads(route.calls.last.request.content)
            # (a) tool declaration name is sanitized on the wire.
            assert sent["tools"][0]["function"]["name"] == "juicefs_fs_read"
            # response tool_call name is remapped back to the dotted form.
            assert response.tool_calls[0].name == "juicefs.fs.read"
    finally:
        await provider.aclose()


async def test_openai_compat_history_round_trip() -> None:
    """Assistant tool_calls (b) and tool-result name (c) are both sanitized."""
    provider = OpenAiCompatibleProvider(model="m", base_url="https://ex.test/v1", api_key="sk")
    try:
        with respx.mock() as router:
            route = router.post("https://ex.test/v1/chat/completions").mock(
                return_value=Response(
                    200,
                    json={
                        "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    },
                )
            )
            await provider.chat(
                ChatRequest(
                    messages=[
                        ChatMessage(role="user", content="read it"),
                        ChatMessage(
                            role="assistant",
                            content="",
                            tool_calls=[ToolCall(id="c1", name="juicefs.fs.read", arguments={})],
                        ),
                        ChatMessage(
                            role="tool",
                            content="file body",
                            name="juicefs.fs.read",
                            tool_call_id="c1",
                        ),
                    ],
                    tools=[ToolSpec(name="juicefs.fs.read", description="x", parameters={"type": "object"})],
                )
            )
            sent = json.loads(route.calls.last.request.content)
            assistant_msg = sent["messages"][1]
            tool_msg = sent["messages"][2]
            assert assistant_msg["tool_calls"][0]["function"]["name"] == "juicefs_fs_read"
            assert tool_msg["name"] == "juicefs_fs_read"
    finally:
        await provider.aclose()


async def test_anthropic_sanitizes_tool_declaration() -> None:
    provider = AnthropicProvider(model="c", api_key="ak", base_url="https://an.test/v1")
    try:
        with respx.mock() as router:
            route = router.post("https://an.test/v1/messages").mock(
                return_value=Response(
                    200,
                    json={
                        "content": [{"type": "tool_use", "id": "t1", "name": "juicefs_fs_read", "input": {}}],
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "stop_reason": "tool_use",
                    },
                )
            )
            response = await provider.chat(
                ChatRequest(
                    messages=[ChatMessage(role="user", content="go")],
                    tools=[ToolSpec(name="juicefs.fs.read", description="x", parameters={"type": "object"})],
                    max_output_tokens=32,
                )
            )
            sent = json.loads(route.calls.last.request.content)
            assert sent["tools"][0]["name"] == "juicefs_fs_read"
            assert response.tool_calls[0].name == "juicefs.fs.read"
    finally:
        await provider.aclose()


# --- confirmations.per_tool stays dotted ------------------------------------


def test_per_tool_confirmation_matches_remapped_name() -> None:
    """The remapped (dotted) name matches dotted per_tool keys, unchanged."""
    config = ConfirmationsConfig(destructive_hint="auto_approve", per_tool={"juicefs.fs.delete": "prompt"})
    codec = ToolNameCodec([ToolSpec(name="juicefs.fs.delete", description="", parameters={})])
    remapped = codec.from_wire("juicefs_fs_delete")
    assert remapped == "juicefs.fs.delete"
    assert policy_for(config, remapped) == "prompt"
    # The sanitized wire name would NOT match (proving why we remap first).
    assert policy_for(config, "juicefs_fs_delete") == "auto_approve"
