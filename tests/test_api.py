"""Tests for the FastAPI application."""

from __future__ import annotations

from fastapi.testclient import TestClient

from config_a2a.api import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_agent_endpoint_returns_example_config() -> None:
    client = TestClient(create_app())
    response = client.get("/agent")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "example-agent"
