"""Working-memory truncation: sliding window + summary of older turns."""

from __future__ import annotations

import logging

from config_a2a.config.models import WorkingMemoryConfig
from config_a2a.providers.base import ChatMessage, ChatRequest, LlmProvider

log = logging.getLogger(__name__)


async def apply_sliding_summary(
    messages: list[ChatMessage],
    *,
    config: WorkingMemoryConfig,
    provider: LlmProvider,
) -> list[ChatMessage]:
    """Return a possibly-shortened message list whose older turns are summarised.

    Strategy:
      - If total messages <= window: untouched.
      - Otherwise: take the first system message (if any), then summarise
        everything between it and the trailing `window` messages.
      - Summaries appear as a single `system` message tagged ``[memory:summary]``.
      - Re-summarisation reuses any existing summary, so the working summary
        grows additively over a long conversation (no compounded drift loss
        from re-summarising prose; raw turns can be re-attached later).
    """
    if config.strategy != "sliding_summary":
        return messages
    if len(messages) <= config.window:
        return messages

    # Preserve the leading system message if present.
    head: list[ChatMessage] = []
    if messages and messages[0].role == "system":
        head = [messages[0]]

    # Carry forward an existing summary, if any.
    existing_summary = ""
    for msg in messages[1:]:
        if msg.role == "system" and msg.content.startswith("[memory:summary]"):
            existing_summary = msg.content[len("[memory:summary]") :].lstrip()
            break

    tail = messages[-config.window :]
    middle = messages[len(head) : -config.window]
    middle = [m for m in middle if not (m.role == "system" and m.content.startswith("[memory:summary]"))]
    if not middle:
        return messages

    summary_text = await _summarise(provider, config, existing_summary, middle)
    summary_msg = ChatMessage(role="system", content=f"[memory:summary] {summary_text}")
    return [*head, summary_msg, *tail]


async def _summarise(
    provider: LlmProvider,
    config: WorkingMemoryConfig,
    existing_summary: str,
    middle: list[ChatMessage],
) -> str:
    transcript = "\n".join(f"[{m.role}] {m.content}" for m in middle if m.content)
    user_payload = transcript
    if existing_summary:
        user_payload = f"Earlier summary:\n{existing_summary}\n\nNew turns to fold in:\n{transcript}"
    response = await provider.chat(
        ChatRequest(
            messages=[
                ChatMessage(role="system", content=config.summary_prompt),
                ChatMessage(role="user", content=user_payload),
            ],
            temperature=0.0,
        )
    )
    return (response.content or "(no summary)").strip()
