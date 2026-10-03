"""Integration tests for investigation fingerprinting (Phase 8).

Run the three detectors end-to-end against the full deterministic synthetic
dataset loaded into an in-memory database.  Ground truth is used ONLY as an
evaluation oracle; production analytic code never reads it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.investigation_fingerprinting import (
    FingerprintConfig,
    FingerprintResult,
    run_investigation_fingerprinting,
)
from app.analytics.anomaly import run_anomaly_detection
from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.metric_risk_divergence import run_metric_risk_divergence
from app.analytics.negative_space import run_negative_space_detection
from app.analytics.peer_benchmark import run_peer_benchmark
from app.datagen.generator import Dataset
from app.datagen.scenarios import ScenarioType
from app.db.base import Base
from app.models import (
    Alert, Asset, Escalation, Investigation, InvestigationAction,
    PerformanceMetric, Remediation, SocEntity, TelemetryRecord,
)

_LOAD_ORDER = [
    ("entities", SocEntity), ("assets", Asset), ("alerts", Alert),
    ("investigations", Investigation),
    ("investigation_actions", InvestigationAction),
    ("escalations", Escalation), ("remediations", Remediation),
    ("telemetry", TelemetryRecord), ("performance_metrics", PerformanceMetric),
]


@pytest.fixture()
def loaded_session(generated_dataset: Dataset):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(c, _):  # pragma: no cover
        c.execute("PRAGMA foreign_keys=ON")

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


def _repetitive_entity(dataset: Dataset) -> str:
    rows = [g for g in dataset.ground_truth
            if g["scenario_type"] == ScenarioType.REPETITIVE_INVESTIGATION_WORKFLOW]
    assert rows, "fixture sanity: repetitive workflow scenario must be planted"
    return rows[0]["entity_id"]


def _repetitive_inv_ids(dataset: Dataset) -> set[str]:
    return {
        g["investigation_id"]
        for g in dataset.ground_truth
        if g["scenario_type"] == ScenarioType.REPETITIVE_INVESTIGATION_WORKFLOW
    }


# ---------------------------------------------------------------------------
# Detection coverage
# ---------------------------------------------------------------------------

def test_repetitive_workflow_entity_is_detected(loaded_session, generated_dataset):
    result = run_investigation_fingerprinting(loaded_session)
    flagged = {f.entity_id for f in result.repetitive_findings}
    assert _repetitive_entity(generated_dataset) in flagged


def test_repetitive_finding_covers_planted_investigations(
    loaded_session, generated_dataset
):
    result = run_investigation_fingerprinting(loaded_session)
    eid = _repetitive_entity(generated_dataset)
    planted_ids = _repetitive_inv_ids(generated_dataset)
    rep_finding = next(
        f for f in result.repetitive_findings if f.entity_id == eid
    )
    # All planted investigation IDs must be in the finding's matching set.
    assert planted_ids <= set(rep_finding.matching_investigation_ids)


def test_repetitive_finding_dominant_fingerprint(loaded_session, generated_dataset):
    """The dominant fingerprint must be the planted pattern."""
    result = run_investigation_fingerprinting(loaded_session)
    eid = _repetitive_entity(generated_dataset)
    rep = next(f for f in result.repetitive_findings if f.entity_id == eid)
    expected = ("OPEN", "EVENT_SEARCH", "EVENT_SEARCH", "EVENT_SEARCH", "CLOSE")
    assert rep.dominant_fingerprint == expected


def test_repetitive_finding_matching_count(loaded_session, generated_dataset):
    result = run_investigation_fingerprinting(loaded_session)
    eid = _repetitive_entity(generated_dataset)
    rep = next(f for f in result.repetitive_findings if f.entity_id == eid)
    # The planted scenario has exactly 4 identical investigations.
    assert rep.matching_investigation_count >= 4


def test_run_covers_all_entities(loaded_session):
    result = run_investigation_fingerprinting(loaded_session)
    assert len(result.entity_ids) == 6


def test_total_investigations_assessed_positive(loaded_session):
    result = run_investigation_fingerprinting(loaded_session)
    assert result.total_investigations_assessed > 0


# ---------------------------------------------------------------------------
# Controls — normal baseline must not be flooded
# ---------------------------------------------------------------------------

def test_repetitive_findings_not_a_flood(loaded_session):
    result = run_investigation_fingerprinting(loaded_session)
    # At most a handful of repetitive findings across 6 entities
    assert len(result.repetitive_findings) <= 6


def test_summary_uses_neutral_language(loaded_session, generated_dataset):
    result = run_investigation_fingerprinting(loaded_session)
    eid = _repetitive_entity(generated_dataset)
    rep = next(f for f in result.repetitive_findings if f.entity_id == eid)
    assert "Potential Template-Driven Investigation Pattern" in rep.summary
    forbidden = ["negligence", "fraud", "manipulat", "misconduct", "lazy"]
    for w in forbidden:
        assert w.lower() not in rep.summary.lower()


# ---------------------------------------------------------------------------
# Sanity invariants
# ---------------------------------------------------------------------------

def test_run_is_deterministic(loaded_session):
    r1 = run_investigation_fingerprinting(loaded_session)
    r2 = run_investigation_fingerprinting(loaded_session)
    assert r1 == r2
    assert [f.finding_key for f in r1.all_findings] == \
           [f.finding_key for f in r2.all_findings]


def test_run_is_read_only(loaded_session):
    models = (SocEntity, Investigation, InvestigationAction, Alert)
    before = {m.__name__: loaded_session.scalar(
        select(func.count()).select_from(m)) for m in models}
    run_investigation_fingerprinting(loaded_session)
    run_investigation_fingerprinting(loaded_session)
    after = {m.__name__: loaded_session.scalar(
        select(func.count()).select_from(m)) for m in models}
    assert before == after


def test_finding_keys_are_sorted(loaded_session):
    result = run_investigation_fingerprinting(loaded_session)
    for collection in (
        result.repetitive_findings,
        result.deviation_findings,
        result.missing_action_findings,
    ):
        keys = [f.finding_key for f in collection]
        assert keys == sorted(keys)


def test_coexists_with_all_prior_analytics(loaded_session):
    """Phase 8 runs after all prior analytics without mutual interference."""
    eg = run_execution_gap_detection(loaded_session)
    ns = run_negative_space_detection(loaded_session)
    an = run_anomaly_detection(loaded_session)
    pb = run_peer_benchmark(loaded_session)
    mrd = run_metric_risk_divergence(loaded_session)

    run_investigation_fingerprinting(loaded_session)

    assert run_execution_gap_detection(loaded_session) == eg
    assert run_negative_space_detection(loaded_session) == ns
    assert run_anomaly_detection(loaded_session) == an
    assert run_peer_benchmark(loaded_session) == pb
    assert run_metric_risk_divergence(loaded_session) == mrd


def test_sensitive_config_detects_same_entity(loaded_session, generated_dataset):
    cfg = FingerprintConfig(
        repetition_threshold=2,
        repetition_rate_threshold=0.30,
        min_comparable_investigations=2,
    )
    result = run_investigation_fingerprinting(loaded_session, config=cfg)
    flagged = {f.entity_id for f in result.repetitive_findings}
    assert _repetitive_entity(generated_dataset) in flagged


def test_findings_reference_real_entity_ids(loaded_session):
    entity_ids = set(loaded_session.scalars(select(SocEntity.entity_id)).all())
    result = run_investigation_fingerprinting(loaded_session)
    for f in result.all_findings:
        assert f.entity_id in entity_ids
