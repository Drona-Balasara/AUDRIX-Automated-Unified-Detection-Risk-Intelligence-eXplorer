"""Unit tests for the Phase 9 evidence and confidence layer.

Tests are organised by category:

1.  EvidenceRef construction and validation
2.  FindingEvidence construction, deduplication, sorting
3.  Confidence aggregation — aggregate_confidence()
4.  Confidence factor helpers — factors_from_counts()
5.  Absence evidence representation
6.  Multiple evidence references per finding
7.  Duplicate reference deduplication
8.  Deterministic ordering of refs
9.  Confidence boundaries (exact threshold conditions)
10. Period-scope consistency
11. Confidence note generation
12. AnnotatedResult wrappers
13. Config validation
"""

from __future__ import annotations

import pytest

from app.analytics.evidence.confidence import (
    aggregate_confidence,
    factors_from_counts,
    note_from_confidence,
)
from app.analytics.evidence.model import (
    ConfidenceFactor,
    EvidenceConfidence,
    EvidenceRef,
    EvidenceSourceType,
    FindingEvidence,
    make_finding_evidence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ref(
    source_type: EvidenceSourceType = EvidenceSourceType.ALERT,
    source_id: str = "ALR-000001",
    role: str = "triggering_alert",
    period_label: str | None = "2024-01",
    reason: str = "Test reason.",
) -> EvidenceRef:
    return EvidenceRef(
        source_type=source_type,
        source_id=source_id,
        role=role,
        period_label=period_label,
        reason=reason,
    )


def _absence_ref(scope: str = "entity:ENT-01|asset:AST-00001") -> EvidenceRef:
    return EvidenceRef(
        source_type=EvidenceSourceType.ABSENCE,
        source_id=f"missing_escalation|{scope}",
        role="missing_expected_escalation",
        period_label="2024-01",
        reason="Expected escalation was not found.",
    )


# ===========================================================================
# 1. EvidenceRef construction
# ===========================================================================

class TestEvidenceRef:

    def test_basic_construction(self):
        ref = _ref()
        assert ref.source_type == EvidenceSourceType.ALERT
        assert ref.source_id == "ALR-000001"
        assert ref.role == "triggering_alert"
        assert ref.period_label == "2024-01"
        assert ref.reason == "Test reason."

    def test_period_label_optional(self):
        ref = _ref(period_label=None)
        assert ref.period_label is None

    def test_frozen_cannot_mutate(self):
        ref = _ref()
        with pytest.raises(Exception):
            ref.source_id = "CHANGED"  # type: ignore[misc]

    def test_all_source_types_constructable(self):
        for stype in EvidenceSourceType:
            ref = _ref(source_type=stype, source_id=f"ID-{stype.value}")
            assert ref.source_type == stype

    def test_absence_source_type(self):
        ref = _absence_ref()
        assert ref.source_type == EvidenceSourceType.ABSENCE
        assert "missing_escalation" in ref.source_id


# ===========================================================================
# 2. FindingEvidence construction
# ===========================================================================

class TestFindingEvidence:

    def test_basic_construction(self):
        refs = [_ref(), _absence_ref()]
        fe = make_finding_evidence(
            finding_key="EG-001:ENT-01:ALR-000001",
            analytic_id="EG-001",
            refs=refs,
            confidence=EvidenceConfidence.MODERATE,
            factors=[ConfidenceFactor.ABSENCE_BASED],
            confidence_note="Test note.",
        )
        assert fe.finding_key == "EG-001:ENT-01:ALR-000001"
        assert fe.analytic_id == "EG-001"
        assert fe.confidence == EvidenceConfidence.MODERATE
        assert fe.evidence_count == 2

    def test_frozen_model(self):
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=[_ref()],
            confidence=EvidenceConfidence.HIGH,
            factors=[],
        )
        with pytest.raises(Exception):
            fe.finding_key = "changed"  # type: ignore[misc]

    def test_has_absence_evidence_true(self):
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=[_absence_ref()],
            confidence=EvidenceConfidence.LOW,
            factors=[ConfidenceFactor.ABSENCE_BASED],
        )
        assert fe.has_absence_evidence is True

    def test_has_absence_evidence_false(self):
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=[_ref()],
            confidence=EvidenceConfidence.HIGH,
            factors=[],
        )
        assert fe.has_absence_evidence is False

    def test_empty_refs_allowed(self):
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=[],
            confidence=EvidenceConfidence.LOW,
            factors=[ConfidenceFactor.SINGLE_RECORD],
        )
        assert fe.evidence_count == 0

    def test_confidence_note_stored(self):
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=[_ref()],
            confidence=EvidenceConfidence.HIGH,
            factors=[],
            confidence_note="Sufficient evidence.",
        )
        assert fe.confidence_note == "Sufficient evidence."


# ===========================================================================
# 3. Confidence aggregation
# ===========================================================================

class TestAggregateConfidence:

    def test_no_refs_returns_low(self):
        assert aggregate_confidence([], []) == EvidenceConfidence.LOW

    def test_only_absence_refs_returns_low(self):
        refs = [_absence_ref()]
        assert aggregate_confidence([], refs) == EvidenceConfidence.LOW

    def test_direct_ref_no_limiting_factors_returns_high(self):
        assert aggregate_confidence([], [_ref()]) == EvidenceConfidence.HIGH

    def test_fragile_baseline_returns_low(self):
        result = aggregate_confidence(
            [ConfidenceFactor.FRAGILE_BASELINE], [_ref()]
        )
        assert result == EvidenceConfidence.LOW

    def test_small_baseline_returns_low(self):
        result = aggregate_confidence(
            [ConfidenceFactor.SMALL_BASELINE], [_ref()]
        )
        assert result == EvidenceConfidence.LOW

    def test_minimum_threshold_returns_low(self):
        result = aggregate_confidence(
            [ConfidenceFactor.MINIMUM_THRESHOLD], [_ref()]
        )
        assert result == EvidenceConfidence.LOW

    def test_insufficient_periods_returns_low(self):
        result = aggregate_confidence(
            [ConfidenceFactor.INSUFFICIENT_PERIODS], [_ref()]
        )
        assert result == EvidenceConfidence.LOW

    def test_single_record_returns_moderate(self):
        result = aggregate_confidence(
            [ConfidenceFactor.SINGLE_RECORD], [_ref()]
        )
        assert result == EvidenceConfidence.MODERATE

    def test_dropped_observations_returns_moderate(self):
        result = aggregate_confidence(
            [ConfidenceFactor.DROPPED_OBSERVATIONS], [_ref()]
        )
        assert result == EvidenceConfidence.MODERATE

    def test_moderate_baseline_returns_moderate(self):
        result = aggregate_confidence(
            [ConfidenceFactor.MODERATE_BASELINE], [_ref()]
        )
        assert result == EvidenceConfidence.MODERATE

    def test_absence_with_direct_records_returns_moderate(self):
        # Direct record present + absence → MODERATE (not LOW).
        refs = [_ref(), _absence_ref()]
        result = aggregate_confidence([ConfidenceFactor.ABSENCE_BASED], refs)
        assert result == EvidenceConfidence.MODERATE

    def test_low_factor_beats_moderate_factor(self):
        # When both LOW and MODERATE factors present → LOW wins.
        result = aggregate_confidence(
            [ConfidenceFactor.MODERATE_BASELINE, ConfidenceFactor.FRAGILE_BASELINE],
            [_ref()],
        )
        assert result == EvidenceConfidence.LOW

    def test_multiple_positive_factors_returns_high(self):
        result = aggregate_confidence(
            [ConfidenceFactor.MULTIPLE_DIRECT_RECORDS, ConfidenceFactor.LARGE_COMPARISON_POP],
            [_ref(), _ref(source_id="ALR-000002"), _ref(source_id="ALR-000003")],
        )
        assert result == EvidenceConfidence.HIGH


# ===========================================================================
# 4. factors_from_counts helper
# ===========================================================================

class TestFactorsFromCounts:

    def test_three_direct_records_gives_multiple(self):
        factors = factors_from_counts(direct_record_count=3)
        assert ConfidenceFactor.MULTIPLE_DIRECT_RECORDS in factors

    def test_one_direct_record_gives_single(self):
        factors = factors_from_counts(direct_record_count=1)
        assert ConfidenceFactor.SINGLE_RECORD in factors

    def test_two_direct_records_gives_adequate(self):
        factors = factors_from_counts(direct_record_count=2)
        assert ConfidenceFactor.ADEQUATE_RECORD_COUNT in factors

    def test_zero_direct_records_no_count_factor(self):
        factors = factors_from_counts(direct_record_count=0)
        count_factors = {
            ConfidenceFactor.MULTIPLE_DIRECT_RECORDS,
            ConfidenceFactor.SINGLE_RECORD,
            ConfidenceFactor.ADEQUATE_RECORD_COUNT,
        }
        assert not (count_factors & set(factors))

    def test_large_baseline_gives_large_comparison(self):
        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=10,
            min_large_comparison=6,
        )
        assert ConfidenceFactor.LARGE_COMPARISON_POP in factors

    def test_small_baseline_gives_small_baseline(self):
        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=2,
            min_adequate_baseline=3,
        )
        assert ConfidenceFactor.SMALL_BASELINE in factors

    def test_fragile_baseline_flag(self):
        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=4,
            fragile_baseline=True,
        )
        assert ConfidenceFactor.FRAGILE_BASELINE in factors

    def test_moderate_baseline_range(self):
        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=4,
            min_adequate_baseline=3,
            min_large_comparison=8,
        )
        assert ConfidenceFactor.MODERATE_BASELINE in factors

    def test_dropped_observations_flag(self):
        factors = factors_from_counts(direct_record_count=1, has_dropped=True)
        assert ConfidenceFactor.DROPPED_OBSERVATIONS in factors

    def test_absence_flag(self):
        factors = factors_from_counts(direct_record_count=1, has_absence=True)
        assert ConfidenceFactor.ABSENCE_BASED in factors

    def test_minimum_threshold_flag(self):
        factors = factors_from_counts(
            direct_record_count=1, at_minimum_threshold=True
        )
        assert ConfidenceFactor.MINIMUM_THRESHOLD in factors

    def test_insufficient_periods_flag(self):
        factors = factors_from_counts(
            direct_record_count=1,
            period_count=3,
            min_adequate_periods=4,
        )
        assert ConfidenceFactor.INSUFFICIENT_PERIODS in factors

    def test_adequate_periods_no_insufficient_flag(self):
        factors = factors_from_counts(
            direct_record_count=1,
            period_count=5,
            min_adequate_periods=4,
        )
        assert ConfidenceFactor.INSUFFICIENT_PERIODS not in factors


# ===========================================================================
# 5. Absence evidence — no fabrication
# ===========================================================================

class TestAbsenceEvidence:

    def test_absence_ref_has_no_real_pk(self):
        """An absence ref must NOT look like a real database primary key.
        Absence source IDs are descriptive scope strings, never row PKs."""
        ref = _absence_ref()
        # A real alert PK looks like "ALR-000001"; absence IDs use pipe-separated scopes.
        assert "|" in ref.source_id or "missing" in ref.source_id.lower()

    def test_absence_only_evidence_gives_low_confidence(self):
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=[_absence_ref()],
            confidence=aggregate_confidence([ConfidenceFactor.ABSENCE_BASED], [_absence_ref()]),
            factors=[ConfidenceFactor.ABSENCE_BASED],
        )
        assert fe.confidence == EvidenceConfidence.LOW

    def test_absence_plus_direct_gives_moderate_at_most(self):
        refs = [_ref(), _absence_ref()]
        conf = aggregate_confidence([ConfidenceFactor.ABSENCE_BASED], refs)
        assert conf != EvidenceConfidence.HIGH

    def test_multiple_absence_refs_allowed(self):
        refs = [
            _absence_ref("entity:ENT-01|alert:ALR-000001"),
            EvidenceRef(
                source_type=EvidenceSourceType.ABSENCE,
                source_id="missing_investigation|entity:ENT-01|alert:ALR-000002",
                role="missing_investigation",
                reason="No investigation opened.",
            ),
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=refs,
            confidence=EvidenceConfidence.LOW,
            factors=[ConfidenceFactor.ABSENCE_BASED],
        )
        assert fe.evidence_count == 2
        assert fe.has_absence_evidence


# ===========================================================================
# 6. Multiple evidence references per finding
# ===========================================================================

class TestMultipleRefs:

    def test_finding_with_three_alert_refs(self):
        refs = [
            _ref(source_id="ALR-000001"),
            _ref(source_id="ALR-000002"),
            _ref(source_id="ALR-000003"),
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="EG-001",
            refs=refs,
            confidence=EvidenceConfidence.HIGH,
            factors=[ConfidenceFactor.MULTIPLE_DIRECT_RECORDS],
        )
        assert fe.evidence_count == 3

    def test_mixed_source_types(self):
        refs = [
            _ref(source_type=EvidenceSourceType.ALERT, source_id="ALR-000001"),
            _ref(source_type=EvidenceSourceType.INVESTIGATION, source_id="INV-000001"),
            _absence_ref(),
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="EG-001",
            refs=refs,
            confidence=EvidenceConfidence.MODERATE,
            factors=[ConfidenceFactor.ABSENCE_BASED],
        )
        types = {r.source_type for r in fe.evidence_refs}
        assert EvidenceSourceType.ALERT in types
        assert EvidenceSourceType.INVESTIGATION in types
        assert EvidenceSourceType.ABSENCE in types


# ===========================================================================
# 7. Duplicate deduplication
# ===========================================================================

class TestDeduplication:

    def test_duplicate_refs_deduplicated(self):
        """Two refs with same (source_type, source_id) → only one kept."""
        refs = [
            _ref(source_id="ALR-000001"),
            _ref(source_id="ALR-000001"),  # duplicate
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=refs,
            confidence=EvidenceConfidence.MODERATE,
            factors=[ConfidenceFactor.SINGLE_RECORD],
        )
        assert fe.evidence_count == 1

    def test_different_roles_same_id_deduplicates(self):
        """Same (source_type, source_id) with different roles → deduplicated;
        first occurrence wins."""
        refs = [
            _ref(source_id="ALR-000001", role="triggering_alert"),
            _ref(source_id="ALR-000001", role="related_alert"),
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=refs,
            confidence=EvidenceConfidence.MODERATE,
            factors=[],
        )
        assert fe.evidence_count == 1
        # First occurrence is kept
        assert fe.evidence_refs[0].role == "triggering_alert"

    def test_different_ids_not_deduplicated(self):
        refs = [
            _ref(source_id="ALR-000001"),
            _ref(source_id="ALR-000002"),
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=refs,
            confidence=EvidenceConfidence.HIGH,
            factors=[],
        )
        assert fe.evidence_count == 2

    def test_duplicate_factors_deduplicated(self):
        factors = [
            ConfidenceFactor.FRAGILE_BASELINE,
            ConfidenceFactor.FRAGILE_BASELINE,  # duplicate
            ConfidenceFactor.SINGLE_RECORD,
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=[_ref()],
            confidence=EvidenceConfidence.LOW,
            factors=factors,
        )
        count = sum(1 for f in fe.confidence_factors if f == ConfidenceFactor.FRAGILE_BASELINE)
        assert count == 1


# ===========================================================================
# 8. Deterministic ordering
# ===========================================================================

class TestDeterministicOrdering:

    def test_refs_sorted_by_source_type_then_id(self):
        refs = [
            _ref(source_type=EvidenceSourceType.INVESTIGATION, source_id="INV-000002"),
            _ref(source_type=EvidenceSourceType.ALERT, source_id="ALR-000001"),
            _ref(source_type=EvidenceSourceType.ALERT, source_id="ALR-000002"),
        ]
        fe = make_finding_evidence(
            finding_key="k",
            analytic_id="X",
            refs=refs,
            confidence=EvidenceConfidence.HIGH,
            factors=[ConfidenceFactor.MULTIPLE_DIRECT_RECORDS],
        )
        sorted_refs = sorted(fe.evidence_refs, key=lambda r: (r.source_type, r.source_id))
        assert list(fe.evidence_refs) == sorted_refs

    def test_same_input_same_output(self):
        refs = [
            _ref(source_id="ALR-000003"),
            _ref(source_id="ALR-000001"),
            _ref(source_id="ALR-000002"),
        ]
        fe1 = make_finding_evidence(
            "k", "X", refs, EvidenceConfidence.HIGH, []
        )
        fe2 = make_finding_evidence(
            "k", "X", refs, EvidenceConfidence.HIGH, []
        )
        assert fe1 == fe2
        assert fe1.evidence_refs == fe2.evidence_refs

    def test_order_independent_of_input_order(self):
        refs_a = [
            _ref(source_id="ALR-000003"),
            _ref(source_id="ALR-000001"),
        ]
        refs_b = [
            _ref(source_id="ALR-000001"),
            _ref(source_id="ALR-000003"),
        ]
        fe_a = make_finding_evidence("k", "X", refs_a, EvidenceConfidence.HIGH, [])
        fe_b = make_finding_evidence("k", "X", refs_b, EvidenceConfidence.HIGH, [])
        assert fe_a.evidence_refs == fe_b.evidence_refs


# ===========================================================================
# 9. Confidence boundary conditions
# ===========================================================================

class TestConfidenceBoundaries:

    def test_exactly_min_periods_not_insufficient(self):
        """period_count == min_adequate_periods → no INSUFFICIENT_PERIODS factor."""
        factors = factors_from_counts(
            direct_record_count=1,
            period_count=4,
            min_adequate_periods=4,
        )
        assert ConfidenceFactor.INSUFFICIENT_PERIODS not in factors

    def test_one_below_min_periods_is_insufficient(self):
        factors = factors_from_counts(
            direct_record_count=1,
            period_count=3,
            min_adequate_periods=4,
        )
        assert ConfidenceFactor.INSUFFICIENT_PERIODS in factors

    def test_exactly_min_baseline_not_small(self):
        """baseline_count == min_adequate_baseline → no SMALL_BASELINE factor."""
        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=3,
            min_adequate_baseline=3,
        )
        assert ConfidenceFactor.SMALL_BASELINE not in factors

    def test_one_below_min_baseline_is_small(self):
        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=2,
            min_adequate_baseline=3,
        )
        assert ConfidenceFactor.SMALL_BASELINE in factors

    def test_at_minimum_threshold_flag_respected(self):
        factors = factors_from_counts(
            direct_record_count=2,
            at_minimum_threshold=True,
        )
        conf = aggregate_confidence(factors, [_ref(), _ref(source_id="ALR-2")])
        assert conf == EvidenceConfidence.LOW


# ===========================================================================
# 10. Period-scope consistency
# ===========================================================================

class TestPeriodScope:

    def test_period_label_stored_on_ref(self):
        ref = _ref(period_label="2024-03")
        assert ref.period_label == "2024-03"

    def test_period_label_none_stored_as_none(self):
        ref = _ref(period_label=None)
        assert ref.period_label is None

    def test_period_labels_not_mixed_in_finding(self):
        """A finding should ideally carry refs with consistent periods, but
        the model does not enforce this — it is the builder's responsibility.
        Verify that refs retain their individual period labels."""
        refs = [
            _ref(source_id="ALR-000001", period_label="2024-01"),
            _ref(source_id="ALR-000002", period_label="2024-01"),
        ]
        fe = make_finding_evidence(
            "k", "EG-001", refs, EvidenceConfidence.HIGH, []
        )
        for r in fe.evidence_refs:
            assert r.period_label == "2024-01"


# ===========================================================================
# 11. Confidence note generation
# ===========================================================================

class TestConfidenceNote:

    def test_high_note_mentions_records(self):
        note = note_from_confidence(EvidenceConfidence.HIGH, [], 3)
        assert "3" in note

    def test_moderate_note_mentions_limiting(self):
        note = note_from_confidence(
            EvidenceConfidence.MODERATE,
            [ConfidenceFactor.SINGLE_RECORD],
            1,
        )
        assert note  # non-empty
        assert "single" in note.lower() or "record" in note.lower()

    def test_low_note_mentions_fragile(self):
        note = note_from_confidence(
            EvidenceConfidence.LOW,
            [ConfidenceFactor.FRAGILE_BASELINE],
            1,
        )
        assert "fragile" in note.lower() or "thin" in note.lower()

    def test_note_does_not_mention_negligence_or_fraud(self):
        for conf in EvidenceConfidence:
            note = note_from_confidence(conf, list(ConfidenceFactor)[:3], 2)
            forbidden = ["negligence", "fraud", "misconduct", "blame", "manipulation"]
            for word in forbidden:
                assert word.lower() not in note.lower()


# ===========================================================================
# 12. AnnotatedResult wrappers
# ===========================================================================

class TestAnnotatedResults:

    def _make_finding_evidence_pair(self) -> tuple[str, FindingEvidence]:
        key = "TEST-001:ENT-01:2024-01"
        fe = make_finding_evidence(
            finding_key=key,
            analytic_id="TEST-001",
            refs=[_ref()],
            confidence=EvidenceConfidence.HIGH,
            factors=[],
        )
        return key, fe

    def test_annotated_result_evidence_for_lookup(self):
        from app.analytics.evidence.annotated import AnnotatedExecutionGapResult
        from app.analytics.execution_gap.engine import ExecutionGapResult

        key, fe = self._make_finding_evidence_pair()
        base = ExecutionGapResult(entity_ids=(), rule_ids=(), findings=())
        annotated = AnnotatedExecutionGapResult(
            result=base,
            evidence_map={key: fe},
        )
        assert annotated.evidence_for(key) == fe
        assert annotated.evidence_for("nonexistent") is None

    def test_annotated_result_finding_count_delegates(self):
        from app.analytics.evidence.annotated import AnnotatedExecutionGapResult
        from app.analytics.execution_gap.engine import ExecutionGapResult

        base = ExecutionGapResult(entity_ids=(), rule_ids=(), findings=())
        annotated = AnnotatedExecutionGapResult(result=base, evidence_map={})
        assert annotated.finding_count == 0

    def test_annotated_result_is_frozen(self):
        from app.analytics.evidence.annotated import AnnotatedAnomalyResult
        from app.analytics.anomaly.engine import AnomalyResult, AnomalyStatus

        base = AnomalyResult(status=AnomalyStatus.OK, entity_ids=(), findings=())
        annotated = AnnotatedAnomalyResult(result=base, evidence_map={})
        with pytest.raises(Exception):
            annotated.evidence_map = {}  # type: ignore[misc]

    def test_all_six_annotated_wrappers_constructable(self):
        from app.analytics.evidence.annotated import (
            AnnotatedAnomalyResult,
            AnnotatedExecutionGapResult,
            AnnotatedFingerprintResult,
            AnnotatedMetricRiskDivergenceResult,
            AnnotatedNegativeSpaceResult,
            AnnotatedPeerBenchmarkResult,
        )
        from app.analytics.anomaly.engine import AnomalyResult, AnomalyStatus
        from app.analytics.execution_gap.engine import ExecutionGapResult
        from app.analytics.investigation_fingerprinting.engine import FingerprintResult
        from app.analytics.metric_risk_divergence.engine import MetricRiskDivergenceResult
        from app.analytics.negative_space.engine import NegativeSpaceResult
        from app.analytics.peer_benchmark.engine import PeerBenchmarkResult

        # All six should construct without error.
        AnnotatedExecutionGapResult(result=ExecutionGapResult(), evidence_map={})
        AnnotatedNegativeSpaceResult(result=NegativeSpaceResult(), evidence_map={})
        AnnotatedAnomalyResult(
            result=AnomalyResult(status=AnomalyStatus.OK, entity_ids=()),
            evidence_map={},
        )
        AnnotatedPeerBenchmarkResult(result=PeerBenchmarkResult(), evidence_map={})
        AnnotatedMetricRiskDivergenceResult(
            result=MetricRiskDivergenceResult(), evidence_map={}
        )
        AnnotatedFingerprintResult(result=FingerprintResult(), evidence_map={})


# ===========================================================================
# 13. Config / enum completeness
# ===========================================================================

class TestEnumsAndConfig:

    def test_evidence_confidence_has_three_levels(self):
        levels = set(EvidenceConfidence)
        assert EvidenceConfidence.HIGH in levels
        assert EvidenceConfidence.MODERATE in levels
        assert EvidenceConfidence.LOW in levels
        assert len(levels) == 3

    def test_all_source_types_have_string_values(self):
        for stype in EvidenceSourceType:
            assert isinstance(stype.value, str)
            assert stype.value == stype.value.upper()

    def test_all_confidence_factors_have_string_values(self):
        for factor in ConfidenceFactor:
            assert isinstance(factor.value, str)
            assert factor.value == factor.value.upper()

    def test_make_finding_evidence_returns_frozen_model(self):
        fe = make_finding_evidence(
            "k", "X", [_ref()], EvidenceConfidence.HIGH, []
        )
        assert isinstance(fe, FindingEvidence)
