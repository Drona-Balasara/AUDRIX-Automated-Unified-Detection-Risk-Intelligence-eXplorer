"""Tests for the health endpoint contract."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_health_response_structure(client: TestClient) -> None:
    """The payload has the expected stable fields and values."""
    body = client.get("/api/v1/health").json()

    assert body["service"] == "sat-sa-api"
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert set(body) == {"service", "status", "version", "environment", "database"}


def test_health_does_not_leak_internals(client: TestClient) -> None:
    """The response must not expose paths, URLs, or other internal details."""
    raw = client.get("/api/v1/health").text.lower()

    for leaked in ("sqlite", "database_url", "traceback", "/users/", "c:\\", "secret", "password"):
        assert leaked not in raw
