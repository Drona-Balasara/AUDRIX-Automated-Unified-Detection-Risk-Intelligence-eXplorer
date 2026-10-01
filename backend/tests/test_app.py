"""Tests proving the application and its import graph are healthy."""

from __future__ import annotations


def test_application_imports() -> None:
    """The FastAPI application object imports and constructs successfully."""
    from app.main import app, create_app

    assert app is not None
    # The factory produces a distinct, independently configured instance.
    assert create_app() is not app


def test_api_v1_router_mounted() -> None:
    """The versioned API router exposes the health route under /api/v1."""
    from app.main import app

    paths = {route.path for route in app.routes}  # type: ignore[attr-defined]
    assert "/api/v1/health" in paths
