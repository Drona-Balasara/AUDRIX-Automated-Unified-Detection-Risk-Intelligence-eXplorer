"""Phase 2 database initialization: additive, idempotent, non-destructive."""

from __future__ import annotations

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models import SystemMetadata
from app.services.system_service import ensure_system_metadata

_EXPECTED_TABLES = {
    "system_metadata",
    "soc_entity",
    "asset",
    "alert",
    "investigation",
    "investigation_action",
    "escalation",
    "remediation",
    "telemetry_record",
    "performance_metric",
}


def test_create_all_registers_every_table(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'phase2.db').as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    tables = set(inspect(engine).get_table_names())
    assert _EXPECTED_TABLES <= tables
    engine.dispose()


def test_init_is_idempotent_and_preserves_metadata(tmp_path) -> None:
    url = f"sqlite:///{(tmp_path / 'phase2.db').as_posix()}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

    # First initialization (as Phase 1 would have left it, now with new models).
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        ensure_system_metadata(session)
        session.commit()
        original = session.scalars(select(SystemMetadata)).one()
        original_ts = original.initialized_at

    # Re-running create_all + ensure must not drop tables or duplicate metadata.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        ensure_system_metadata(session)
        session.commit()
        count = session.scalar(select(func.count()).select_from(SystemMetadata))
        preserved = session.scalars(select(SystemMetadata)).one()

    assert count == 1
    assert preserved.initialized_at == original_ts
    assert _EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()
