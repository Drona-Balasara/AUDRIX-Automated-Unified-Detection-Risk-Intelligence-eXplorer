"""Tests for database initialization and the system-metadata foundation."""

from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import SystemMetadata
from app.services.system_service import SCHEMA_VERSION, check_database, ensure_system_metadata


def _fresh_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


def test_schema_creation_and_seed_succeeds() -> None:
    """A fresh database initializes with exactly one metadata row."""
    engine = _fresh_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    with SessionLocal() as session:
        ensure_system_metadata(session)
        session.commit()

        count = session.scalar(select(func.count()).select_from(SystemMetadata))
        row = session.scalars(select(SystemMetadata)).one()

    assert count == 1
    assert row.schema_version == SCHEMA_VERSION
    assert row.initialized_at is not None


def test_ensure_system_metadata_is_idempotent() -> None:
    """Repeated initialization does not create duplicate metadata rows."""
    engine = _fresh_engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    with SessionLocal() as session:
        ensure_system_metadata(session)
        ensure_system_metadata(session)
        session.commit()
        count = session.scalar(select(func.count()).select_from(SystemMetadata))

    assert count == 1


def test_check_database_true_when_connected(db_session: Session) -> None:
    assert check_database(db_session) is True
