"""End-to-end smoke tests against real LLM providers (OpenRouter)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config_a2a.api import create_app
from config_a2a.config.loader import load_server_config

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "config_examples"

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _require_openrouter() -> None:
    if os.environ.get("RUN_E2E") != "1":
        pytest.skip("set RUN_E2E=1 to run e2e tests")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")


def _client_for(example: str) -> tuple[TestClient, str]:
    server = load_server_config(EXAMPLES_DIR / example / "agents.yaml")
    client = TestClient(create_app(server))
    prefix = f"/agents/{server.agents[0].slug}"
    return client, prefix


def test_01_simple_real_llm() -> None:
    client, prefix = _client_for("01-simple")
    payload = {
        "message": {
            "messageId": "e2e-1",
            "role": "ROLE_USER",
            "parts": [{"text": "Reply with the single word 'pong' and nothing else."}],
        }
    }
    with client.stream("POST", f"{prefix}/message:stream", json=payload) as response:
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
    assert text
