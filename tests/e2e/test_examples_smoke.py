"""End-to-end smoke tests against real LLM providers (OpenRouter).

Gated by `RUN_E2E=1` and presence of `OPENROUTER_API_KEY` so unit CI stays cheap.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app
from config_a2a.config.loader import load_agent_config
from config_a2a.runtime import AgentRuntime

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "config_examples"

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _require_openrouter() -> None:
    if os.environ.get("RUN_E2E") != "1":
        pytest.skip("set RUN_E2E=1 to run e2e tests")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")


def _client_for(example: str) -> TestClient:
    config_path = EXAMPLES_DIR / example / "agent.yaml"
    runtime = AgentRuntime(load_agent_config(config_path))
    return TestClient(create_app(runtime))


def test_01_simple_real_llm() -> None:
    client = _client_for("01-simple")
    payload = {
        "message": {
            "messageId": "e2e-1",
            "role": "ROLE_USER",
            "parts": [{"text": "Reply with the single word 'pong' and nothing else."}],
        }
    }
    with client.stream("POST", "/message:stream", json=payload) as response:
        assert response.status_code == 200
        body = "".join(response.iter_text())
    blocks = [b for b in body.split("\n\n") if b.strip()]
    final = next(
        json.loads(line[len("data: ") :])
        for block in reversed(blocks)
        for line in block.splitlines()
        if line.startswith("data: ") and "statusUpdate" in line
    )
    state = final["statusUpdate"]["status"]["state"]
    assert state == "TASK_STATE_COMPLETED", f"unexpected state: {state}; body={body[-500:]}"
    text = final["statusUpdate"]["status"]["message"]["parts"][0]["text"].lower()
    assert text  # non-empty
