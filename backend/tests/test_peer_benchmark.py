"""Unit tests for peer benchmarking.

Each test builds a small, hand-constructed in-memory database so the robust
median/MAD comparison, peer-group boundaries, self-exclusion, same-period
scoping, insufficient-peer handling, and the zero-MAD fallback can be exercised
in isolation. Synthetic-dataset integration coverage lives in
``test_anomaly_peer_integration.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.peer_benchmark import (
    PeerBenchmarkConfig,
    run_peer_benchmark,
)
from app.analytics.peer_benchmark.findings import DeviationBasis, DeviationDirection
from app.db.base import Base
from app.models import PerformanceMetric, SocEntity
from app.models.enums import EntityScale, Sector

P1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
P1_END = datetime(2024, 2, 1, tzinfo=timezone.utc)
P2 = datetime(2024, 2, 1, tzinfo=timezone.utc)
P2_END = datetime(2024, 3, 1, tzinfo=timezone.utc)


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


def add_entity(s, entity_id, scale=EntityScale.MEDIUM, peer_group="GRP", sector=Sector.TECHNOLOGY):
    s.add(
        SocEntity(
            entity_id=entity_id,
            name=f"Entity {entity_id}",
            sector=sector.value,
            peer_group=peer_group,
            scale=scale.value,
            asset_count_estimate=10,
            analyst_headcount=5,
            created_at=P1,
            data_period_start=P1,
            data_period_end=P2_END,
        )
    )
    s.flush()


_METRIC_DEFAULTS = dict(
    mttr_hours=2.0,
    closure_rate=0.9,
    sla_compliance=0.9,
    escalation_rate=0.2,
    investigation_completeness=0.8,
    recurrence_rate=0.1,
    remediation_rate=0.8,
    evidence_completeness=0.8,
)


def add_metric(s, entity_id, start=P1, end=P1_END, **overrides):
    values = {**_METRIC_DEFAULTS, **overrides}
    s.add(
        PerformanceMetric(
            metric_id=f"PMET-{entity_id}-{start:%Y%m}",
            entity_id=entity_id,
            period_start=start,
            period_end=end,
            **values,
        )
    )
    s.flush()


def test_insufficient_peers_emits_status_not_finding(session):
    add_entity(session, "ENT-01")
    add_metric(session, "ENT-01", mttr_hours=50.0)
    result = run_peer_benchmark(session)
    assert result.finding_count == 0
    assert len(result.statuses) == 1
    assert result.statuses[0].peer_population_count == 0


def test_self_value_excluded_from_baseline(session):
    # Three same-scale peers; the subject is an extreme outlier on mttr_hours.
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    add_metric(session, "ENT-01", mttr_hours=100.0)  # subject (extreme)
    add_metric(session, "ENT-02", mttr_hours=2.0)
    add_metric(session, "ENT-03", mttr_hours=4.0)
    result = run_peer_benchmark(session)
    subject = [f for f in result.findings if f.entity_id == "ENT-01" and f.metric_name == "mttr_hours"]
    assert len(subject) == 1
    # Baseline is the median of the OTHER two (2.0, 4.0) -> 3.0, not including 100.
    assert subject[0].peer_baseline == 3.0
    assert subject[0].peer_population_count == 2
    assert subject[0].direction == DeviationDirection.ABOVE


def test_incompatible_peer_groups_not_compared(session):
    # Different scale groups must never be pooled to inflate the sample.
    add_entity(session, "ENT-01", scale=EntityScale.LARGE)
    add_entity(session, "ENT-02", scale=EntityScale.SMALL)
    add_entity(session, "ENT-03", scale=EntityScale.MEDIUM)
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_metric(session, eid, mttr_hours=float(hash(eid) % 20))
    result = run_peer_benchmark(session)
    # Each entity is alone in its scale group -> all insufficient, no findings.
    assert result.finding_count == 0
    assert {s.peer_population_count for s in result.statuses} == {0}


def test_only_same_period_peers_compared(session):
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    # Period 1: three comparable peers.
    add_metric(session, "ENT-01", start=P1, end=P1_END, mttr_hours=100.0)
    add_metric(session, "ENT-02", start=P1, end=P1_END, mttr_hours=2.0)
    add_metric(session, "ENT-03", start=P1, end=P1_END, mttr_hours=4.0)
    # Period 2: subject alone -> insufficient, cannot borrow period-1 peers.
    add_metric(session, "ENT-01", start=P2, end=P2_END, mttr_hours=100.0)
    result = run_peer_benchmark(session)
    p2_findings = [f for f in result.findings if f.period_start == P2]
    assert p2_findings == []
    p2_status = [s for s in result.statuses if s.period_start == P2]
    assert len(p2_status) == 1 and p2_status[0].peer_population_count == 0


def test_zero_mad_identical_peers_uses_absolute_floor(session):
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    # Two identical peers -> zero spread. closure_rate floor is 0.15.
    add_metric(session, "ENT-02", closure_rate=0.90)
    add_metric(session, "ENT-03", closure_rate=0.90)
    # Subject 0.70 differs by 0.20 >= 0.15 -> flagged via absolute floor.
    add_metric(session, "ENT-01", closure_rate=0.70)
    result = run_peer_benchmark(session)
    cr = [f for f in result.findings if f.metric_name == "closure_rate" and f.entity_id == "ENT-01"]
    assert len(cr) == 1
    assert cr[0].deviation_basis == DeviationBasis.ABSOLUTE_ZERO_MAD
    assert cr[0].direction == DeviationDirection.BELOW


def test_zero_mad_below_floor_not_flagged(session):
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    add_metric(session, "ENT-02", closure_rate=0.90)
    add_metric(session, "ENT-03", closure_rate=0.90)
    add_metric(session, "ENT-01", closure_rate=0.80)  # diff 0.10 < 0.15 floor
    result = run_peer_benchmark(session)
    assert [f for f in result.findings if f.metric_name == "closure_rate"] == []


def test_single_peer_allowed_only_under_lowered_minimum(session):
    add_entity(session, "ENT-01")
    add_entity(session, "ENT-02")
    add_metric(session, "ENT-01", closure_rate=0.50)
    add_metric(session, "ENT-02", closure_rate=0.90)
    # Default min=2 -> insufficient; min=1 -> single-peer zero-MAD comparison.
    assert run_peer_benchmark(session).finding_count == 0
    lowered = run_peer_benchmark(session, PeerBenchmarkConfig(peer_min_count=1))
    cr = [
        f
        for f in lowered.findings
        if f.metric_name == "closure_rate" and f.entity_id == "ENT-01"
    ]
    assert len(cr) == 1
    assert cr[0].deviation_basis == DeviationBasis.ABSOLUTE_ZERO_MAD
    assert cr[0].peer_population_count == 1


def test_robust_threshold_equality_is_inclusive(session):
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    cfg = PeerBenchmarkConfig(mad_scale_factor=1.0, mad_threshold=3.0)
    # peers 1.0,3.0 -> median 2.0, MAD 1.0, scaled 1.0. distance 3.0 at self=5.0.
    add_metric(session, "ENT-02", mttr_hours=1.0)
    add_metric(session, "ENT-03", mttr_hours=3.0)
    add_metric(session, "ENT-01", mttr_hours=5.0)
    at = [f for f in run_peer_benchmark(session, cfg).findings if f.entity_id == "ENT-01" and f.metric_name == "mttr_hours"]
    assert len(at) == 1 and at[0].deviation_measure == pytest.approx(3.0)


def test_just_below_robust_threshold_not_flagged(session):
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    cfg = PeerBenchmarkConfig(mad_scale_factor=1.0, mad_threshold=3.0)
    add_metric(session, "ENT-02", mttr_hours=1.0)
    add_metric(session, "ENT-03", mttr_hours=3.0)
    add_metric(session, "ENT-01", mttr_hours=4.99)  # distance 2.99 < 3.0
    at = [f for f in run_peer_benchmark(session, cfg).findings if f.entity_id == "ENT-01" and f.metric_name == "mttr_hours"]
    assert at == []


def test_missing_peer_metric_excluded_then_insufficient(session):
    # PerformanceMetric columns are NOT NULL, so model missingness is simulated
    # by a peer simply being absent from the period; verify counts are honest.
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    add_metric(session, "ENT-01", mttr_hours=100.0)
    add_metric(session, "ENT-02", mttr_hours=2.0)
    # ENT-03 has no metric this period -> only one peer remains -> insufficient.
    result = run_peer_benchmark(session)
    assert [f for f in result.findings if f.entity_id == "ENT-01"] == []
    st = [s for s in result.statuses if s.entity_id == "ENT-01"]
    assert len(st) == 1 and st[0].peer_population_count == 1


def test_multiple_metrics_produce_separate_findings(session):
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    add_metric(session, "ENT-02", closure_rate=0.90, sla_compliance=0.90)
    add_metric(session, "ENT-03", closure_rate=0.90, sla_compliance=0.90)
    add_metric(session, "ENT-01", closure_rate=0.50, sla_compliance=0.40)
    metrics = {f.metric_name for f in run_peer_benchmark(session).findings if f.entity_id == "ENT-01"}
    assert {"closure_rate", "sla_compliance"} <= metrics


def test_run_is_deterministic_and_read_only(session):
    for eid in ("ENT-01", "ENT-02", "ENT-03"):
        add_entity(session, eid)
    add_metric(session, "ENT-01", mttr_hours=100.0)
    add_metric(session, "ENT-02", mttr_hours=2.0)
    add_metric(session, "ENT-03", mttr_hours=4.0)
    before = session.scalar(select(func.count()).select_from(PerformanceMetric))
    first = run_peer_benchmark(session)
    second = run_peer_benchmark(session)
    after = session.scalar(select(func.count()).select_from(PerformanceMetric))
    assert first == second
    assert before == after


def test_empty_database_returns_safe_result(session):
    result = run_peer_benchmark(session)
    assert result.finding_count == 0
    assert result.statuses == ()


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        PeerBenchmarkConfig(peer_dimension="nonsense")
    with pytest.raises(ValueError):
        PeerBenchmarkConfig(peer_min_count=0)
    with pytest.raises(ValueError):
        PeerBenchmarkConfig(mad_threshold=0.0)
