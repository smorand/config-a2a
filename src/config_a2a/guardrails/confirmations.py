"""Destructive-tool confirmation policy → INPUT_REQUIRED handshake.

Wire format (mirrors web-a2a/docs/agent-input-required.md):

```
"statusUpdate": {
  "taskId": "<id>",
  "contextId": "<id>",
  "status": {
    "state": "TASK_STATE_INPUT_REQUIRED",
    "message": { "role": "ROLE_AGENT", "parts": [{ "text": "<prompt>" }] }
  },
  "final": false,
  "metadata": {
    "kind": "confirm_tool",
    "tool_name": "<name>",
    "arguments": { ... },
    "tool_call_id": "<id>"
  }
}
```
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from config_a2a.config.models import ConfirmationsConfig

ConfirmationPolicy = Literal["prompt", "auto_approve", "auto_deny"]


def policy_for(config: ConfirmationsConfig, tool_name: str) -> ConfirmationPolicy:
    """Return the confirmation policy for a tool name, falling back to the default."""
    return config.per_tool.get(tool_name, config.destructive_hint)


@dataclass
class PendingConfirmation:
    """State suspended while waiting for the user to approve or deny a tool call."""

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    transcript: list[dict[str, Any]]


def confirm_metadata(tool_name: str, tool_call_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "confirm_tool",
        "tool_name": tool_name,
        "arguments": arguments,
        "tool_call_id": tool_call_id,
    }


def confirm_prompt(tool_name: str, arguments: dict[str, Any]) -> str:
    args = ", ".join(f"{key}={value!r}" for key, value in sorted(arguments.items()))
    return f"About to call destructive tool `{tool_name}` with: {args}. Reply 'yes' or 'approve' to continue."


def is_approval(text: str) -> bool:
    return text.strip().lower() in {"yes", "y", "approve", "approved", "ok", "confirm"}


def is_denial(text: str) -> bool:
    return text.strip().lower() in {"no", "n", "deny", "denied", "cancel", "abort"}
