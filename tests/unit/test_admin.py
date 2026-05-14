"""Admin REST surface: status, load (atomic), list, delete, async reload."""

from __future__ import annotations

import time

import pytest
import yaml
from fastapi.testclient import TestClient

from config_a2a.api import create_app
from config_a2a.config.models import (
    AdminConfig,
    AuthenticationConfig,
    ServerConfig,
)


def _empty_server(tmp_path) -> ServerConfig:  # noqa: ANN001
    return ServerConfig(
        name="empty-server",
        version="0.1.0",
        agents=[],
        admin=AdminConfig(enabled=True),
    )


def test_directory_on_empty_server(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    body = client.get("/.well-known/agent-card.json").json()
    assert body["name"] == "empty-server"
    assert body["agents"] == []


def test_admin_status_and_list_empty(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    status = client.get("/admin/status").json()
    assert status["name"] == "empty-server"
    assert status["agents_loaded"] == 0
    listing = client.get("/admin/agents").json()
    assert listing == {"agents": []}


def _agent_payload(slug: str, name: str | None = None) -> dict:
    return {
        "slug": slug,
        "name": name or slug,
        "model": {"provider": "openai-compatible", "model": "test"},
        "pattern": {"type": "simple"},
    }


def test_admin_load_inline_succeeds(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    response = client.post("/admin/agents", json=_agent_payload("alpha"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["slug"] == "alpha"
    listing = client.get("/admin/agents").json()
    assert [a["slug"] for a in listing["agents"]] == ["alpha"]


def test_admin_load_via_config_path(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    yaml_path = tmp_path / "agent.yaml"
    yaml_path.write_text(yaml.safe_dump(_agent_payload("beta")), encoding="utf-8")
    response = client.post("/admin/agents", json={"config_path": str(yaml_path)})
    assert response.status_code == 201
    assert client.get("/admin/agents/beta").status_code == 200


def test_admin_load_broken_does_not_leave_zombie(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    # Validation failure (missing required field) — 400, no zombie.
    response = client.post("/admin/agents", json={"slug": "broken"})
    assert response.status_code == 400, response.text
    listing = client.get("/admin/agents").json()
    assert listing == {"agents": []}


def test_admin_load_duplicate_slug_conflicts(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    assert client.post("/admin/agents", json=_agent_payload("gamma")).status_code == 201
    second = client.post("/admin/agents", json=_agent_payload("gamma"))
    assert second.status_code == 409


def test_admin_delete_unloads(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    client.post("/admin/agents", json=_agent_payload("delta"))
    # The agent's routes are mounted.
    assert client.get("/agents/delta/.well-known/agent-card.json").status_code == 200
    # Delete it.
    response = client.delete("/admin/agents/delta")
    assert response.status_code == 200
    # Subsequent requests for that prefix 404 (the routes are gone).
    assert client.get("/agents/delta/.well-known/agent-card.json").status_code == 404


def test_admin_reload_lifecycle(tmp_path) -> None:
    server = _empty_server(tmp_path)
    client = TestClient(create_app(server))
    client.post("/admin/agents", json=_agent_payload("epsilon"))
    response = client.post("/admin/agents/epsilon/reloads", json=_agent_payload("epsilon", name="epsilon-v2"))
    assert response.status_code == 202
    op = response.json()
    assert op["status"] in {"pending", "running"}
    op_id = op["id"]
    # Poll the operation status (TestClient runs callbacks synchronously between requests).
    final = None
    for _ in range(20):
        final = client.get(f"/admin/agents/epsilon/reloads/{op_id}").json()
        if final["status"] in {"completed", "failed"}:
            break
        time.sleep(0.05)
    assert final is not None and final["status"] == "completed", final


def test_admin_authentication_blocks_when_required(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "letmein")
    server = ServerConfig(
        name="protected",
        agents=[],
        admin=AdminConfig(
            enabled=True,
            authentication=AuthenticationConfig(type="bearer", value_env="ADMIN_TOKEN"),
        ),
    )
    client = TestClient(create_app(server))
    assert client.get("/admin/status").status_code == 401
    ok = client.get("/admin/status", headers={"Authorization": "Bearer letmein"})
    assert ok.status_code == 200
