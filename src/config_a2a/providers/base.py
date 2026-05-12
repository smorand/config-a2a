"""Provider-agnostic chat interface used by every executor."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal


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
