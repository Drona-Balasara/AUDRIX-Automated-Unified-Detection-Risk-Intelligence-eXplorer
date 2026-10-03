"""Unit tests for anomaly detection.

These tests build small, hand-constructed in-memory databases to exercise
feature construction (undefined denominators, no identifier leakage), the
minimum-sample safeguard, determinism, read-only behaviour, and configuration
validation in isolation. Synthetic-dataset integration coverage lives in
``test_anomaly_peer_integration.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.anomaly import (
    ALL_FEATURES,
    ELIGIBLE_FEATURES,
    AnomalyConfig,
    AnomalyStatus,
    run_anomaly_detection,
)
from app.analytics.anomaly.context import build_observations, load_context
from app.db.base import Base
from app.models import (
    Alert,
    Asset,
    Investigation,
    InvestigationAction,
    PerformanceMetric,
    SocEntity,
)
from app.models.enums import (
    ActionType,
    AlertCategory,
    AlertSeverity,
    AlertStatus,
    AssetCategory,
    Criticality,
    DetectionSource,
    EntityScale,
    InvestigationStatus,
    Sector,
)

BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial pragma
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _month(index: int) -> tuple[datetime, datetime]:
    start = BASE + timedelta(days=31 * index)
    return start, start + timedelta(days=30)


def add_entity(s, entity_id, asset_count=10):
    s.add(
        SocEntity(
            entity_id=entity_id,
            name=f"Entity {entity_id}",
            sector=Sector.TECHNOLOGY.value,
            peer_group="GRP",
            scale=EntityScale.MEDIUM.value,
            asset_count_estimate=asset_count,
            analyst_headcount=5,
            created_at=BASE,
            data_period_start=BASE,
            data_period_end=BASE + timedelta(days=400),
        )
    )
    s.flush()
    s.add(
        Asset(
            asset_id=f"AST-{entity_id}",
            entity_id=entity_id,
            name=f"Asset {entity_id}",
            category=AssetCategory.SERVER.value,
            criticality=Criticality.MEDIUM.value,
            monitoring_expected=True,
            created_at=BASE,
        )
    )
    s.flush()


def seed_period(s, entity_id, period_index, *, n_alerts, n_invs, closed=True):
    """Create a reporting period with populated operational records."""
    start, end = _month(period_index)
    s.add(
        PerformanceMetric(
            metric_id=f"PMET-{entity_id}-{period_index}",
            entity_id=entity_id,
            period_start=start,
            period_end=end,
            mttr_hours=2.0,
            closure_rate=0.9,
            sla_compliance=0.9,
            escalation_rate=0.2,
            investigation_completeness=0.8,
            recurrence_rate=0.1,
            remediation_rate=0.8,
            evidence_completeness=0.8,
        )
    )
    ts = start + timedelta(hours=1)
    for a in range(n_alerts):
        s.add(
            Alert(
                alert_id=f"ALR-{entity_id}-{period_index}-{a}",
                entity_id=entity_id,
                asset_id=f"AST-{entity_id}",
                severity=(AlertSeverity.HIGH if a % 2 == 0 else AlertSeverity.LOW).value,
                category=AlertCategory.MALWARE.value,
                detection_source=DetectionSource.ENDPOINT.value,
                status=AlertStatus.CLOSED.value,
                created_at=ts,
                is_true_positive=(a % 2 == 0),
            )
        )
    for i in range(n_invs):
        inv_id = f"INV-{entity_id}-{period_index}-{i}"
        s.add(
            Investigation(
                investigation_id=inv_id,
                alert_id=f"ALR-{entity_id}-{period_index}-{i}",
                entity_id=entity_id,
                analyst_id="ANALYST-1",
                status=(InvestigationStatus.CLOSED if closed else InvestigationStatus.OPEN).value,
                started_at=ts,
                ended_at=ts + timedelta(hours=3) if closed else None,
                duration_seconds=10800 if closed else None,
                evidence_count=4,
            )
        )
        s.add(
            InvestigationAction(
                action_id=f"ACT-{entity_id}-{period_index}-{i}",
                investigation_id=inv_id,
                sequence_number=1,
                action_type=ActionType.EVENT_SEARCH.value,
                occurred_at=ts,
            )
        )
    s.flush()


def seed_grid(s, n_entities, n_periods):
    for e in range(n_entities):
        eid = f"ENT-{e:02d}"
        add_entity(s, eid, asset_count=10 + e)
        for p in range(n_periods):
            seed_period(s, eid, p, n_alerts=3 + (e % 3), n_invs=2 + (p % 2))


def test_feature_schema_has_no_identifier_leakage():
    forbidden = {"entity_id", "asset_id", "alert_id", "investigation_id", "period"}
    assert forbidden.isdisjoint(set(ALL_FEATURES))
    # Every model-eligible feature is a numeric aggregate, not an identifier.
    assert len(ELIGIBLE_FEATURES) == 9


def test_undefined_denominator_is_none_not_zero(session):
    # A period with investigations but zero alerts leaves alert-denominated
    # features undefined; they must be None (not a silent zero) and the
    # observation must be dropped from the model population.
    add_entity(session, "ENT-00")
    start, end = _month(0)
    session.add(
        PerformanceMetric(
            metric_id="PMET-ENT-00-0",
            entity_id="ENT-00",
            period_start=start,
            period_end=end,
            mttr_hours=2.0,
            closure_rate=0.9,
            sla_compliance=0.9,
            escalation_rate=0.2,
            investigation_completeness=0.8,
            recurrence_rate=0.1,
            remediation_rate=0.8,
            evidence_completeness=0.8,
        )
    )
    session.flush()
    ctx = load_context(session)
    obs = build_observations(ctx)
    assert len(obs) == 1
    o = obs[0]
    assert o.values["crit_high_rate"] is None  # 0 alerts -> undefined
    assert o.values["inv_coverage"] is None
    assert o.values["alert_count"] == 0.0  # count is defined (not None)
    assert o.is_model_complete() is False


def test_insufficient_sample_returns_explicit_status(session):
    seed_grid(session, n_entities=2, n_periods=3)  # 6 observations < default 20
    result = run_anomaly_detection(session)
    assert result.status == AnomalyStatus.INSUFFICIENT_SAMPLE
    assert result.finding_count == 0
    assert result.model_metadata is None
    assert result.scored_observation_count == 6


def test_ok_run_is_deterministic_and_read_only(session):
    seed_grid(session, n_entities=8, n_periods=3)  # 24 complete observations
    cfg = AnomalyConfig(min_training_samples=10)
    before = session.scalar(select(func.count()).select_from(Alert))
    first = run_anomaly_detection(session, cfg)
    second = run_anomaly_detection(session, cfg)
    after = session.scalar(select(func.count()).select_from(Alert))
    assert first.status == AnomalyStatus.OK
    assert first == second  # identical scores, decisions, findings
    assert before == after  # no mutation
    assert first.model_metadata.feature_names == ELIGIBLE_FEATURES
    assert first.scored_observation_count == 24


def test_findings_are_subset_and_well_formed(session):
    seed_grid(session, n_entities=8, n_periods=3)
    result = run_anomaly_detection(session, AnomalyConfig(min_training_samples=10))
    assert result.finding_count <= result.scored_observation_count
    for f in result.findings:
        assert f.finding_key.startswith("AN-001:")
        assert 0.0 <= f.normalized_anomaly_score <= 1.0
        assert set(f.feature_snapshot) == set(ELIGIBLE_FEATURES)
        assert len(f.notable_features) == 3


def test_empty_database_returns_safe_result(session):
    result = run_anomaly_detection(session)
    assert result.status == AnomalyStatus.INSUFFICIENT_SAMPLE
    assert result.observation_count == 0
    assert result.findings == ()


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        AnomalyConfig(n_estimators=0)
    with pytest.raises(ValueError):
        AnomalyConfig(contamination=0.0)
    with pytest.raises(ValueError):
        AnomalyConfig(contamination=0.9)
    with pytest.raises(ValueError):
        AnomalyConfig(min_training_samples=0)
