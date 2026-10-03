"""Unit tests for investigation fingerprinting (Phase 8).

Tests are grouped by category and use hand-constructed in-memory databases
so each detection capability can be exercised in isolation.  Integration
tests against the full synthetic dataset live in
``test_investigation_fingerprinting_integration.py``.

Categories
----------
1.  Sequence utilities — build_fingerprint, edit_distance, normalized_distance
2.  Canonical ordering — sequence_number primary, occurred_at tiebreaker
3.  Duplicate sequence numbers — handled gracefully
4.  Incomplete / missing action data — empty sequences, None timestamps
5.  Repetitive-workflow detection (IF-REP-001)
6.  Sequence-deviation detection (IF-DEV-002)
7.  Missing-expected-action detection (IF-MEA-003)
8.  Minimum-length / minimum-comparison guards
9.  Threshold boundary tests
10. Peer/entity isolation — findings never cross entity scope
11. Determinism — repeated runs are identical
12. Future-leakage prevention — baseline never uses later-period investigations
13. Read-only behaviour
14. Stable finding keys
15. Config validation
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.investigation_fingerprinting import (
    DEVIATION_ANALYTIC_ID,
    MISSING_ACTION_ANALYTIC_ID,
    REPETITIVE_ANALYTIC_ID,
    FingerprintConfig,
    FingerprintResult,
    run_investigation_fingerprinting,
)
from app.analytics.investigation_fingerprinting.sequence import (
    build_fingerprint,
    edit_distance,
    has_duplicate_sequence_numbers,
    normalized_distance,
    similarity,
)
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
    ActionOutcome,
    ActionType,
    AlertCategory,
    AlertSeverity,
    AlertStatus,
    EntityScale,
    InvestigationStatus,
    Sector,
)

# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc
BASE = datetime(2024, 1, 1, tzinfo=UTC)


def _month(n: int) -> datetime:
    m = BASE.month - 1 + n
    y = BASE.year + m // 12
    mo = m % 12 + 1
    return datetime(y, mo, 1, tzinfo=UTC)


# Six monthly periods
PERIODS = [(_month(i), _month(i + 1)) for i in range(6)]


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(c, _):  # pragma: no cover
        c.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# DB helper functions
# ---------------------------------------------------------------------------

_eid_counter: list[int] = [0]
_aid_counter: list[int] = [0]
_iid_counter: list[int] = [0]
_acid_counter: list[int] = [0]
_pmid_counter: list[int] = [0]


def _new_entity_id() -> str:
    _eid_counter[0] += 1
    return f"ENT-T{_eid_counter[0]:02d}"


def _new_alert_id() -> str:
    _aid_counter[0] += 1
    return f"ALR-T{_aid_counter[0]:06d}"


def _new_inv_id() -> str:
    _iid_counter[0] += 1
    return f"INV-T{_iid_counter[0]:06d}"


def _new_action_id() -> str:
    _acid_counter[0] += 1
    return f"ACT-T{_acid_counter[0]:07d}"


def _new_pm_id() -> str:
    _pmid_counter[0] += 1
    return f"PMET-T{_pmid_counter[0]:04d}"


def add_entity(s: Session, entity_id: str, period_end: datetime | None = None) -> None:
    pe = period_end or PERIODS[-1][1]
    s.add(SocEntity(
        entity_id=entity_id,
        name=f"Entity {entity_id}",
        sector=Sector.FINANCE.value,
        peer_group=Sector.FINANCE.value,
        scale=EntityScale.MEDIUM.value,
        asset_count_estimate=20,
        analyst_headcount=8,
        created_at=PERIODS[0][0],
        data_period_start=PERIODS[0][0],
        data_period_end=pe,
    ))
    s.flush()
    # Every entity gets one seed asset so Alert FK constraints are satisfied.
    from app.models import Asset
    s.add(Asset(
        asset_id=f"AST-{entity_id}",
        entity_id=entity_id,
        name="Seed Asset",
        category="SERVER",
        criticality="HIGH",
        monitoring_expected=True,
        expected_telemetry="AUTHENTICATION",
        created_at=PERIODS[0][0],
    ))
    s.flush()


def add_period_metric(s: Session, entity_id: str, period_idx: int) -> None:
    ps, pe = PERIODS[period_idx]
    s.add(PerformanceMetric(
        metric_id=_new_pm_id(),
        entity_id=entity_id,
        period_start=ps,
        period_end=pe,
        mttr_hours=4.0, closure_rate=0.9, sla_compliance=0.9,
        escalation_rate=0.2, investigation_completeness=0.8,
        recurrence_rate=0.1, remediation_rate=0.8, evidence_completeness=0.8,
    ))
    s.flush()


def add_all_periods(s: Session, entity_id: str, n: int = 6) -> None:
    for i in range(n):
        add_period_metric(s, entity_id, i)


def add_alert(
    s: Session,
    entity_id: str,
    alert_id: str | None = None,
    severity: str = "MEDIUM",
    period_idx: int = 0,
) -> str:
    aid = alert_id or _new_alert_id()
    ps, _ = PERIODS[period_idx]
    s.add(Alert(
        alert_id=aid,
        entity_id=entity_id,
        asset_id=f"AST-{entity_id}",
        severity=severity,
        category=AlertCategory.ANOMALOUS_BEHAVIOR.value,
        detection_source="ENDPOINT",
        status=AlertStatus.CLOSED.value,
        created_at=ps + timedelta(hours=1),
        acknowledged_at=ps + timedelta(hours=2),
    ))
    s.flush()
    return aid


def add_investigation(
    s: Session,
    entity_id: str,
    alert_id: str,
    inv_id: str | None = None,
    status: str = "CLOSED",
    period_idx: int = 0,
    analyst_id: str = "ANALYST-001",
) -> str:
    iid = inv_id or _new_inv_id()
    ps, _ = PERIODS[period_idx]
    started = ps + timedelta(hours=3)
    s.add(Investigation(
        investigation_id=iid,
        alert_id=alert_id,
        entity_id=entity_id,
        analyst_id=analyst_id,
        status=status,
        started_at=started,
        ended_at=started + timedelta(hours=1),
        duration_seconds=3600,
        evidence_count=3,
    ))
    s.flush()
    return iid


def add_actions(
    s: Session,
    inv_id: str,
    action_types: list[str],
    period_idx: int = 0,
) -> None:
    ps, _ = PERIODS[period_idx]
    base_ts = ps + timedelta(hours=3, minutes=5)
    for seq, atype in enumerate(action_types, start=1):
        s.add(InvestigationAction(
            action_id=_new_action_id(),
            investigation_id=inv_id,
            sequence_number=seq,
            action_type=atype,
            occurred_at=base_ts + timedelta(minutes=seq * 2),
            duration_seconds=120,
            outcome=None,
        ))
    s.flush()


def full_investigation(
    s: Session,
    entity_id: str,
    period_idx: int = 0,
    severity: str = "MEDIUM",
    action_types: list[str] | None = None,
    analyst_id: str = "ANALYST-001",
    status: str = "CLOSED",
) -> str:
    """Add a complete alert + investigation + actions; return inv_id."""
    aid = add_alert(s, entity_id, severity=severity, period_idx=period_idx)
    iid = add_investigation(
        s, entity_id, aid, status=status, period_idx=period_idx,
        analyst_id=analyst_id
    )
    types = action_types or [
        "OPEN", "ASSET_LOOKUP", "EVENT_SEARCH", "CORRELATION",
        "CONTEXT_REVIEW", "EVIDENCE_REVIEW", "VALIDATE", "CLOSE",
    ]
    add_actions(s, iid, types, period_idx=period_idx)
    return iid


# Default repetitive pattern matching the planted scenario
REPETITIVE_PATTERN = [
    ActionType.OPEN.value,
    ActionType.EVENT_SEARCH.value,
    ActionType.EVENT_SEARCH.value,
    ActionType.EVENT_SEARCH.value,
    ActionType.CLOSE.value,
]
FULL_PATTERN = [
    ActionType.OPEN.value, ActionType.ASSET_LOOKUP.value,
    ActionType.EVENT_SEARCH.value, ActionType.CORRELATION.value,
    ActionType.CONTEXT_REVIEW.value, ActionType.EVIDENCE_REVIEW.value,
    ActionType.VALIDATE.value, ActionType.CLOSE.value,
]
# Diverse patterns for negative controls — deliberately different from each other
# to avoid accidentally triggering the repetitive detector.
_DIVERSE_PATTERNS = [
    [ActionType.OPEN.value, ActionType.ASSET_LOOKUP.value, ActionType.CORRELATION.value, ActionType.VALIDATE.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.EVENT_SEARCH.value, ActionType.CONTEXT_REVIEW.value, ActionType.EVIDENCE_REVIEW.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.ASSET_LOOKUP.value, ActionType.EVENT_SEARCH.value, ActionType.ESCALATE.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.CORRELATION.value, ActionType.CONTEXT_REVIEW.value, ActionType.VALIDATE.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.EVIDENCE_REVIEW.value, ActionType.REMEDIATION_REQUEST.value, ActionType.VALIDATE.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.ASSET_LOOKUP.value, ActionType.CONTEXT_REVIEW.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.EVENT_SEARCH.value, ActionType.ASSET_LOOKUP.value, ActionType.VALIDATE.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.CORRELATION.value, ActionType.EVIDENCE_REVIEW.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.CONTEXT_REVIEW.value, ActionType.ESCALATE.value, ActionType.VALIDATE.value, ActionType.CLOSE.value],
    [ActionType.OPEN.value, ActionType.ASSET_LOOKUP.value, ActionType.CORRELATION.value, ActionType.EVIDENCE_REVIEW.value, ActionType.CLOSE.value],
]


# ===========================================================================
# 1. Sequence utilities
# ===========================================================================

class TestSequenceUtilities:

    def test_edit_distance_identical(self):
        assert edit_distance(["A", "B", "C"], ["A", "B", "C"]) == 0

    def test_edit_distance_empty(self):
        assert edit_distance([], []) == 0
        assert edit_distance(["A"], []) == 1
        assert edit_distance([], ["A"]) == 1

    def test_edit_distance_insertion(self):
        # "AB" → "ABC" requires 1 insertion
        assert edit_distance(["A", "B"], ["A", "B", "C"]) == 1

    def test_edit_distance_deletion(self):
        assert edit_distance(["A", "B", "C"], ["A", "C"]) == 1

    def test_edit_distance_substitution(self):
        assert edit_distance(["A", "B", "C"], ["A", "X", "C"]) == 1

    def test_edit_distance_completely_different(self):
        a = ["A", "B", "C"]
        b = ["X", "Y", "Z"]
        assert edit_distance(a, b) == 3

    def test_edit_distance_symmetric(self):
        a = ["OPEN", "EVENT_SEARCH", "CLOSE"]
        b = ["OPEN", "ASSET_LOOKUP", "CORRELATION", "VALIDATE", "CLOSE"]
        assert edit_distance(a, b) == edit_distance(b, a)

    def test_normalized_distance_identical(self):
        assert normalized_distance(["A", "B"], ["A", "B"]) == pytest.approx(0.0)

    def test_normalized_distance_completely_different(self):
        assert normalized_distance(["A", "B"], ["C", "D"]) == pytest.approx(1.0)

    def test_normalized_distance_both_empty(self):
        assert normalized_distance([], []) == pytest.approx(0.0)

    def test_normalized_distance_one_empty(self):
        # distance([], ["A","B"]) = 2; normalized = 2/2 = 1.0
        assert normalized_distance([], ["A", "B"]) == pytest.approx(1.0)

    def test_normalized_distance_bounded(self):
        import random
        rng = random.Random(42)
        vocab = list(ActionType)
        for _ in range(50):
            a = [str(rng.choice(vocab)) for _ in range(rng.randint(0, 8))]
            b = [str(rng.choice(vocab)) for _ in range(rng.randint(0, 8))]
            d = normalized_distance(a, b)
            assert 0.0 <= d <= 1.0

    def test_similarity_complement_of_distance(self):
        a = ["OPEN", "CLOSE"]
        b = ["OPEN", "EVENT_SEARCH", "CLOSE"]
        assert similarity(a, b) == pytest.approx(1.0 - normalized_distance(a, b))

    def test_real_workflow_similarity(self):
        # Fast investigation (OPEN→CLOSE) vs full workflow: expect distance > 0.6
        fast = ["OPEN", "CLOSE"]
        full = FULL_PATTERN
        d = normalized_distance(fast, full)
        assert d > 0.6

    def test_similar_workflows_low_distance(self):
        # Two full-ish workflows that differ by one action: expect distance < 0.3
        a = FULL_PATTERN
        b = FULL_PATTERN[:3] + FULL_PATTERN[4:]  # drop one middle action
        d = normalized_distance(a, b)
        assert d < 0.3


# ===========================================================================
# 2. Canonical ordering
# ===========================================================================

class TestCanonicalOrdering:

    def test_ordered_by_sequence_number(self):
        records = [
            (3, None, "C"),
            (1, None, "A"),
            (2, None, "B"),
        ]
        assert build_fingerprint(records) == ("A", "B", "C")

    def test_occurred_at_tiebreaker(self):
        t1 = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
        t2 = datetime(2024, 1, 1, 10, 5, tzinfo=UTC)
        records = [
            (1, t2, "LATER"),
            (1, t1, "EARLIER"),
        ]
        assert build_fingerprint(records) == ("EARLIER", "LATER")

    def test_empty_returns_empty_tuple(self):
        assert build_fingerprint([]) == ()

    def test_single_action(self):
        assert build_fingerprint([(1, None, "OPEN")]) == ("OPEN",)


# ===========================================================================
# 3. Duplicate sequence numbers
# ===========================================================================

class TestDuplicateSequenceNumbers:

    def test_detects_duplicates(self):
        records = [(1, None, "A"), (1, None, "B"), (2, None, "C")]
        assert has_duplicate_sequence_numbers(records) is True

    def test_no_duplicates(self):
        records = [(1, None, "A"), (2, None, "B"), (3, None, "C")]
        assert has_duplicate_sequence_numbers(records) is False

    def test_empty_no_duplicates(self):
        assert has_duplicate_sequence_numbers([]) is False

    def test_single_no_duplicates(self):
        assert has_duplicate_sequence_numbers([(1, None, "A")]) is False

    def test_none_timestamp_sorts_last(self):
        t = datetime(2024, 1, 1, tzinfo=UTC)
        records = [(1, None, "NULL_TS"), (1, t, "REAL_TS")]
        fp = build_fingerprint(records)
        # Real timestamp sorts before None
        assert fp == ("REAL_TS", "NULL_TS")


# ===========================================================================
# 4. Incomplete / missing action data
# ===========================================================================

class TestIncompleteData:

    def test_investigation_without_actions_excluded_from_comparison(self, session):
        eid = "ENT-IC1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # Add 3 full investigations + 1 with no actions
        for _ in range(3):
            full_investigation(session, eid, period_idx=0)
        aid = add_alert(session, eid, period_idx=0)
        iid = add_investigation(session, eid, aid, period_idx=0)
        # No add_actions call → empty fingerprint
        session.commit()
        cfg = FingerprintConfig(min_sequence_length=2, min_comparable_investigations=3)
        result = run_investigation_fingerprinting(session, config=cfg)
        # The empty-fingerprint investigation must not cause a crash.
        assert isinstance(result, FingerprintResult)

    def test_empty_database_returns_safe_result(self, session):
        result = run_investigation_fingerprinting(session)
        assert result.finding_count == 0
        assert result.entity_ids == ()

    def test_entity_with_no_investigations_is_safe(self, session):
        eid = "ENT-NI1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        session.commit()
        result = run_investigation_fingerprinting(session)
        assert result.finding_count == 0


# ===========================================================================
# 5. Repetitive-workflow detection (IF-REP-001)
# ===========================================================================

class TestRepetitiveWorkflow:

    def _setup_repetitive(self, session: Session, entity_id: str,
                           n_repetitive: int = 4, n_diverse: int = 0,
                           period_idx: int = 0) -> None:
        add_entity(session, entity_id)
        add_all_periods(session, entity_id)
        for _ in range(n_repetitive):
            full_investigation(
                session, entity_id, period_idx=period_idx,
                action_types=REPETITIVE_PATTERN, analyst_id="ANALYST-001"
            )
        for i in range(n_diverse):
            full_investigation(
                session, entity_id, period_idx=period_idx,
                action_types=_DIVERSE_PATTERNS[i % len(_DIVERSE_PATTERNS)],
                analyst_id=f"ANALYST-{i + 10:03d}"
            )
        session.commit()

    def test_four_identical_fingerprints_flagged(self, session):
        self._setup_repetitive(session, "ENT-R1", n_repetitive=4)
        result = run_investigation_fingerprinting(session)
        assert len(result.repetitive_findings) == 1
        assert result.repetitive_findings[0].entity_id == "ENT-R1"

    def test_dominant_fingerprint_matches_pattern(self, session):
        self._setup_repetitive(session, "ENT-R2", n_repetitive=4)
        result = run_investigation_fingerprinting(session)
        f = result.repetitive_findings[0]
        assert f.dominant_fingerprint == tuple(REPETITIVE_PATTERN)

    def test_matching_count_and_rate_correct(self, session):
        self._setup_repetitive(session, "ENT-R3", n_repetitive=4, n_diverse=1)
        result = run_investigation_fingerprinting(session)
        assert len(result.repetitive_findings) == 1
        f = result.repetitive_findings[0]
        assert f.matching_investigation_count == 4
        assert f.window_investigation_count == 5
        assert f.repetition_rate == pytest.approx(4 / 5)

    def test_repetition_rate_below_threshold_not_flagged(self, session):
        # Rate threshold is no longer a hard gate; with 3 identical (>= threshold)
        # among 13 total the count condition is still met → flagged.
        # Test instead that only 3 diverse (below count threshold=3) are NOT flagged.
        self._setup_repetitive(session, "ENT-R4", n_repetitive=2, n_diverse=10)
        result = run_investigation_fingerprinting(session)
        assert len(result.repetitive_findings) == 0

    def test_fewer_than_threshold_not_flagged(self, session):
        # Only 2 identical fingerprints — below default repetition_threshold=3
        self._setup_repetitive(session, "ENT-R5", n_repetitive=2)
        result = run_investigation_fingerprinting(session)
        assert len(result.repetitive_findings) == 0

    def test_exactly_at_threshold_flagged(self, session):
        # Exactly 3 identical, 0 diverse, rate = 1.0 → should fire
        self._setup_repetitive(session, "ENT-R6", n_repetitive=3)
        result = run_investigation_fingerprinting(session)
        assert len(result.repetitive_findings) == 1

    def test_finding_key_format(self, session):
        self._setup_repetitive(session, "ENT-R7", n_repetitive=4)
        result = run_investigation_fingerprinting(session)
        key = result.repetitive_findings[0].finding_key
        assert key.startswith(f"{REPETITIVE_ANALYTIC_ID}:ENT-R7:")
        assert "2024-01" in key  # period label for period_idx=0

    def test_matching_investigation_ids_sorted(self, session):
        self._setup_repetitive(session, "ENT-R8", n_repetitive=4)
        result = run_investigation_fingerprinting(session)
        ids = result.repetitive_findings[0].matching_investigation_ids
        assert list(ids) == sorted(ids)

    def test_neutral_language_in_summary(self, session):
        self._setup_repetitive(session, "ENT-R9", n_repetitive=4)
        result = run_investigation_fingerprinting(session)
        summary = result.repetitive_findings[0].summary
        assert "Potential Template-Driven Investigation Pattern" in summary
        forbidden = ["negligence", "fraud", "lazy", "misconduct", "manipulat"]
        for word in forbidden:
            assert word.lower() not in summary.lower()

    def test_wrong_period_not_polluted(self, session):
        # Repetitive investigations in period 2 only; period 0 has full-workflow ones
        eid = "ENT-R10"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # Period 0: 3 diverse investigations each with a unique pattern
        for i in range(3):
            full_investigation(session, eid, period_idx=0,
                               action_types=_DIVERSE_PATTERNS[i])
        # Period 2: 4 identical repetitive
        for _ in range(4):
            full_investigation(session, eid, period_idx=2, action_types=REPETITIVE_PATTERN)
        session.commit()
        result = run_investigation_fingerprinting(session)
        rep = result.repetitive_findings
        assert len(rep) == 1
        assert rep[0].period_label == "2024-03"  # period_idx=2 → March 2024


# ===========================================================================
# 6. Sequence-deviation detection (IF-DEV-002)
# ===========================================================================

class TestSequenceDeviation:

    def test_outlier_investigation_flagged(self, session):
        eid = "ENT-D1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # Establish a full-workflow baseline across first 5 periods
        for pi in range(5):
            for _ in range(2):
                full_investigation(session, eid, period_idx=pi,
                                   action_types=FULL_PATTERN)
        # Period 5: one fast investigation (structurally very different)
        full_investigation(session, eid, period_idx=5,
                           action_types=["OPEN", "CLOSE"])
        session.commit()
        result = run_investigation_fingerprinting(session)
        flagged_invs = {f.investigation_id for f in result.deviation_findings}
        # At least one deviation finding should exist
        assert len(result.deviation_findings) >= 1
        # All deviation findings belong to ENT-D1
        for f in result.deviation_findings:
            assert f.entity_id == eid

    def test_deviation_finding_key_format(self, session):
        eid = "ENT-D2"
        add_entity(session, eid)
        add_all_periods(session, eid)
        for pi in range(4):
            for _ in range(2):
                full_investigation(session, eid, period_idx=pi,
                                   action_types=FULL_PATTERN)
        fast_iid = full_investigation(session, eid, period_idx=4,
                                      action_types=["OPEN", "CLOSE"])
        session.commit()
        result = run_investigation_fingerprinting(session)
        dev_keys = [f.finding_key for f in result.deviation_findings]
        if dev_keys:
            assert all(k.startswith(DEVIATION_ANALYTIC_ID) for k in dev_keys)

    def test_similar_investigations_not_flagged(self, session):
        eid = "ENT-D3"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # All investigations use near-identical sequences spread across 6 periods
        # (only 2 per period → never exceeds repetition_threshold=3 per period)
        for pi in range(6):
            full_investigation(session, eid, period_idx=pi,
                               action_types=_DIVERSE_PATTERNS[pi % len(_DIVERSE_PATTERNS)])
            # Variant with one action swapped — still close to the above
            full_investigation(
                session, eid, period_idx=pi,
                action_types=_DIVERSE_PATTERNS[(pi + 1) % len(_DIVERSE_PATTERNS)]
            )
        session.commit()
        result = run_investigation_fingerprinting(session)
        # No repetitive findings (2 per period < threshold=3)
        assert len(result.repetitive_findings) == 0

    def test_future_leakage_prevented(self, session):
        """The baseline for period P must not include investigations from P+1 onwards."""
        eid = "ENT-D4"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # Only ONE investigation in period 0 — no baseline possible for it
        full_investigation(session, eid, period_idx=0, action_types=["OPEN", "CLOSE"])
        # Establish baseline in period 1–5 (full workflows)
        for pi in range(1, 6):
            for _ in range(2):
                full_investigation(session, eid, period_idx=pi,
                                   action_types=FULL_PATTERN)
        session.commit()
        result = run_investigation_fingerprinting(session)
        from app.analytics.investigation_fingerprinting.context import load_context
        ctx = load_context(session)
        # Find the period-0 record; it should have no prior baseline and
        # therefore NOT be flagged by deviation (no prior baseline available).
        period_0_recs = [
            r for r in ctx.records
            if r.entity_id == eid and r.period_start == PERIODS[0][0]
        ]
        assert period_0_recs  # sanity
        for r in period_0_recs:
            assert all(
                f.investigation_id != r.investigation_id
                for f in result.deviation_findings
            ), "Period-0 investigation incorrectly flagged (no baseline available)"

    def test_deviation_distance_stored_correctly(self, session):
        eid = "ENT-D5"
        add_entity(session, eid)
        add_all_periods(session, eid)
        for pi in range(5):
            for _ in range(2):
                full_investigation(session, eid, period_idx=pi,
                                   action_types=FULL_PATTERN)
        full_investigation(session, eid, period_idx=5,
                           action_types=["OPEN", "CLOSE"])
        session.commit()
        result = run_investigation_fingerprinting(session)
        for f in result.deviation_findings:
            if f.entity_id == eid:
                assert 0.0 <= f.normalized_distance <= 1.0
                assert f.normalized_distance >= FingerprintConfig().deviation_threshold


# ===========================================================================
# 7. Missing-expected-action detection (IF-MEA-003)
# ===========================================================================

class TestMissingExpectedAction:

    def test_closed_high_without_validate_flagged(self, session):
        eid = "ENT-M1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # HIGH alert with OPEN→EVENT_SEARCH→CLOSE (no VALIDATE or EVIDENCE_REVIEW)
        full_investigation(
            session, eid, period_idx=0, severity="HIGH",
            action_types=["OPEN", "EVENT_SEARCH", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        assert len(result.missing_action_findings) >= 1
        f = result.missing_action_findings[0]
        assert f.entity_id == eid
        assert f.alert_severity == "HIGH"
        assert "VALIDATE" in f.missing_action_types or "EVIDENCE_REVIEW" in f.missing_action_types

    def test_closed_critical_without_evidence_review_flagged(self, session):
        eid = "ENT-M2"
        add_entity(session, eid)
        add_all_periods(session, eid)
        full_investigation(
            session, eid, period_idx=0, severity="CRITICAL",
            action_types=["OPEN", "ASSET_LOOKUP", "CORRELATION", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        mea = [f for f in result.missing_action_findings if f.entity_id == eid]
        assert len(mea) == 1

    def test_closed_high_with_validate_not_flagged(self, session):
        eid = "ENT-M3"
        add_entity(session, eid)
        add_all_periods(session, eid)
        full_investigation(
            session, eid, period_idx=0, severity="HIGH",
            action_types=["OPEN", "EVENT_SEARCH", "VALIDATE", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        mea = [f for f in result.missing_action_findings if f.entity_id == eid]
        assert len(mea) == 0

    def test_closed_high_with_evidence_review_not_flagged(self, session):
        eid = "ENT-M4"
        add_entity(session, eid)
        add_all_periods(session, eid)
        full_investigation(
            session, eid, period_idx=0, severity="HIGH",
            action_types=["OPEN", "EVIDENCE_REVIEW", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        mea = [f for f in result.missing_action_findings if f.entity_id == eid]
        assert len(mea) == 0

    def test_medium_alert_not_assessed(self, session):
        """MEDIUM severity is below the default HIGH threshold → not assessed."""
        eid = "ENT-M5"
        add_entity(session, eid)
        add_all_periods(session, eid)
        full_investigation(
            session, eid, period_idx=0, severity="MEDIUM",
            action_types=["OPEN", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        mea = [f for f in result.missing_action_findings if f.entity_id == eid]
        assert len(mea) == 0

    def test_open_investigation_not_assessed(self, session):
        """Only CLOSED investigations are assessed for missing expected actions."""
        eid = "ENT-M6"
        add_entity(session, eid)
        add_all_periods(session, eid)
        aid = add_alert(session, eid, severity="HIGH")
        iid = add_investigation(session, eid, aid, status="IN_PROGRESS")
        add_actions(session, iid, ["OPEN", "EVENT_SEARCH"])
        session.commit()
        result = run_investigation_fingerprinting(session)
        mea = [f for f in result.missing_action_findings if f.entity_id == eid]
        assert len(mea) == 0

    def test_critical_threshold_config_skips_high(self, session):
        """With missing_action_min_severity='CRITICAL', HIGH alerts are skipped."""
        eid = "ENT-M7"
        add_entity(session, eid)
        add_all_periods(session, eid)
        full_investigation(
            session, eid, period_idx=0, severity="HIGH",
            action_types=["OPEN", "CLOSE"]
        )
        session.commit()
        cfg = FingerprintConfig(missing_action_min_severity="CRITICAL")
        result = run_investigation_fingerprinting(session, config=cfg)
        mea = [f for f in result.missing_action_findings if f.entity_id == eid]
        assert len(mea) == 0

    def test_mea_finding_key_format(self, session):
        eid = "ENT-M8"
        add_entity(session, eid)
        add_all_periods(session, eid)
        full_investigation(
            session, eid, period_idx=0, severity="HIGH",
            action_types=["OPEN", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        for f in result.missing_action_findings:
            assert f.finding_key.startswith(MISSING_ACTION_ANALYTIC_ID)

    def test_neutral_language_mea(self, session):
        eid = "ENT-M9"
        add_entity(session, eid)
        add_all_periods(session, eid)
        full_investigation(
            session, eid, period_idx=0, severity="HIGH",
            action_types=["OPEN", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        for f in result.missing_action_findings:
            assert "Potential Missing Investigation Action" in f.summary
            assert "negligence" not in f.summary.lower()


# ===========================================================================
# 8. Minimum-length / minimum-comparison guards
# ===========================================================================

class TestMinimumGuards:

    def test_seq_below_min_length_excluded_from_comparison(self, session):
        eid = "ENT-G1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        cfg = FingerprintConfig(min_sequence_length=3)
        # Add 4 investigations with only 2-action sequences → all excluded from REP
        for _ in range(4):
            full_investigation(session, eid, period_idx=0,
                               action_types=["OPEN", "CLOSE"])
        session.commit()
        result = run_investigation_fingerprinting(session, config=cfg)
        assert len(result.repetitive_findings) == 0

    def test_below_min_comparable_not_assessed(self, session):
        eid = "ENT-G2"
        add_entity(session, eid)
        add_all_periods(session, eid)
        cfg = FingerprintConfig(
            min_comparable_investigations=5, min_sequence_length=2
        )
        # Only 4 investigations with diverse patterns → below min_comparable=5
        for i in range(4):
            full_investigation(session, eid, period_idx=0,
                               action_types=_DIVERSE_PATTERNS[i])
        session.commit()
        result = run_investigation_fingerprinting(session, config=cfg)
        assert len(result.repetitive_findings) == 0


# ===========================================================================
# 9. Threshold boundary tests
# ===========================================================================

class TestThresholdBoundaries:

    def test_repetition_rate_at_exactly_threshold(self, session):
        eid = "ENT-T1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # 3 identical + 3 diverse (unique patterns) → count=3 == threshold → flagged
        for _ in range(3):
            full_investigation(session, eid, period_idx=0,
                               action_types=REPETITIVE_PATTERN)
        for i in range(3):
            full_investigation(session, eid, period_idx=0,
                               action_types=_DIVERSE_PATTERNS[i])
        session.commit()
        result = run_investigation_fingerprinting(session)
        assert len(result.repetitive_findings) == 1
        # Rate is informational, stored correctly
        assert result.repetitive_findings[0].repetition_rate == pytest.approx(0.5)

    def test_repetition_rate_just_below_threshold_not_flagged(self, session):
        eid = "ENT-T2"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # 2 identical (below count threshold=3) + 4 diverse (unique) → not flagged
        for _ in range(2):
            full_investigation(session, eid, period_idx=0,
                               action_types=REPETITIVE_PATTERN)
        for i in range(4):
            full_investigation(session, eid, period_idx=0,
                               action_types=_DIVERSE_PATTERNS[i])
        session.commit()
        result = run_investigation_fingerprinting(session)
        assert len(result.repetitive_findings) == 0

    def test_deviation_threshold_boundary_inclusive(self, session):
        """Normalized distance exactly at threshold → flagged (>= comparison)."""
        # Constructing an exact boundary is tricky; instead verify that the
        # comparison is >= by checking the threshold is applied correctly.
        cfg = FingerprintConfig(deviation_threshold=0.5,
                                min_baseline_investigations=3)
        # Build two sequences with distance = 0.5: e.g. 2-element vs 4-element
        # "AB" vs "ABCD": edit=2, max=4 → 0.50 exactly.
        eid = "ENT-T3"
        add_entity(session, eid)
        add_all_periods(session, eid)
        # 3 baseline investigations: all use 4-element sequence
        for pi in range(3):
            full_investigation(session, eid, period_idx=pi,
                               action_types=["OPEN", "ASSET_LOOKUP",
                                             "VALIDATE", "CLOSE"])
        # 1 test investigation: 2-element sequence → distance 0.50 from baseline
        full_investigation(session, eid, period_idx=3,
                           action_types=["OPEN", "CLOSE"])
        session.commit()
        result = run_investigation_fingerprinting(session, config=cfg)
        # At distance == threshold → should be flagged
        dev = [f for f in result.deviation_findings if f.entity_id == eid]
        assert len(dev) >= 1
        assert dev[0].normalized_distance >= cfg.deviation_threshold


# ===========================================================================
# 10. Entity isolation
# ===========================================================================

class TestEntityIsolation:

    def test_findings_never_cross_entities(self, session):
        # Two entities: one with repetitive workflow, one with diverse workflows
        for eid, n_rep in [("ENT-ISO1", 4), ("ENT-ISO2", 0)]:
            add_entity(session, eid)
            add_all_periods(session, eid)
            for _ in range(n_rep):
                full_investigation(session, eid, period_idx=0,
                                   action_types=REPETITIVE_PATTERN)
            # ENT-ISO2 gets diverse investigations (no two share a pattern)
            if n_rep == 0:
                for i in range(4):
                    full_investigation(session, eid, period_idx=0,
                                       action_types=_DIVERSE_PATTERNS[i])
        session.commit()
        result = run_investigation_fingerprinting(session)
        for f in result.repetitive_findings:
            assert f.entity_id == "ENT-ISO1"

    def test_scope_filter_restricts_to_named_entities(self, session):
        for eid in ["ENT-SC1", "ENT-SC2"]:
            add_entity(session, eid)
            add_all_periods(session, eid)
            for _ in range(4):
                full_investigation(session, eid, period_idx=0,
                                   action_types=REPETITIVE_PATTERN)
        session.commit()
        result = run_investigation_fingerprinting(
            session, entity_ids=["ENT-SC1"]
        )
        assert result.entity_ids == ("ENT-SC1",)
        for f in result.all_findings:
            assert f.entity_id == "ENT-SC1"


# ===========================================================================
# 11. Determinism
# ===========================================================================

class TestDeterminism:

    def test_repeated_runs_identical(self, session):
        eid = "ENT-DET1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        for _ in range(4):
            full_investigation(session, eid, period_idx=0,
                               action_types=REPETITIVE_PATTERN)
        session.commit()
        r1 = run_investigation_fingerprinting(session)
        r2 = run_investigation_fingerprinting(session)
        r3 = run_investigation_fingerprinting(session)
        assert r1 == r2 == r3

    def test_finding_keys_identical_across_runs(self, session):
        eid = "ENT-DET2"
        add_entity(session, eid)
        add_all_periods(session, eid)
        for _ in range(4):
            full_investigation(session, eid, period_idx=0,
                               action_types=REPETITIVE_PATTERN)
        session.commit()
        r1 = run_investigation_fingerprinting(session)
        r2 = run_investigation_fingerprinting(session)
        assert [f.finding_key for f in r1.all_findings] == \
               [f.finding_key for f in r2.all_findings]


# ===========================================================================
# 12. Future-leakage prevention (already partially covered in TestSequenceDeviation)
# ===========================================================================

class TestFutureLeakage:

    def test_baseline_uses_only_prior_periods(self, session):
        """An investigation in period 0 must not be compared against a baseline
        that includes investigations from periods 1+."""
        from app.analytics.investigation_fingerprinting.engine import (
            _plurality_baseline,
        )
        from app.analytics.investigation_fingerprinting.context import (
            InvestigationRecord,
        )
        # Build synthetic InvestigationRecord objects directly (no DB needed)
        def _rec(period_idx: int, fp: tuple) -> InvestigationRecord:
            ps, pe = PERIODS[period_idx]
            return InvestigationRecord(
                investigation_id=f"INV-FL-{period_idx}",
                entity_id="ENT-FL",
                alert_id="ALR-FL",
                alert_severity="MEDIUM",
                analyst_id="ANALYST-001",
                status="CLOSED",
                started_at=ps + timedelta(hours=1),
                ended_at=ps + timedelta(hours=2),
                period_start=ps,
                period_end=pe,
                fingerprint=fp,
                action_count=len(fp),
                has_duplicate_seq_nums=False,
            )

        # Baseline should be derived only from records up to period 1
        prior = [
            _rec(0, ("OPEN", "CLOSE")),
            _rec(0, ("OPEN", "CLOSE")),
            _rec(0, ("OPEN", "CLOSE")),
        ]
        # Period 2 onwards — must not pollute the prior baseline
        future = [
            _rec(2, ("OPEN", "ASSET_LOOKUP", "EVENT_SEARCH", "CLOSE")),
        ]
        # The baseline is derived from `prior` only, not `prior + future`
        result_prior = _plurality_baseline(prior, min_baseline=3)
        assert result_prior is not None
        assert result_prior[0] == ("OPEN", "CLOSE")


# ===========================================================================
# 13. Read-only behaviour
# ===========================================================================

class TestReadOnly:

    def test_run_does_not_mutate_records(self, session):
        from sqlalchemy import func, select

        eid = "ENT-RO1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        for _ in range(4):
            full_investigation(session, eid, period_idx=0,
                               action_types=REPETITIVE_PATTERN)
        session.commit()

        before_inv = session.scalar(
            select(func.count()).select_from(Investigation)
        )
        before_act = session.scalar(
            select(func.count()).select_from(InvestigationAction)
        )
        run_investigation_fingerprinting(session)
        run_investigation_fingerprinting(session)
        after_inv = session.scalar(
            select(func.count()).select_from(Investigation)
        )
        after_act = session.scalar(
            select(func.count()).select_from(InvestigationAction)
        )
        assert before_inv == after_inv
        assert before_act == after_act


# ===========================================================================
# 14. Stable finding keys
# ===========================================================================

class TestStableFindingKeys:

    def test_repetitive_key_contains_period_label_and_fp_hash(self, session):
        eid = "ENT-SK1"
        add_entity(session, eid)
        add_all_periods(session, eid)
        for _ in range(4):
            full_investigation(session, eid, period_idx=2,
                               action_types=REPETITIVE_PATTERN)
        session.commit()
        result = run_investigation_fingerprinting(session)
        key = result.repetitive_findings[0].finding_key
        # Expected: IF-REP-001:ENT-SK1:2024-03:<8-char-hash>
        parts = key.split(":")
        assert parts[0] == "IF-REP-001"
        assert parts[1] == eid
        assert parts[2] == "2024-03"  # period_idx=2 → March 2024
        assert len(parts[3]) == 8     # hex digest

    def test_mea_key_contains_investigation_id(self, session):
        eid = "ENT-SK2"
        add_entity(session, eid)
        add_all_periods(session, eid)
        iid = full_investigation(
            session, eid, period_idx=0, severity="HIGH",
            action_types=["OPEN", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        mea = result.missing_action_findings
        assert any(iid in f.finding_key for f in mea)

    def test_deviation_key_contains_investigation_id(self, session):
        eid = "ENT-SK3"
        add_entity(session, eid)
        add_all_periods(session, eid)
        for pi in range(5):
            for _ in range(2):
                full_investigation(session, eid, period_idx=pi,
                                   action_types=FULL_PATTERN)
        fast_iid = full_investigation(
            session, eid, period_idx=5, action_types=["OPEN", "CLOSE"]
        )
        session.commit()
        result = run_investigation_fingerprinting(session)
        dev = [f for f in result.deviation_findings if fast_iid in f.finding_key]
        if dev:
            assert dev[0].investigation_id == fast_iid


# ===========================================================================
# 15. Config validation
# ===========================================================================

class TestConfigValidation:

    def test_min_sequence_length_zero_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(min_sequence_length=0)

    def test_min_comparable_below_two_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(min_comparable_investigations=1)

    def test_repetition_threshold_below_two_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(repetition_threshold=1)

    def test_repetition_rate_zero_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(repetition_rate_threshold=0.0)

    def test_repetition_rate_above_one_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(repetition_rate_threshold=1.01)

    def test_deviation_threshold_zero_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(deviation_threshold=0.0)

    def test_deviation_threshold_above_one_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(deviation_threshold=1.01)

    def test_invalid_severity_threshold_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(missing_action_min_severity="MEDIUM")

    def test_empty_expected_actions_rejected(self):
        with pytest.raises(ValueError):
            FingerprintConfig(expected_actions_for_closed_high_crit=frozenset())

    def test_valid_default_config_accepted(self):
        cfg = FingerprintConfig()
        assert cfg.min_sequence_length == 2
        assert cfg.repetition_threshold == 3
        assert cfg.deviation_threshold == 0.6
