"""A2A v1.0 envelope models: messages, parts, tasks, status."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow")


TaskState = Literal[
    "TASK_STATE_SUBMITTED",
    "TASK_STATE_WORKING",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
]

Role = Literal["ROLE_USER", "ROLE_AGENT"]


class TextPart(_Loose):
    text: str


class FilePart(_Loose):
    raw: str  # base64
    filename: str | None = None
    mediaType: str | None = None  # noqa: N815 — wire format


Part = TextPart | FilePart


class Message(_Loose):
    messageId: str  # noqa: N815
    role: Role
    contextId: str | None = None  # noqa: N815
    taskId: str | None = None  # noqa: N815
    skillId: str | None = None  # noqa: N815 — wire format; selects an advertised skill
    parts: list[Part] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TaskStatus(_Loose):
    state: TaskState
    message: Message | None = None
    timestamp: str | None = None


class Task(_Loose):
    id: str
    contextId: str | None = None  # noqa: N815
    status: TaskStatus
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    history: list[Message] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendMessageRequest(_Loose):
    message: Message


def text_message(role: Role, text: str, **extra: Any) -> Message:
    """Helper to build a single-part text message."""
    msg_id = extra.pop("messageId", None)
    return Message(
        messageId=msg_id or _new_id(),
        role=role,
        parts=[TextPart(text=text)],
        **extra,
    )


def _new_id() -> str:
    import uuid

    return str(uuid.uuid4())
