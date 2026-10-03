"""Integration tests for metric-risk divergence detection (Phase 7).

These tests run the detector end-to-end against the full deterministic synthetic
dataset loaded into an in-memory database.  Ground truth is used ONLY as an
evaluation oracle to confirm that the planted scenario is detected; production
analytic code never reads ground truth.

Test categories
---------------
1. Detection coverage — planted divergence entity is flagged.
2. Controls — normal-baseline entities are not flooded with findings.
3. Coexistence — Phase 4–6 analytics remain unaffected when Phase 7 runs.
4. Sanity invariants — determinism, read-only behaviour, record traceability.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.metric_risk_divergence import (
    MetricRiskDivergenceConfig,
    run_metric_risk_divergence,
)
from app.analytics.anomaly import run_anomaly_detection
from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.negative_space import run_negative_space_detection
from app.analytics.peer_benchmark import run_peer_benchmark
from app.datagen.generator import Dataset
from app.datagen.scenarios import ScenarioType
from app.db.base import Base
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

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

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
    """In-memory database loaded with the full deterministic synthetic dataset."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover
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


def _divergence_entity(dataset: Dataset) -> str:
    """Return the entity_id of the planted metric-risk-divergence scenario.

    Ground truth is used ONLY here, as an evaluation oracle.  Production code
    never reads ground truth.
    """
    gt = [
        g
        for g in dataset.ground_truth
        if g["scenario_type"] == ScenarioType.METRIC_RISK_DIVERGENCE
    ]
    assert len(gt) == 1, "fixture sanity: exactly one divergence entity planted"
    return gt[0]["entity_id"]


# ---------------------------------------------------------------------------
# 1. Detection coverage
# ---------------------------------------------------------------------------

def test_planted_divergence_entity_is_detected(loaded_session, generated_dataset) -> None:
    """The entity whose metrics were planted with divergence must be flagged."""
    result = run_metric_risk_divergence(loaded_session)
    flagged = {f.entity_id for f in result.findings}
    assert _divergence_entity(generated_dataset) in flagged


def test_finding_for_divergence_entity_has_positive_headline(
    loaded_session, generated_dataset
) -> None:
    result = run_metric_risk_divergence(loaded_session)
    div_eid = _divergence_entity(generated_dataset)
    f = next(f for f in result.findings if f.entity_id == div_eid)
    assert f.headline_trend_score > 0, (
        f"Expected positive headline trend score, got {f.headline_trend_score}"
    )


def test_finding_for_divergence_entity_has_negative_quality(
    loaded_session, generated_dataset
) -> None:
    result = run_metric_risk_divergence(loaded_session)
    div_eid = _divergence_entity(generated_dataset)
    f = next(f for f in result.findings if f.entity_id == div_eid)
    assert f.quality_trend_score < 0, (
        f"Expected negative quality trend score, got {f.quality_trend_score}"
    )


def test_divergence_finding_covers_all_six_periods(
    loaded_session, generated_dataset
) -> None:
    result = run_metric_risk_divergence(loaded_session)
    div_eid = _divergence_entity(generated_dataset)
    f = next(f for f in result.findings if f.entity_id == div_eid)
    assert f.supporting_period_count == 6


def test_divergence_finding_has_high_confidence(
    loaded_session, generated_dataset
) -> None:
    """The planted scenario spans 6 periods with strong trends → HIGH confidence."""
    from app.analytics.metric_risk_divergence.findings import ConfidenceLevel
    result = run_metric_risk_divergence(loaded_session)
    div_eid = _divergence_entity(generated_dataset)
    f = next(f for f in result.findings if f.entity_id == div_eid)
    assert f.confidence == ConfidenceLevel.HIGH


# ---------------------------------------------------------------------------
# 2. Controls — no false positive flood
# ---------------------------------------------------------------------------

def test_normal_baseline_entities_not_all_flagged(
    loaded_session, generated_dataset
) -> None:
    """With default thresholds only the planted divergence entity is flagged."""
    result = run_metric_risk_divergence(loaded_session)
    # With the calibrated default thresholds only ENT-04 (the planted scenario)
    # should fire; the other five entities must not produce findings.
    assert result.finding_count == 1
    assert result.findings[0].entity_id == _divergence_entity(generated_dataset)


def test_run_covers_all_six_entities(loaded_session) -> None:
    result = run_metric_risk_divergence(loaded_session)
    assert len(result.entity_ids) == 6


def test_skipped_entities_are_reported_not_silently_dropped(loaded_session) -> None:
    """Any entity with insufficient periods is explicitly in skipped_entity_ids."""
    # With 6 periods per entity and default min_observation_periods=4, no entity
    # should be skipped in the full synthetic dataset.
    result = run_metric_risk_divergence(loaded_session)
    assert result.skipped_entity_ids == ()


def test_findings_reference_real_entity_ids(loaded_session) -> None:
    entity_ids = set(
        loaded_session.scalars(select(SocEntity.entity_id)).all()
    )
    result = run_metric_risk_divergence(loaded_session)
    for f in result.findings:
        assert f.entity_id in entity_ids


def test_finding_snapshots_reference_real_metric_periods(
    loaded_session, generated_dataset
) -> None:
    """Snapshot period labels must correspond to actual PerformanceMetric rows."""
    result = run_metric_risk_divergence(loaded_session)
    div_eid = _divergence_entity(generated_dataset)
    f = next(f for f in result.findings if f.entity_id == div_eid)
    # Collect real period labels for this entity from the DB.
    real_labels = {
        pm.period_start.strftime("%Y-%m") if pm.period_start.tzinfo else
        pm.period_start.replace(tzinfo=__import__("datetime").timezone.utc).strftime("%Y-%m")
        for pm in loaded_session.scalars(
            select(PerformanceMetric).where(
                PerformanceMetric.entity_id == div_eid
            )
        )
    }
    for snap in f.headline_snapshots:
        for label, _value in snap.period_values:
            assert label in real_labels, (
                f"Snapshot label {label!r} not in real period labels {real_labels}"
            )


# ---------------------------------------------------------------------------
# 3. Coexistence — Phase 4–6 analytics are unaffected
# ---------------------------------------------------------------------------

def test_phase7_coexists_with_phase4_to_6(loaded_session) -> None:
    """All five analytics can run against the same session without mutual interference."""
    # Run each analytic before Phase 7.
    eg_before = run_execution_gap_detection(loaded_session)
    ns_before = run_negative_space_detection(loaded_session)
    an_before = run_anomaly_detection(loaded_session)
    pb_before = run_peer_benchmark(loaded_session)

    # Run Phase 7.
    run_metric_risk_divergence(loaded_session)

    # Re-run earlier analytics; results must be identical.
    eg_after = run_execution_gap_detection(loaded_session)
    ns_after = run_negative_space_detection(loaded_session)
    an_after = run_anomaly_detection(loaded_session)
    pb_after = run_peer_benchmark(loaded_session)

    assert eg_before == eg_after
    assert ns_before == ns_after
    assert an_before == an_after
    assert pb_before == pb_after


# ---------------------------------------------------------------------------
# 4. Sanity invariants
# ---------------------------------------------------------------------------

def test_run_is_deterministic(loaded_session) -> None:
    r1 = run_metric_risk_divergence(loaded_session)
    r2 = run_metric_risk_divergence(loaded_session)
    assert r1 == r2
    assert [f.finding_key for f in r1.findings] == [f.finding_key for f in r2.findings]


def test_run_is_read_only(loaded_session) -> None:
    models = (SocEntity, Asset, Alert, Investigation, PerformanceMetric)
    before = {
        m.__name__: loaded_session.scalar(select(func.count()).select_from(m))
        for m in models
    }
    run_metric_risk_divergence(loaded_session)
    run_metric_risk_divergence(loaded_session)
    after = {
        m.__name__: loaded_session.scalar(select(func.count()).select_from(m))
        for m in models
    }
    assert before == after


def test_finding_keys_are_sorted(loaded_session) -> None:
    result = run_metric_risk_divergence(loaded_session)
    keys = [f.finding_key for f in result.findings]
    assert keys == sorted(keys)


def test_summary_neutral_language_on_real_dataset(loaded_session, generated_dataset) -> None:
    result = run_metric_risk_divergence(loaded_session)
    div_eid = _divergence_entity(generated_dataset)
    f = next((f for f in result.findings if f.entity_id == div_eid), None)
    assert f is not None
    forbidden = ["manipulat", "fraud", "gaming", "falsif"]
    for word in forbidden:
        assert word.lower() not in f.summary.lower()
    assert "Potential Metric-Risk Divergence" in f.summary


def test_config_override_more_sensitive_detects_same_entity(
    loaded_session, generated_dataset
) -> None:
    """A lower threshold config still detects the planted entity."""
    cfg = MetricRiskDivergenceConfig(
        headline_min_improvement=0.05,
        quality_min_deterioration=0.05,
        min_observation_periods=4,
    )
    result = run_metric_risk_divergence(loaded_session, config=cfg)
    flagged = {f.entity_id for f in result.findings}
    assert _divergence_entity(generated_dataset) in flagged
