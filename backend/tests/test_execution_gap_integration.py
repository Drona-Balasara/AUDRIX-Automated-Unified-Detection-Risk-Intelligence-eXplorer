"""Integration tests for execution-gap detection against the synthetic dataset.

These tests load the full deterministic synthetic dataset (default seed) into an
in-memory database and run the detector end to end. The ``ground_truth`` manifest
is used ONLY here, as an evaluation oracle, to confirm that planted scenarios are
detected and labelled negative controls are not. Production detection code never
reads ground truth.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.execution_gap import run_execution_gap_detection
from app.datagen.generator import Dataset
from app.db.base import Base
from app.datagen.scenarios import ScenarioType
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


def _gt(dataset: Dataset, scenario_type: str) -> list[dict]:
    return [g for g in dataset.ground_truth if g["scenario_type"] == scenario_type]


def _flagged_alert_ids(result) -> set[str]:
    ids: set[str] = set()
    for f in result.findings:
        if f.alert_id:
            ids.add(f.alert_id)
        ids.update(f.related_alert_ids)
    return ids


# ----- coverage: planted scenarios are detected ----------------------------


def test_escalation_scenarios_detected(loaded_session, generated_dataset) -> None:
    result = run_execution_gap_detection(loaded_session)
    detected = {f.alert_id for f in result.findings if f.rule_id == "EG-001"}
    expected = {g["alert_id"] for g in _gt(generated_dataset, ScenarioType.CRITICAL_ALERT_WITHOUT_ESCALATION)}
    assert expected, "fixture sanity: scenario should exist in ground truth"
    assert expected <= detected


def test_investigation_scenarios_detected(loaded_session, generated_dataset) -> None:
    result = run_execution_gap_detection(loaded_session)
    detected = {f.alert_id for f in result.findings if f.rule_id == "EG-002"}
    expected = {g["alert_id"] for g in _gt(generated_dataset, ScenarioType.ACKNOWLEDGED_WITHOUT_INVESTIGATION)}
    assert expected
    assert expected <= detected


def test_remediation_scenarios_detected(loaded_session, generated_dataset) -> None:
    result = run_execution_gap_detection(loaded_session)
    detected = {(f.entity_id, f.recurrence_key) for f in result.findings if f.rule_id == "EG-003"}
    expected = {
        (g["entity_id"], g["recurrence_key"])
        for g in _gt(generated_dataset, ScenarioType.RECURRING_ALERTS_WITHOUT_REMEDIATION)
    }
    assert expected
    assert expected <= detected


# ----- negative controls and no-flood --------------------------------------


def test_normal_baseline_controls_not_flagged(loaded_session, generated_dataset) -> None:
    result = run_execution_gap_detection(loaded_session)
    flagged = _flagged_alert_ids(result)
    controls = {g["alert_id"] for g in _gt(generated_dataset, ScenarioType.NORMAL_BASELINE)}
    assert controls
    assert controls.isdisjoint(flagged)


def test_disproven_fast_investigation_not_escalation_gap(loaded_session, generated_dataset) -> None:
    # Fast-investigation alerts are disproven (is_true_positive=False); they must
    # not surface as EG-001 escalation gaps, which require a confirmed alert.
    result = run_execution_gap_detection(loaded_session)
    eg001 = {f.alert_id for f in result.findings if f.rule_id == "EG-001"}
    fast = {g["alert_id"] for g in _gt(generated_dataset, ScenarioType.SUSPICIOUSLY_FAST_INVESTIGATION)}
    assert fast.isdisjoint(eg001)


def test_findings_are_not_a_flood(loaded_session, generated_dataset) -> None:
    result = run_execution_gap_detection(loaded_session)
    alert_count = len(generated_dataset.tables["alerts"])
    # Conservative detection: findings are a small fraction of all alerts, not a
    # blanket flag of the baseline.
    assert result.finding_count < alert_count * 0.10


# ----- determinism and read-only -------------------------------------------


def test_detection_is_deterministic(loaded_session) -> None:
    first = run_execution_gap_detection(loaded_session)
    second = run_execution_gap_detection(loaded_session)
    assert first == second
    assert [f.finding_key for f in first.findings] == [f.finding_key for f in second.findings]


def test_finding_keys_unique(loaded_session) -> None:
    result = run_execution_gap_detection(loaded_session)
    keys = [f.finding_key for f in result.findings]
    assert len(keys) == len(set(keys))


def test_detection_read_only(loaded_session) -> None:
    models = (Alert, Investigation, Escalation, Remediation, SocEntity, Asset)
    before = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}
    run_execution_gap_detection(loaded_session)
    run_execution_gap_detection(loaded_session)
    after = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}
    assert before == after


def test_findings_reference_real_records(loaded_session) -> None:
    # Every finding's referenced ids resolve to actual rows (not ground-truth labels).
    result = run_execution_gap_detection(loaded_session)
    for f in result.findings:
        if f.alert_id:
            assert loaded_session.get(Alert, f.alert_id) is not None
        if f.investigation_id:
            assert loaded_session.get(Investigation, f.investigation_id) is not None
        for alert_id in f.related_alert_ids:
            assert loaded_session.get(Alert, alert_id) is not None


