"""Provider-agnostic chat interface used by every executor."""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)

# Every major LLM provider (OpenAI, Anthropic, Gemini/Vertex) constrains function
# names to ``^[a-zA-Z0-9_-]+$`` with a 64-char cap. Internally config-a2a uses
# dotted, qualified MCP names (e.g. ``juicefs.fs.read``); those dots are illegal
# on the wire. Sanitization happens only at the provider boundary; everything
# else keeps the dotted names.
_TOOL_NAME_MAX_LEN = 64
_INVALID_TOOL_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_tool_name(name: str) -> str:
    """Map any tool name to the provider-safe ``^[a-zA-Z0-9_-]+$`` form.

    Replaces every illegal character with ``_`` and truncates to 64 chars.
    Deterministic and stable; collision handling is the caller's job (see
    :class:`ToolNameCodec`).
    """
    return _INVALID_TOOL_NAME_CHARS.sub("_", name)[:_TOOL_NAME_MAX_LEN]


@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list["ToolCall"] = field(default_factory=list)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class ToolNameCodec:
    """Bidirectional, collision-safe map between qualified and wire tool names.

    Built per request from ``request.tools``. ``to_wire`` produces the
    provider-safe name to put on the outbound payload (tool declarations,
    assistant ``tool_calls`` in history, tool-result ``name``); ``from_wire``
    maps a name returned by the model back to its qualified dotted form so the
    rest of config-a2a (MCP registry dispatch, ``confirmations.per_tool``) keeps
    working unchanged.

    Two qualified names that sanitize to the same wire name are disambiguated
    with a ``_2``, ``_3``, ... suffix (deterministic by registration order).
    """

    __slots__ = ("_to_wire", "_from_wire")

    def __init__(self, tools: list["ToolSpec"] | None = None) -> None:
        self._to_wire: dict[str, str] = {}
        self._from_wire: dict[str, str] = {}
        for tool in tools or []:
            self._register(tool.name)

    def _register(self, qualified: str) -> str:
        existing = self._to_wire.get(qualified)
        if existing is not None:
            return existing
        base = sanitize_tool_name(qualified)
        wire = base
        suffix = 2
        while wire in self._from_wire and self._from_wire[wire] != qualified:
            tail = f"_{suffix}"
            wire = f"{base[: _TOOL_NAME_MAX_LEN - len(tail)]}{tail}"
            suffix += 1
        if wire != qualified:
            log.debug("sanitized tool name %r -> %r", qualified, wire)
        if wire != base:
            log.warning("tool name collision: %r sanitized to %r (already taken), using %r", qualified, base, wire)
        self._to_wire[qualified] = wire
        self._from_wire[wire] = qualified
        return wire

    def to_wire(self, qualified: str) -> str:
        """Return the provider-safe name for a qualified name (registering it)."""
        known = self._to_wire.get(qualified)
        if known is not None:
            return known
        # History may reference a tool absent from this request's tool list;
        # register on the fly so the call stays internally consistent.
        return self._register(qualified)

    def from_wire(self, wire: str) -> str:
        """Map a model-returned name back to its qualified form (best effort)."""
        return self._from_wire.get(wire, wire)


@dataclass
class ChatRequest:
    messages: list[ChatMessage]
    tools: list[ToolSpec] = field(default_factory=list)
    model: str | None = None  # overrides provider default
    temperature: float | None = None
    max_output_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = "stop"
    raw: dict[str, Any] = field(default_factory=dict)


class LlmProvider(ABC):
    """Abstract LLM provider. One instance per process; thread-safe."""

    name: str

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Send a chat completion request and return the response."""

    async def aclose(self) -> None:  # pragma: no cover — default no-op
        """Release any resources (HTTP clients, etc.)."""


class ProviderError(Exception):
    """Raised when an LLM provider returns an unrecoverable error."""
