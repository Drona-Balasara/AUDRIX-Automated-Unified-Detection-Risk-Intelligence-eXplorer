"""The generated dataset loads into the SQLAlchemy ORM with FK integrity."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.datagen.generator import Dataset
from app.models import (
    Alert,
    Asset,
    Escalation,
    Investigation,
    InvestigationAction,
    PerformanceMetric,
    Remediation,
    SocEntity,
    TelemetryRecord,
)

# Insert order respects foreign-key dependencies.
_LOAD_ORDER = [
    ("entities", SocEntity),
    ("assets", Asset),
    ("alerts", Alert),
    ("investigations", Investigation),
    ("investigation_actions", InvestigationAction),
    ("escalations", Escalation),
    ("remediations", Remediation),
    ("telemetry", TelemetryRecord),
    ("performance_metrics", PerformanceMetric),
]


@pytest.fixture()
def loaded_session(generated_dataset: Dataset):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # Enforce foreign keys on SQLite so referential integrity is actually checked.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial pragma
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = Session(engine)
    for table, model in _LOAD_ORDER:
        session.add_all(model(**row) for row in generated_dataset.tables[table])
        session.flush()
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_full_dataset_loads(loaded_session: Session, generated_dataset: Dataset) -> None:
    assert loaded_session.scalar(select(SocEntity).limit(1)) is not None
    assert len(loaded_session.scalars(select(Alert)).all()) == len(
        generated_dataset.tables["alerts"]
    )


def test_relationships_navigable(loaded_session: Session) -> None:
    alert = loaded_session.scalar(
        select(Alert).where(Alert.status == "CLOSED").limit(1)
    )
    assert alert is not None
    assert alert.entity is not None
    assert alert.asset is not None
    if alert.investigation is not None:
        actions = alert.investigation.actions
        seqs = [a.sequence_number for a in actions]
        assert seqs == sorted(seqs)  # relationship order_by honored
