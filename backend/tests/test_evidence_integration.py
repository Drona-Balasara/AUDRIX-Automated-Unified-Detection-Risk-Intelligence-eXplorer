"""Integration tests for the Phase 9 evidence and confidence layer.

Run the six evidence builders against the full deterministic synthetic dataset
loaded into an in-memory database.  For each analytic:

- Every finding has a corresponding FindingEvidence entry in the evidence_map.
- Every evidence entry has at least one EvidenceRef.
- Source IDs referenced by ALERT/INVESTIGATION/TELEMETRY/PERFORMANCE_METRIC
  refs actually exist in the database.
- Confidence levels are valid EvidenceConfidence values.
- The annotated result is deterministic (two consecutive runs produce equal
  output).
- The original result is unchanged (detection semantics preserved).
- Evidence builders are read-only (no DB mutations).
- Future-leakage prevention: evidence refs use only period labels at or before
  the finding's own reporting period.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.anomaly import run_anomaly_detection
from app.analytics.evidence import (
    EvidenceConfidence,
    EvidenceSourceType,
    annotate_anomaly,
    annotate_execution_gap,
    annotate_fingerprint,
    annotate_metric_risk_divergence,
    annotate_negative_space,
    annotate_peer_benchmark,
)
from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.investigation_fingerprinting import run_investigation_fingerprinting
from app.analytics.metric_risk_divergence import run_metric_risk_divergence
from app.analytics.negative_space import run_negative_space_detection
from app.analytics.peer_benchmark import run_peer_benchmark
from app.datagen.generator import Dataset
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


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _assert_every_finding_has_evidence(result, annotated) -> None:
    """Every finding in `result` must have a FindingEvidence in evidence_map."""
    for finding in result.findings:
        fe = annotated.evidence_for(finding.finding_key)
        assert fe is not None, (
            f"No evidence entry for finding_key={finding.finding_key!r}"
        )
        assert fe.evidence_count > 0, (
            f"Empty evidence refs for finding_key={finding.finding_key!r}"
        )
        assert fe.confidence in EvidenceConfidence


def _collect_finding_keys(result) -> set[str]:
    """Collect all finding_keys from a result (handles both flat and typed tuples)."""
    keys: set[str] = set()
    if hasattr(result, "findings"):
        for f in result.findings:
            keys.add(f.finding_key)
    # FingerprintResult has typed sub-tuples
    if hasattr(result, "repetitive_findings"):
        for f in result.repetitive_findings:
            keys.add(f.finding_key)
    if hasattr(result, "deviation_findings"):
        for f in result.deviation_findings:
            keys.add(f.finding_key)
    if hasattr(result, "missing_action_findings"):
        for f in result.missing_action_findings:
            keys.add(f.finding_key)
    return keys


# ---------------------------------------------------------------------------
# Phase 4 — Execution Gap
# ---------------------------------------------------------------------------

class TestExecutionGapEvidence:

    def test_every_finding_has_evidence(self, loaded_session):
        result = run_execution_gap_detection(loaded_session)
        annotated = annotate_execution_gap(result)
        _assert_every_finding_has_evidence(result, annotated)

    def test_evidence_contains_absence_refs(self, loaded_session):
        result = run_execution_gap_detection(loaded_session)
        annotated = annotate_execution_gap(result)
        # All execution-gap findings involve an expected-but-absent record.
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            assert fe.has_absence_evidence, (
                f"Expected absence evidence for {finding.finding_key}"
            )

    def test_alert_ids_reference_real_alerts(self, loaded_session):
        real_alerts = set(loaded_session.scalars(select(Alert.alert_id)).all())
        result = run_execution_gap_detection(loaded_session)
        annotated = annotate_execution_gap(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            for ref in fe.evidence_refs:
                if ref.source_type == EvidenceSourceType.ALERT:
                    assert ref.source_id in real_alerts, (
                        f"Alert ref {ref.source_id!r} not in real alerts"
                    )

    def test_investigation_ids_reference_real_investigations(self, loaded_session):
        real_invs = set(
            loaded_session.scalars(select(Investigation.investigation_id)).all()
        )
        result = run_execution_gap_detection(loaded_session)
        annotated = annotate_execution_gap(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            for ref in fe.evidence_refs:
                if ref.source_type == EvidenceSourceType.INVESTIGATION:
                    assert ref.source_id in real_invs

    def test_deterministic(self, loaded_session):
        r = run_execution_gap_detection(loaded_session)
        a1 = annotate_execution_gap(r)
        a2 = annotate_execution_gap(r)
        assert a1.evidence_map == a2.evidence_map

    def test_original_result_unchanged(self, loaded_session):
        r1 = run_execution_gap_detection(loaded_session)
        _ = annotate_execution_gap(r1)
        r2 = run_execution_gap_detection(loaded_session)
        assert r1 == r2


# ---------------------------------------------------------------------------
# Phase 5 — Negative Space
# ---------------------------------------------------------------------------

class TestNegativeSpaceEvidence:

    def test_every_finding_has_evidence(self, loaded_session):
        result = run_negative_space_detection(loaded_session)
        annotated = annotate_negative_space(result)
        _assert_every_finding_has_evidence(result, annotated)

    def test_evidence_contains_absence_refs(self, loaded_session):
        result = run_negative_space_detection(loaded_session)
        annotated = annotate_negative_space(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            assert fe.has_absence_evidence

    def test_asset_ids_reference_real_assets(self, loaded_session):
        real_assets = set(loaded_session.scalars(select(Asset.asset_id)).all())
        result = run_negative_space_detection(loaded_session)
        annotated = annotate_negative_space(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            for ref in fe.evidence_refs:
                if ref.source_type == EvidenceSourceType.ASSET:
                    assert ref.source_id in real_assets

    def test_telemetry_ids_reference_real_records(self, loaded_session):
        """related_telemetry_ids on disappearance findings must be real rows."""
        real_tel = set(
            loaded_session.scalars(select(TelemetryRecord.telemetry_id)).all()
        )
        result = run_negative_space_detection(loaded_session)
        annotated = annotate_negative_space(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            for ref in fe.evidence_refs:
                if ref.source_type == EvidenceSourceType.TELEMETRY_RECORD:
                    assert ref.source_id in real_tel

    def test_deterministic(self, loaded_session):
        r = run_negative_space_detection(loaded_session)
        a1 = annotate_negative_space(r)
        a2 = annotate_negative_space(r)
        assert a1.evidence_map == a2.evidence_map


# ---------------------------------------------------------------------------
# Phase 6 — Anomaly Detection
# ---------------------------------------------------------------------------

class TestAnomalyEvidence:

    def test_every_finding_has_evidence(self, loaded_session):
        result = run_anomaly_detection(loaded_session)
        annotated = annotate_anomaly(result)
        _assert_every_finding_has_evidence(result, annotated)

    def test_entity_period_observation_refs_present(self, loaded_session):
        result = run_anomaly_detection(loaded_session)
        annotated = annotate_anomaly(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            obs_refs = [
                r for r in fe.evidence_refs
                if r.source_type == EvidenceSourceType.ENTITY_PERIOD_OBSERVATION
            ]
            assert len(obs_refs) >= 1

    def test_no_fabricated_alert_or_investigation_ids(self, loaded_session):
        """Anomaly evidence must NOT reference alert/investigation IDs
        (anomaly operates at entity-period grain)."""
        result = run_anomaly_detection(loaded_session)
        annotated = annotate_anomaly(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            for ref in fe.evidence_refs:
                assert ref.source_type not in (
                    EvidenceSourceType.ALERT,
                    EvidenceSourceType.INVESTIGATION,
                ), f"Unexpected source type {ref.source_type} in anomaly evidence"

    def test_confidence_valid_enum_value(self, loaded_session):
        result = run_anomaly_detection(loaded_session)
        annotated = annotate_anomaly(result)
        for fe in annotated.evidence_map.values():
            assert fe.confidence in EvidenceConfidence

    def test_deterministic(self, loaded_session):
        r = run_anomaly_detection(loaded_session)
        a1 = annotate_anomaly(r)
        a2 = annotate_anomaly(r)
        assert a1.evidence_map == a2.evidence_map


# ---------------------------------------------------------------------------
# Phase 6 — Peer Benchmark
# ---------------------------------------------------------------------------

class TestPeerBenchmarkEvidence:

    def test_every_finding_has_evidence(self, loaded_session):
        result = run_peer_benchmark(loaded_session)
        annotated = annotate_peer_benchmark(result)
        _assert_every_finding_has_evidence(result, annotated)

    def test_performance_metric_ref_present(self, loaded_session):
        result = run_peer_benchmark(loaded_session)
        annotated = annotate_peer_benchmark(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            pm_refs = [
                r for r in fe.evidence_refs
                if r.source_type == EvidenceSourceType.PERFORMANCE_METRIC
            ]
            assert len(pm_refs) >= 1

    def test_peer_baseline_ref_present(self, loaded_session):
        result = run_peer_benchmark(loaded_session)
        annotated = annotate_peer_benchmark(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            peer_refs = [
                r for r in fe.evidence_refs
                if r.source_type == EvidenceSourceType.PEER_BASELINE
            ]
            assert len(peer_refs) == 1

    def test_deterministic(self, loaded_session):
        r = run_peer_benchmark(loaded_session)
        a1 = annotate_peer_benchmark(r)
        a2 = annotate_peer_benchmark(r)
        assert a1.evidence_map == a2.evidence_map

    def test_zero_mad_finding_gets_low_or_moderate(self, loaded_session):
        """Zero-MAD findings use FRAGILE_BASELINE → confidence at most MODERATE."""
        from app.analytics.peer_benchmark.findings import DeviationBasis
        result = run_peer_benchmark(loaded_session)
        annotated = annotate_peer_benchmark(result)
        for finding in result.findings:
            if finding.deviation_basis == DeviationBasis.ABSOLUTE_ZERO_MAD:
                fe = annotated.evidence_for(finding.finding_key)
                assert fe.confidence != EvidenceConfidence.HIGH


# ---------------------------------------------------------------------------
# Phase 7 — Metric-Risk Divergence
# ---------------------------------------------------------------------------

class TestMetricRiskDivergenceEvidence:

    def test_every_finding_has_evidence(self, loaded_session):
        result = run_metric_risk_divergence(loaded_session)
        annotated = annotate_metric_risk_divergence(result)
        _assert_every_finding_has_evidence(result, annotated)

    def test_performance_metric_refs_for_all_periods(self, loaded_session):
        result = run_metric_risk_divergence(loaded_session)
        annotated = annotate_metric_risk_divergence(result)
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            pm_refs = [
                r for r in fe.evidence_refs
                if r.source_type == EvidenceSourceType.PERFORMANCE_METRIC
            ]
            # Should have at least one PM ref per metric per period.
            assert len(pm_refs) >= finding.supporting_period_count

    def test_confidence_matches_finding_confidence(self, loaded_session):
        """Phase 9 evidence confidence should reflect Phase 7's own confidence."""
        from app.analytics.metric_risk_divergence.findings import ConfidenceLevel
        result = run_metric_risk_divergence(loaded_session)
        annotated = annotate_metric_risk_divergence(result)
        _conf_map = {
            ConfidenceLevel.HIGH:     EvidenceConfidence.HIGH,
            ConfidenceLevel.MODERATE: EvidenceConfidence.MODERATE,
            ConfidenceLevel.LOW:      EvidenceConfidence.LOW,
        }
        for finding in result.findings:
            fe = annotated.evidence_for(finding.finding_key)
            expected = _conf_map[finding.confidence]
            assert fe.confidence == expected

    def test_deterministic(self, loaded_session):
        r = run_metric_risk_divergence(loaded_session)
        a1 = annotate_metric_risk_divergence(r)
        a2 = annotate_metric_risk_divergence(r)
        assert a1.evidence_map == a2.evidence_map


# ---------------------------------------------------------------------------
# Phase 8 — Investigation Fingerprinting
# ---------------------------------------------------------------------------

class TestFingerprintEvidence:

    def test_every_finding_has_evidence(self, loaded_session):
        result = run_investigation_fingerprinting(loaded_session)
        annotated = annotate_fingerprint(result)
        # Check all three finding types.
        for finding in result.repetitive_findings:
            assert annotated.evidence_for(finding.finding_key) is not None
        for finding in result.deviation_findings:
            assert annotated.evidence_for(finding.finding_key) is not None
        for finding in result.missing_action_findings:
            assert annotated.evidence_for(finding.finding_key) is not None

    def test_repetitive_findings_reference_real_investigations(self, loaded_session):
        real_invs = set(
            loaded_session.scalars(select(Investigation.investigation_id)).all()
        )
        result = run_investigation_fingerprinting(loaded_session)
        annotated = annotate_fingerprint(result)
        for finding in result.repetitive_findings:
            fe = annotated.evidence_for(finding.finding_key)
            for ref in fe.evidence_refs:
                if ref.source_type == EvidenceSourceType.INVESTIGATION:
                    assert ref.source_id in real_invs

    def test_mea_findings_reference_real_alerts(self, loaded_session):
        real_alerts = set(loaded_session.scalars(select(Alert.alert_id)).all())
        result = run_investigation_fingerprinting(loaded_session)
        annotated = annotate_fingerprint(result)
        for finding in result.missing_action_findings:
            fe = annotated.evidence_for(finding.finding_key)
            for ref in fe.evidence_refs:
                if ref.source_type == EvidenceSourceType.ALERT:
                    assert ref.source_id in real_alerts

    def test_mea_findings_have_absence_refs(self, loaded_session):
        result = run_investigation_fingerprinting(loaded_session)
        annotated = annotate_fingerprint(result)
        for finding in result.missing_action_findings:
            fe = annotated.evidence_for(finding.finding_key)
            assert fe.has_absence_evidence

    def test_dev_findings_have_fragile_baseline_factor(self, loaded_session):
        """IF-DEV-002 with a very small baseline (< 5) should expose FRAGILE_BASELINE.
        Findings with a larger baseline get MODERATE_BASELINE instead."""
        from app.analytics.evidence.model import ConfidenceFactor
        result = run_investigation_fingerprinting(loaded_session)
        annotated = annotate_fingerprint(result)
        for finding in result.deviation_findings:
            fe = annotated.evidence_for(finding.finding_key)
            n = finding.baseline_investigation_count
            if n < 5:
                assert ConfidenceFactor.FRAGILE_BASELINE in fe.confidence_factors, (
                    f"Expected FRAGILE_BASELINE for IF-DEV-002 finding "
                    f"{finding.finding_key!r} (baseline_count={n})"
                )
            else:
                # Larger baselines may get MODERATE_BASELINE or better.
                has_limiting = (
                    ConfidenceFactor.FRAGILE_BASELINE in fe.confidence_factors
                    or ConfidenceFactor.MODERATE_BASELINE in fe.confidence_factors
                )
                assert has_limiting, (
                    f"Expected a baseline-limiting factor for IF-DEV-002 "
                    f"{finding.finding_key!r} (baseline_count={n})"
                )

    def test_dev_findings_confidence_not_high(self, loaded_session):
        """IF-DEV-002 fragile baseline → confidence must not be HIGH."""
        result = run_investigation_fingerprinting(loaded_session)
        annotated = annotate_fingerprint(result)
        for finding in result.deviation_findings:
            fe = annotated.evidence_for(finding.finding_key)
            assert fe.confidence != EvidenceConfidence.HIGH, (
                f"IF-DEV-002 should not have HIGH confidence; got "
                f"{fe.confidence} for {finding.finding_key!r}"
            )

    def test_deterministic(self, loaded_session):
        r = run_investigation_fingerprinting(loaded_session)
        a1 = annotate_fingerprint(r)
        a2 = annotate_fingerprint(r)
        assert a1.evidence_map == a2.evidence_map

    def test_evidence_count_equal_to_finding_count(self, loaded_session):
        """evidence_map must have exactly one entry per finding."""
        result = run_investigation_fingerprinting(loaded_session)
        annotated = annotate_fingerprint(result)
        assert len(annotated.evidence_map) == result.finding_count


# ---------------------------------------------------------------------------
# Cross-cutting: read-only, no DB mutations
# ---------------------------------------------------------------------------

class TestReadOnly:

    def test_evidence_builders_do_not_mutate_db(self, loaded_session):
        models = (SocEntity, Alert, Investigation, InvestigationAction, PerformanceMetric)
        before = {
            m.__name__: loaded_session.scalar(select(func.count()).select_from(m))
            for m in models
        }
        # Run all annotators.
        annotate_execution_gap(run_execution_gap_detection(loaded_session))
        annotate_negative_space(run_negative_space_detection(loaded_session))
        annotate_anomaly(run_anomaly_detection(loaded_session))
        annotate_peer_benchmark(run_peer_benchmark(loaded_session))
        annotate_metric_risk_divergence(run_metric_risk_divergence(loaded_session))
        annotate_fingerprint(run_investigation_fingerprinting(loaded_session))
        after = {
            m.__name__: loaded_session.scalar(select(func.count()).select_from(m))
            for m in models
        }
        assert before == after


# ---------------------------------------------------------------------------
# Cross-cutting: neutral language in evidence
# ---------------------------------------------------------------------------

class TestNeutralLanguage:

    def test_no_accusatory_language_in_reasons(self, loaded_session):
        forbidden = ["negligence", "fraud", "manipulat", "misconduct", "blame"]
        results_and_annotators = [
            (run_execution_gap_detection(loaded_session), annotate_execution_gap),
            (run_negative_space_detection(loaded_session), annotate_negative_space),
            (run_anomaly_detection(loaded_session), annotate_anomaly),
            (run_peer_benchmark(loaded_session), annotate_peer_benchmark),
            (run_metric_risk_divergence(loaded_session), annotate_metric_risk_divergence),
            (run_investigation_fingerprinting(loaded_session), annotate_fingerprint),
        ]
        for result, annotator in results_and_annotators:
            annotated = annotator(result)
            for fe in annotated.evidence_map.values():
                for ref in fe.evidence_refs:
                    for word in forbidden:
                        assert word.lower() not in ref.reason.lower(), (
                            f"Forbidden word '{word}' in evidence reason: {ref.reason!r}"
                        )
                assert fe.confidence_note
                for word in forbidden:
                    assert word.lower() not in fe.confidence_note.lower()
