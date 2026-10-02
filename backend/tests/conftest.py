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
from app.db.session import enable_sqlite_fk, get_db
from app.main import create_app
from app.models import SystemMetadata  # noqa: F401  (register models on Base.metadata)
from app.services.system_service import ensure_system_metadata

from app.datagen.config import DEFAULT_SEED, GenerationConfig
from app.datagen.generator import Dataset, DatasetGenerator
from app.datagen.writer import write_dataset


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Provide a fresh, isolated in-memory database for a single test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    enable_sqlite_fk(engine)
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


@pytest.fixture(scope="session")
def generated_dataset() -> Dataset:
    """A single deterministic dataset (default seed) reused across tests."""
    return DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()


@pytest.fixture(scope="session")
def written_dataset_dir(tmp_path_factory: pytest.TempPathFactory, generated_dataset: Dataset):
    """Write the generated dataset once to a temp dir and return the path."""
    out_dir = tmp_path_factory.mktemp("synthetic")
    write_dataset(generated_dataset, out_dir)
    return out_dir


# --- Ingestion test helpers -------------------------------------------------

import csv as _csv  # noqa: E402
import io as _io  # noqa: E402
import json as _json  # noqa: E402


def make_csv(rows: list[dict[str, object]], columns: list[str]) -> bytes:
    """Serialize rows to CSV bytes with an explicit column order."""
    buffer = _io.StringIO()
    writer = _csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in columns})
    return buffer.getvalue().encode("utf-8")


def make_json(rows: list[dict[str, object]]) -> bytes:
    """Serialize rows to a JSON array of objects."""
    return _json.dumps(rows).encode("utf-8")


@pytest.fixture()
def seed_entity(db_session: Session):
    """Insert one entity + asset so child-dataset imports have valid references."""
    from datetime import datetime, timezone

    from app.models import Asset, SocEntity

    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    db_session.add(
        SocEntity(
            entity_id="ENT-01",
            name="Seed Entity",
            sector="FINANCE",
            peer_group="FINANCE",
            scale="MEDIUM",
            asset_count_estimate=10,
            analyst_headcount=5,
            created_at=now,
            data_period_start=now,
            data_period_end=now,
        )
    )
    db_session.add(
        Asset(
            asset_id="AST-00001",
            entity_id="ENT-01",
            name="Seed Asset",
            category="SERVER",
            criticality="HIGH",
            monitoring_expected=True,
            expected_telemetry="AUTHENTICATION",
            created_at=now,
        )
    )
    db_session.commit()
    return db_session
