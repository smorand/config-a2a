"""Wire-level tests for the outbound A2A client (Handoff/Orchestrate pattern support).

Unlike tests/unit/test_patterns_handoff_orchestrate.py (which monkeypatches
``send_text`` entirely), these tests mock the HTTP/SSE transport with ``respx``
so the actual header-building and SSE-parsing logic in
``config_a2a.a2a.client`` is exercised end to end.
"""

from __future__ import annotations

import httpx
import respx

from config_a2a.a2a.client import fetch_agent_card, send_text


def _sse_body(*events: dict) -> str:
    import json

    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


@respx.mock
async def test_send_text_sets_a2a_version_header() -> None:
    route = respx.post("http://agent.example/message:stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse_body(
                {"task": {"id": "t1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
                {
                    "statusUpdate": {
                        "taskId": "t1",
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "message": {
                                "messageId": "m1",
                                "role": "ROLE_AGENT",
                                "parts": [{"text": "hi"}],
                            },
                        },
                        "final": True,
                    }
                },
            ),
        )
    )
    await send_text("http://agent.example", "hello")
    assert route.calls.last.request.headers["A2A-Version"] == "1.0"


@respx.mock
async def test_send_text_reads_text_from_artifact_update() -> None:
    """A spec-standard peer delivers its answer as an artifact, not status.message."""
    respx.post("http://agent.example/message:stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse_body(
                {"task": {"id": "t1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
                {"statusUpdate": {"taskId": "t1", "status": {"state": "TASK_STATE_WORKING"}, "final": False}},
                {
                    "artifactUpdate": {
                        "taskId": "t1",
                        "artifact": {"artifactId": "a1", "parts": [{"text": "artifact answer"}]},
                    }
                },
                {"statusUpdate": {"taskId": "t1", "status": {"state": "TASK_STATE_COMPLETED"}, "final": True}},
            ),
        )
    )
    result = await send_text("http://agent.example", "hello")
    assert result.state == "TASK_STATE_COMPLETED"
    assert result.text == "artifact answer"
    assert result.task_id == "t1"


@respx.mock
async def test_send_text_reads_text_from_status_message_backward_compat() -> None:
    """config-a2a's own server (no artifacts) still works: text stays in status.message."""
    respx.post("http://agent.example/message:stream").mock(
        return_value=httpx.Response(
            200,
            content=_sse_body(
                {"task": {"id": "t1", "status": {"state": "TASK_STATE_SUBMITTED"}}},
                {
                    "statusUpdate": {
                        "taskId": "t1",
                        "status": {
                            "state": "TASK_STATE_COMPLETED",
                            "message": {
                                "messageId": "m1",
                                "role": "ROLE_AGENT",
                                "parts": [{"text": "status answer"}],
                            },
                        },
                        "final": True,
                    }
                },
            ),
        )
    )
    result = await send_text("http://agent.example", "hello")
    assert result.state == "TASK_STATE_COMPLETED"
    assert result.text == "status answer"


@respx.mock
async def test_fetch_agent_card_falls_back_to_second_path() -> None:
    respx.get("http://agent.example/.well-known/a2a/agent-card").mock(return_value=httpx.Response(404))
    respx.get("http://agent.example/.well-known/agent-card.json").mock(
        return_value=httpx.Response(200, json={"name": "remote-agent"})
    )
    card = await fetch_agent_card("http://agent.example")
    assert card == {"name": "remote-agent"}
