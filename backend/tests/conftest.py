"""Shared pytest fixtures.

Tests run against an isolated in-memory SQLite database so they never touch the
development database and never depend on external services. A ``StaticPool`` is
used so the single in-memory connection is shared across sessions within a test.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import SystemMetadata  # noqa: F401  (register models on Base.metadata)
from app.services.system_service import ensure_system_metadata


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Provide a fresh, isolated in-memory database for a single test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=Session
    )

    session = TestingSessionLocal()
    ensure_system_metadata(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient whose database dependency is bound to the in-memory session."""
    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    # The client is intentionally NOT used as a context manager: that would run
    # the startup lifespan (and its init_db) against the real development
    # database. Startup initialization is covered directly in test_db.py.
    # ``raise_server_exceptions=False`` lets the app's exception handler run as
    # it would in production so error responses can be asserted.
    test_client = TestClient(app, raise_server_exceptions=False)
    yield test_client
    app.dependency_overrides.clear()
