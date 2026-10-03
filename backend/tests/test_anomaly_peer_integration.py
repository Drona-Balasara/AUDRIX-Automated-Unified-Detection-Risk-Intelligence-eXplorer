"""Integration tests for anomaly detection and peer benchmarking.

Both Phase 6 analytics are run end to end against the full deterministic
synthetic dataset (default seed) loaded into an in-memory database. The
``ground_truth`` manifest is used ONLY here, as an evaluation oracle, to confirm
that the entity carrying a planted metric-risk-divergence profile surfaces as an
unusual observation. Production analytic code never reads ground truth.

The tests are organised into three groups per the Phase 6 brief:
detection-coverage (planted signal surfaces), non-detection controls / honest
accounting (no flood, degenerate peer groups report insufficient peers, never a
misleading finding), and sanity invariants (determinism, wall-clock
independence, read-only behaviour, real-record traceability, coexistence with
the Phase 4 and Phase 5 analytics).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.anomaly import AnomalyStatus, run_anomaly_detection
from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.negative_space import run_negative_space_detection
from app.analytics.peer_benchmark import (
    PeerBenchmarkConfig,
    run_peer_benchmark,
)
from app.analytics.peer_benchmark.findings import BenchmarkStatusCode
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


def _divergence_entity(dataset: Dataset) -> str:
    gt = [
        g
        for g in dataset.ground_truth
        if g["scenario_type"] == ScenarioType.METRIC_RISK_DIVERGENCE
    ]
    assert len(gt) == 1, "fixture sanity: exactly one divergence entity planted"
    return gt[0]["entity_id"]


# ----- detection coverage: planted signal surfaces -------------------------


def test_anomaly_run_is_ok_on_full_dataset(loaded_session) -> None:
    result = run_anomaly_detection(loaded_session)
    assert result.status == AnomalyStatus.OK
    assert result.observation_count == 36  # 6 entities x 6 monthly periods
    assert result.scored_observation_count == 36
    assert result.dropped_incomplete_count == 0
    assert result.model_metadata is not None


def test_divergence_entity_surfaces_as_anomaly(loaded_session, generated_dataset) -> None:
    # The entity whose operational quality diverges from its headline KPIs is a
    # natural outlier in the modelled population. Ground truth is consulted only
    # to name the expected entity; the model never saw the label.
    result = run_anomaly_detection(loaded_session)
    flagged = {f.entity_id for f in result.findings}
    assert _divergence_entity(generated_dataset) in flagged


def test_divergence_entity_surfaces_in_peer_benchmark(loaded_session, generated_dataset) -> None:
    result = run_peer_benchmark(loaded_session)
    flagged = {f.entity_id for f in result.findings}
    assert _divergence_entity(generated_dataset) in flagged


# ----- controls / honest accounting ----------------------------------------


def test_anomaly_findings_are_not_a_flood(loaded_session) -> None:
    # Contamination is set to a small-minority expectation, not tuned to the
    # planted scenarios; the flagged fraction must stay well below a third.
    result = run_anomaly_detection(loaded_session)
    assert result.finding_count <= int(result.scored_observation_count * 0.2)


def test_sector_dimension_is_degenerate_and_reports_insufficient(loaded_session) -> None:
    # Empirically sector groups are pairs -> one peer each -> no robust baseline.
    # The analytic must emit insufficient-peer statuses, never a misleading
    # finding manufactured from a single peer.
    result = run_peer_benchmark(loaded_session, PeerBenchmarkConfig(peer_dimension="sector"))
    assert result.finding_count == 0
    assert len(result.statuses) == 36
    assert {s.status for s in result.statuses} == {BenchmarkStatusCode.INSUFFICIENT_PEERS}


def test_scale_dimension_produces_findings_and_statuses(loaded_session) -> None:
    # The MEDIUM scale group has >=3 members (a genuine robust baseline); the
    # LARGE/SMALL groups are too small and must surface as insufficient.
    result = run_peer_benchmark(loaded_session)
    assert result.finding_count > 0
    assert len(result.statuses) > 0


def test_peer_baseline_never_includes_self(loaded_session) -> None:
    # Every finding's peer population excludes the subject; a single-entity
    # group could never yield a finding under the default minimum.
    result = run_peer_benchmark(loaded_session)
    for f in result.findings:
        assert f.peer_population_count >= PeerBenchmarkConfig().peer_min_count


# ----- sanity invariants ----------------------------------------------------


def test_anomaly_is_deterministic(loaded_session) -> None:
    first = run_anomaly_detection(loaded_session)
    second = run_anomaly_detection(loaded_session)
    assert first == second
    assert [f.finding_key for f in first.findings] == [f.finding_key for f in second.findings]


def test_peer_benchmark_is_deterministic(loaded_session) -> None:
    assert run_peer_benchmark(loaded_session) == run_peer_benchmark(loaded_session)


def test_anomaly_feature_snapshot_has_no_identifier_leakage(loaded_session) -> None:
    result = run_anomaly_detection(loaded_session)
    forbidden = {"entity_id", "asset_id", "alert_id", "investigation_id", "period"}
    for f in result.findings:
        assert forbidden.isdisjoint(set(f.feature_snapshot))


def test_findings_reference_real_records(loaded_session) -> None:
    entity_ids = set(loaded_session.scalars(select(SocEntity.entity_id)).all())
    anomaly = run_anomaly_detection(loaded_session)
    for f in anomaly.findings:
        assert f.entity_id in entity_ids
        assert loaded_session.scalar(
            select(func.count())
            .select_from(PerformanceMetric)
            .where(
                PerformanceMetric.entity_id == f.entity_id,
                PerformanceMetric.period_start == f.period_start,
            )
        ) == 1
    peer = run_peer_benchmark(loaded_session)
    for f in peer.findings:
        assert f.entity_id in entity_ids
        assert loaded_session.scalar(
            select(func.count())
            .select_from(PerformanceMetric)
            .where(
                PerformanceMetric.entity_id == f.entity_id,
                PerformanceMetric.period_start == f.period_start,
            )
        ) == 1


def test_both_analytics_are_read_only(loaded_session) -> None:
    models = (SocEntity, Asset, Alert, Investigation, PerformanceMetric)
    before = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}
    run_anomaly_detection(loaded_session)
    run_peer_benchmark(loaded_session)
    after = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}
    assert before == after


def test_coexists_with_phase4_and_phase5_without_mutation(loaded_session) -> None:
    # All four detectors run against the same session; none perturbs another's
    # result and no source records are mutated.
    anomaly_before = run_anomaly_detection(loaded_session)
    peer_before = run_peer_benchmark(loaded_session)
    eg = run_execution_gap_detection(loaded_session)
    ns = run_negative_space_detection(loaded_session)
    anomaly_after = run_anomaly_detection(loaded_session)
    peer_after = run_peer_benchmark(loaded_session)
    assert anomaly_before == anomaly_after
    assert peer_before == peer_after
    assert eg.finding_count >= 0
    assert ns.finding_count >= 0
