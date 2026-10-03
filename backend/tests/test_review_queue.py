"""Unit tests for the Phase 10 Supervisory Review Queue.

Categories:
1.  Enum completeness and valid-transition table
2.  priority_up / priority_down helpers
3.  derive_priority — base priorities for all analytic types
4.  derive_priority — severity modifier (upward)
5.  derive_priority — confidence modifier (downward for LOW)
6.  derive_priority — evidence-absence modifier
7.  derive_priority — priority/confidence separation
8.  Low-confidence HIGH-priority case
9.  queue_id and finding_version determinism
10. build_queue_item — field derivation
11. upsert_queue_item — insert new
12. upsert_queue_item — idempotent (same version → preserve review state)
13. upsert_queue_item — version change → reset to OPEN
14. Status transitions — valid
15. Status transitions — invalid (must raise InvalidTransitionError)
16. QueueItemNotFoundError
17. Review note and reviewer_ref handling
18. Multiple findings for one entity — independent items
19. Multiple analytics for one entity-period — separate items
20. Deterministic repeated generation (same queue_ids)
21. fetch_queue_items — filtering and ordering
22. Neutral-language titles
23. finding_category mapping
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

# Importing ReviewQueueItem registers it on Base.metadata.
from app.analytics.review_queue import (
    FindingCategory,
    FindingsBundle,
    InvalidTransitionError,
    QueueItemData,
    QueueItemNotFoundError,
    QueuePriority,
    QueueSummary,
    ReviewQueueItem,
    ReviewStatus,
    VALID_TRANSITIONS,
    build_queue_item,
    build_review_queue,
    derive_priority,
    fetch_queue_items,
    finding_category,
    transition_status,
    upsert_queue_item,
)
from app.analytics.review_queue.enums import priority_down, priority_up
from app.analytics.evidence.model import EvidenceConfidence
from app.db.base import Base


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
# Helpers
# ---------------------------------------------------------------------------

def _data(
    finding_key: str = "EG-001:ENT-01:ALR-000001",
    analytic_id: str = "EG-001",
    finding_type: str = "CRITICAL_ALERT_NO_ESCALATION",
    entity_id: str = "ENT-01",
    period_label: str = "2024-01",
    alert_severity: str | None = "CRITICAL",
    confidence: EvidenceConfidence | None = EvidenceConfidence.MODERATE,
    evidence_count: int = 3,
) -> QueueItemData:
    return QueueItemData(
        finding_key=finding_key,
        analytic_id=analytic_id,
        finding_type=finding_type,
        entity_id=entity_id,
        period_label=period_label,
        alert_severity=alert_severity,
        confidence=confidence,
        evidence_count=evidence_count,
    )


# ===========================================================================
# 1. Enum completeness and valid-transition table
# ===========================================================================

class TestEnums:

    def test_review_status_values(self):
        assert set(ReviewStatus) == {
            ReviewStatus.OPEN, ReviewStatus.IN_REVIEW,
            ReviewStatus.REVIEWED, ReviewStatus.DISMISSED,
        }

    def test_queue_priority_values(self):
        assert set(QueuePriority) == {
            QueuePriority.CRITICAL, QueuePriority.HIGH,
            QueuePriority.MEDIUM, QueuePriority.LOW,
        }

    def test_finding_category_values(self):
        assert len(list(FindingCategory)) == 6

    def test_all_statuses_have_transitions_defined(self):
        for s in ReviewStatus:
            assert s in VALID_TRANSITIONS

    def test_open_can_reach_in_review(self):
        assert ReviewStatus.IN_REVIEW in VALID_TRANSITIONS[ReviewStatus.OPEN]

    def test_open_can_reach_reviewed_directly(self):
        assert ReviewStatus.REVIEWED in VALID_TRANSITIONS[ReviewStatus.OPEN]

    def test_open_can_reach_dismissed(self):
        assert ReviewStatus.DISMISSED in VALID_TRANSITIONS[ReviewStatus.OPEN]

    def test_in_review_can_reach_reviewed(self):
        assert ReviewStatus.REVIEWED in VALID_TRANSITIONS[ReviewStatus.IN_REVIEW]

    def test_in_review_can_revert_to_open(self):
        assert ReviewStatus.OPEN in VALID_TRANSITIONS[ReviewStatus.IN_REVIEW]

    def test_reviewed_can_reopen(self):
        assert ReviewStatus.OPEN in VALID_TRANSITIONS[ReviewStatus.REVIEWED]

    def test_dismissed_can_reopen(self):
        assert ReviewStatus.OPEN in VALID_TRANSITIONS[ReviewStatus.DISMISSED]


# ===========================================================================
# 2. priority_up / priority_down
# ===========================================================================

class TestPriorityHelpers:

    def test_priority_up_from_medium(self):
        assert priority_up(QueuePriority.MEDIUM) == QueuePriority.HIGH

    def test_priority_up_from_high(self):
        assert priority_up(QueuePriority.HIGH) == QueuePriority.CRITICAL

    def test_priority_up_capped_at_critical(self):
        assert priority_up(QueuePriority.CRITICAL) == QueuePriority.CRITICAL

    def test_priority_down_from_medium(self):
        assert priority_down(QueuePriority.MEDIUM) == QueuePriority.LOW

    def test_priority_down_from_high(self):
        assert priority_down(QueuePriority.HIGH) == QueuePriority.MEDIUM

    def test_priority_down_floored_at_low(self):
        assert priority_down(QueuePriority.LOW) == QueuePriority.LOW


# ===========================================================================
# 3. derive_priority — base priorities for all analytic types
# ===========================================================================

class TestBasePriorities:

    def test_critical_alert_no_escalation_base_high(self):
        p = derive_priority(
            analytic_id="EG-001",
            finding_sub_type="CRITICAL_ALERT_NO_ESCALATION",
            evidence_count=3,
        )
        assert p == QueuePriority.HIGH

    def test_acknowledged_no_investigation_base_high(self):
        p = derive_priority(
            analytic_id="EG-001",
            finding_sub_type="ACKNOWLEDGED_ALERT_NO_INVESTIGATION",
            evidence_count=3,
        )
        assert p == QueuePriority.HIGH

    def test_remediation_gap_base_medium(self):
        p = derive_priority(
            analytic_id="EG-001",
            finding_sub_type="RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION",
            evidence_count=3,
        )
        assert p == QueuePriority.MEDIUM

    def test_monitoring_gap_base_medium(self):
        p = derive_priority(analytic_id="NS-001", finding_sub_type="CRITICAL_ASSET_NO_TELEMETRY", evidence_count=2)
        assert p == QueuePriority.MEDIUM

    def test_telemetry_disappearance_base_medium(self):
        p = derive_priority(analytic_id="NS-001", finding_sub_type="TELEMETRY_CONTINUITY_GAP", evidence_count=2)
        assert p == QueuePriority.MEDIUM

    def test_anomaly_base_high(self):
        p = derive_priority(analytic_id="AN-001", finding_sub_type="", evidence_count=2)
        assert p == QueuePriority.HIGH

    def test_peer_deviation_base_medium(self):
        p = derive_priority(analytic_id="PB-001", finding_sub_type="", evidence_count=2)
        assert p == QueuePriority.MEDIUM

    def test_metric_risk_divergence_base_high(self):
        p = derive_priority(analytic_id="MRD-001", finding_sub_type="", evidence_count=5)
        assert p == QueuePriority.HIGH

    def test_repetitive_workflow_base_low(self):
        p = derive_priority(analytic_id="IF-REP-001", finding_sub_type="REPETITIVE_WORKFLOW", evidence_count=4)
        assert p == QueuePriority.LOW

    def test_sequence_deviation_base_low(self):
        p = derive_priority(analytic_id="IF-DEV-002", finding_sub_type="SEQUENCE_DEVIATION", evidence_count=1)
        assert p == QueuePriority.LOW

    def test_missing_expected_action_base_medium(self):
        p = derive_priority(analytic_id="IF-MEA-003", finding_sub_type="MISSING_EXPECTED_ACTION", evidence_count=3)
        assert p == QueuePriority.MEDIUM


# ===========================================================================
# 4. derive_priority — severity modifier
# ===========================================================================

class TestSeverityModifier:

    def test_critical_severity_raises_medium_to_high(self):
        p = derive_priority(
            analytic_id="EG-001",
            finding_sub_type="RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION",
            alert_severity="CRITICAL",
            evidence_count=3,
        )
        assert p == QueuePriority.HIGH

    def test_high_severity_raises_medium_to_high(self):
        p = derive_priority(
            analytic_id="NS-001",
            finding_sub_type="CRITICAL_ASSET_NO_TELEMETRY",
            alert_severity="HIGH",
            evidence_count=2,
        )
        assert p == QueuePriority.HIGH

    def test_high_severity_raises_low_to_medium(self):
        p = derive_priority(
            analytic_id="IF-MEA-003",
            finding_sub_type="MISSING_EXPECTED_ACTION",
            alert_severity="HIGH",
            evidence_count=3,
        )
        # base=MEDIUM, severity=HIGH → HIGH
        assert p == QueuePriority.HIGH

    def test_critical_severity_does_not_exceed_critical(self):
        # base HIGH + CRITICAL severity = CRITICAL; further up = still CRITICAL
        p = derive_priority(
            analytic_id="AN-001",
            finding_sub_type="",
            alert_severity="CRITICAL",
            evidence_count=2,
        )
        assert p == QueuePriority.CRITICAL

    def test_low_severity_no_upward_modifier(self):
        p_with = derive_priority(
            analytic_id="EG-001",
            finding_sub_type="RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION",
            alert_severity="LOW",
            evidence_count=3,
        )
        p_without = derive_priority(
            analytic_id="EG-001",
            finding_sub_type="RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION",
            alert_severity=None,
            evidence_count=3,
        )
        assert p_with == p_without


# ===========================================================================
# 5. derive_priority — confidence modifier
# ===========================================================================

class TestConfidenceModifier:

    def test_low_confidence_lowers_high_to_medium(self):
        p = derive_priority(
            analytic_id="AN-001",
            finding_sub_type="",
            evidence_confidence=EvidenceConfidence.LOW,
            evidence_count=2,
        )
        # base=HIGH, LOW confidence → MEDIUM
        assert p == QueuePriority.MEDIUM

    def test_low_confidence_lowers_medium_to_low(self):
        p = derive_priority(
            analytic_id="PB-001",
            finding_sub_type="",
            evidence_confidence=EvidenceConfidence.LOW,
            evidence_count=2,
        )
        # base=MEDIUM, LOW confidence → LOW
        assert p == QueuePriority.LOW

    def test_low_confidence_floored_at_low(self):
        p = derive_priority(
            analytic_id="IF-REP-001",
            finding_sub_type="REPETITIVE_WORKFLOW",
            evidence_confidence=EvidenceConfidence.LOW,
            evidence_count=4,
        )
        # base=LOW, LOW confidence → still LOW
        assert p == QueuePriority.LOW

    def test_moderate_confidence_no_downward_modifier(self):
        p_mod = derive_priority(
            analytic_id="AN-001",
            finding_sub_type="",
            evidence_confidence=EvidenceConfidence.MODERATE,
            evidence_count=2,
        )
        p_high_conf = derive_priority(
            analytic_id="AN-001",
            finding_sub_type="",
            evidence_confidence=EvidenceConfidence.HIGH,
            evidence_count=2,
        )
        # MODERATE should not lower priority compared to HIGH confidence
        assert p_mod == p_high_conf == QueuePriority.HIGH

    def test_none_confidence_no_modifier(self):
        p_none = derive_priority(analytic_id="AN-001", finding_sub_type="", evidence_confidence=None, evidence_count=2)
        p_high = derive_priority(analytic_id="AN-001", finding_sub_type="", evidence_confidence=EvidenceConfidence.HIGH, evidence_count=2)
        assert p_none == p_high


# ===========================================================================
# 6. derive_priority — evidence-absence modifier
# ===========================================================================

class TestEvidenceAbsenceModifier:

    def test_zero_evidence_lowers_priority(self):
        p_with = derive_priority(analytic_id="MRD-001", finding_sub_type="", evidence_count=5)
        p_without = derive_priority(analytic_id="MRD-001", finding_sub_type="", evidence_count=0)
        assert p_with == QueuePriority.HIGH
        # 0 evidence → one step down
        assert p_without == QueuePriority.MEDIUM

    def test_zero_evidence_floored_at_low(self):
        p = derive_priority(
            analytic_id="IF-REP-001",
            finding_sub_type="REPETITIVE_WORKFLOW",
            evidence_count=0,
        )
        # base=LOW, 0 evidence → still LOW
        assert p == QueuePriority.LOW


# ===========================================================================
# 7. Priority / confidence separation
# ===========================================================================

class TestPriorityConfidenceSeparation:

    def test_low_confidence_does_not_eliminate_high_priority_signal(self):
        """A LOW-confidence finding should still reach MEDIUM priority for a
        HIGH-severity analytic (not fall all the way to LOW)."""
        # EG-001 CRITICAL_ALERT_NO_ESCALATION with CRITICAL severity:
        # base=HIGH → severity → CRITICAL → LOW confidence → HIGH
        p = derive_priority(
            analytic_id="EG-001",
            finding_sub_type="CRITICAL_ALERT_NO_ESCALATION",
            alert_severity="CRITICAL",
            evidence_confidence=EvidenceConfidence.LOW,
            evidence_count=2,
        )
        # CRITICAL → LOW conf → HIGH. Still HIGH — the signal is visible.
        assert p == QueuePriority.HIGH

    def test_high_confidence_low_impact_stays_low(self):
        p = derive_priority(
            analytic_id="IF-DEV-002",
            finding_sub_type="SEQUENCE_DEVIATION",
            evidence_confidence=EvidenceConfidence.HIGH,
            evidence_count=3,
        )
        # base=LOW, HIGH confidence, no modifiers → LOW
        assert p == QueuePriority.LOW


# ===========================================================================
# 8. queue_id and finding_version determinism
# ===========================================================================

class TestIdDeterminism:

    def test_queue_id_is_12_chars(self):
        fields = build_queue_item(_data())
        assert len(fields["queue_id"]) == 12

    def test_queue_id_is_deterministic(self):
        f1 = build_queue_item(_data())
        f2 = build_queue_item(_data())
        assert f1["queue_id"] == f2["queue_id"]

    def test_different_finding_keys_give_different_queue_ids(self):
        d1 = _data(finding_key="EG-001:ENT-01:ALR-000001")
        d2 = _data(finding_key="EG-001:ENT-01:ALR-000002")
        assert build_queue_item(d1)["queue_id"] != build_queue_item(d2)["queue_id"]

    def test_finding_version_is_8_chars(self):
        fields = build_queue_item(_data())
        assert len(fields["finding_version"]) == 8

    def test_finding_version_deterministic(self):
        f1 = build_queue_item(_data())
        f2 = build_queue_item(_data())
        assert f1["finding_version"] == f2["finding_version"]

    def test_finding_version_changes_with_entity_id(self):
        d1 = _data(entity_id="ENT-01")
        d2 = _data(entity_id="ENT-02")
        assert build_queue_item(d1)["finding_version"] != build_queue_item(d2)["finding_version"]


# ===========================================================================
# 9. build_queue_item — field derivation
# ===========================================================================

class TestBuildQueueItem:

    def test_all_required_fields_present(self):
        fields = build_queue_item(_data())
        required = {
            "queue_id", "finding_key", "analytic_id", "finding_type",
            "entity_id", "period_label", "title", "category",
            "priority", "confidence", "evidence_count", "finding_version",
        }
        assert required <= set(fields)

    def test_category_is_execution_gap_for_eg(self):
        fields = build_queue_item(_data(analytic_id="EG-001"))
        assert fields["category"] == FindingCategory.EXECUTION_GAP

    def test_category_is_anomaly_for_an(self):
        fields = build_queue_item(_data(analytic_id="AN-001", finding_type="ANOMALOUS"))
        assert fields["category"] == FindingCategory.ANOMALY

    def test_title_contains_entity_id(self):
        fields = build_queue_item(_data(entity_id="ENT-42"))
        assert "ENT-42" in fields["title"]

    def test_title_is_neutral(self):
        fields = build_queue_item(_data())
        forbidden = ["negligence", "fraud", "misconduct", "blame", "manipulat"]
        for word in forbidden:
            assert word.lower() not in fields["title"].lower()

    def test_confidence_stored_as_string(self):
        fields = build_queue_item(_data(confidence=EvidenceConfidence.MODERATE))
        assert fields["confidence"] == "MODERATE"

    def test_none_confidence_stored_as_unknown(self):
        fields = build_queue_item(_data(confidence=None))
        assert fields["confidence"] == "UNKNOWN"


# ===========================================================================
# 10. upsert_queue_item — insert new
# ===========================================================================

class TestUpsertInsert:

    def test_insert_creates_open_item(self, session):
        item, created = upsert_queue_item(session, _data())
        assert created is True
        assert item.status == ReviewStatus.OPEN

    def test_insert_sets_correct_fields(self, session):
        d = _data(entity_id="ENT-01", period_label="2024-03", analytic_id="AN-001", finding_type="ANOMALOUS")
        item, _ = upsert_queue_item(session, d)
        assert item.entity_id == "ENT-01"
        assert item.period_label == "2024-03"
        assert item.analytic_id == "AN-001"

    def test_insert_priority_not_none(self, session):
        item, _ = upsert_queue_item(session, _data())
        assert item.priority in QueuePriority

    def test_insert_is_committed_queryable(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        session.flush()
        from sqlalchemy import select
        found = session.scalar(select(ReviewQueueItem).where(ReviewQueueItem.queue_id == item.queue_id))
        assert found is not None
        assert found.finding_key == d.finding_key


# ===========================================================================
# 11. upsert_queue_item — idempotent (same version → preserve review state)
# ===========================================================================

class TestUpsertIdempotent:

    def test_same_version_preserves_status(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        # Transition to REVIEWED
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED, review_note="Done.")
        # Re-upsert same data
        item2, created = upsert_queue_item(session, d)
        assert created is False
        assert item2.status == ReviewStatus.REVIEWED
        assert item2.review_note == "Done."

    def test_same_version_preserves_reviewer_ref(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.IN_REVIEW, reviewer_ref="analyst-1")
        upsert_queue_item(session, d)  # re-upsert unchanged
        refreshed = session.get(ReviewQueueItem, item.queue_id)
        assert refreshed.reviewer_ref == "analyst-1"

    def test_same_version_does_not_change_created_at(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        session.flush()
        created_at_before = item.created_at
        upsert_queue_item(session, d)
        session.flush()
        refreshed = session.get(ReviewQueueItem, item.queue_id)
        assert refreshed.created_at == created_at_before


# ===========================================================================
# 12. upsert_queue_item — version change → reset to OPEN
# ===========================================================================

class TestUpsertVersionChange:

    def test_changed_entity_id_resets_to_open(self, session):
        d1 = _data(finding_key="EG-001:ENT-01", entity_id="ENT-01")
        item, _ = upsert_queue_item(session, d1)
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED)
        # Same finding_key → same queue_id, but different entity_id → different version
        d2 = _data(finding_key="EG-001:ENT-01", entity_id="ENT-02")
        item2, created = upsert_queue_item(session, d2)
        assert created is False
        assert item2.status == ReviewStatus.OPEN
        assert item2.reviewer_ref is None
        assert item2.review_note is None

    def test_changed_version_updates_title(self, session):
        d1 = _data(finding_key="EG-001:ENT-01", entity_id="ENT-01", analytic_id="EG-001", finding_type="CRITICAL_ALERT_NO_ESCALATION")
        item, _ = upsert_queue_item(session, d1)
        old_title = item.title
        d2 = _data(finding_key="EG-001:ENT-01", entity_id="ENT-99", analytic_id="EG-001", finding_type="CRITICAL_ALERT_NO_ESCALATION")
        item2, _ = upsert_queue_item(session, d2)
        assert item2.title != old_title


# ===========================================================================
# 13. Status transitions — valid
# ===========================================================================

class TestValidTransitions:

    def test_open_to_in_review(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        t = transition_status(session, item.queue_id, ReviewStatus.IN_REVIEW)
        assert t.status == ReviewStatus.IN_REVIEW

    def test_in_review_to_reviewed(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.IN_REVIEW)
        t = transition_status(session, item.queue_id, ReviewStatus.REVIEWED)
        assert t.status == ReviewStatus.REVIEWED
        assert t.reviewed_at is not None

    def test_open_to_reviewed_shortcut(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        t = transition_status(session, item.queue_id, ReviewStatus.REVIEWED)
        assert t.status == ReviewStatus.REVIEWED

    def test_open_to_dismissed(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        t = transition_status(session, item.queue_id, ReviewStatus.DISMISSED)
        assert t.status == ReviewStatus.DISMISSED
        assert t.reviewed_at is not None

    def test_reviewed_to_open_reopen(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED)
        t = transition_status(session, item.queue_id, ReviewStatus.OPEN)
        assert t.status == ReviewStatus.OPEN

    def test_in_review_to_open_revert(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.IN_REVIEW)
        t = transition_status(session, item.queue_id, ReviewStatus.OPEN)
        assert t.status == ReviewStatus.OPEN

    def test_dismissed_to_open(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.DISMISSED)
        t = transition_status(session, item.queue_id, ReviewStatus.OPEN)
        assert t.status == ReviewStatus.OPEN


# ===========================================================================
# 14. Status transitions — invalid
# ===========================================================================

class TestInvalidTransitions:

    def test_open_to_open_invalid(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        with pytest.raises(InvalidTransitionError):
            transition_status(session, item.queue_id, ReviewStatus.OPEN)

    def test_reviewed_to_reviewed_invalid(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED)
        with pytest.raises(InvalidTransitionError):
            transition_status(session, item.queue_id, ReviewStatus.REVIEWED)

    def test_reviewed_to_in_review_invalid(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED)
        with pytest.raises(InvalidTransitionError):
            transition_status(session, item.queue_id, ReviewStatus.IN_REVIEW)

    def test_dismissed_to_reviewed_invalid(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.DISMISSED)
        with pytest.raises(InvalidTransitionError):
            transition_status(session, item.queue_id, ReviewStatus.REVIEWED)

    def test_invalid_transition_error_message(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        with pytest.raises(InvalidTransitionError) as exc:
            transition_status(session, item.queue_id, ReviewStatus.OPEN)
        assert "OPEN" in str(exc.value)


# ===========================================================================
# 15. QueueItemNotFoundError
# ===========================================================================

class TestNotFoundError:

    def test_transition_nonexistent_raises(self, session):
        with pytest.raises(QueueItemNotFoundError):
            transition_status(session, "nonexistent_id", ReviewStatus.IN_REVIEW)

    def test_error_contains_queue_id(self, session):
        with pytest.raises(QueueItemNotFoundError) as exc:
            transition_status(session, "bad_id_xyz", ReviewStatus.REVIEWED)
        assert "bad_id_xyz" in str(exc.value)


# ===========================================================================
# 16. Review note and reviewer_ref handling
# ===========================================================================

class TestReviewMetadata:

    def test_review_note_stored_on_reviewed(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED, review_note="Pattern matches known false positive.")
        refreshed = session.get(ReviewQueueItem, item.queue_id)
        assert refreshed.review_note == "Pattern matches known false positive."

    def test_reviewer_ref_stored_on_in_review(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.IN_REVIEW, reviewer_ref="supervisor-team-b")
        refreshed = session.get(ReviewQueueItem, item.queue_id)
        assert refreshed.reviewer_ref == "supervisor-team-b"

    def test_review_note_not_overwritten_by_status_without_note(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED, review_note="Important note.")
        transition_status(session, item.queue_id, ReviewStatus.OPEN)  # reopen
        refreshed = session.get(ReviewQueueItem, item.queue_id)
        # Note is preserved; only status changes.
        assert refreshed.review_note == "Important note."


# ===========================================================================
# 17. Multiple findings for one entity — independent items
# ===========================================================================

class TestMultipleFindings:

    def test_two_findings_same_entity_different_keys(self, session):
        d1 = _data(finding_key="EG-001:ENT-01:ALR-000001")
        d2 = _data(finding_key="EG-001:ENT-01:ALR-000002")
        item1, _ = upsert_queue_item(session, d1)
        item2, _ = upsert_queue_item(session, d2)
        assert item1.queue_id != item2.queue_id

    def test_two_analytics_one_entity_period(self, session):
        d_eg = _data(finding_key="EG-001:ENT-01:2024-01", analytic_id="EG-001", finding_type="CRITICAL_ALERT_NO_ESCALATION")
        d_an = _data(finding_key="AN-001:ENT-01:2024-01", analytic_id="AN-001", finding_type="ANOMALOUS", alert_severity=None)
        item_eg, _ = upsert_queue_item(session, d_eg)
        item_an, _ = upsert_queue_item(session, d_an)
        assert item_eg.queue_id != item_an.queue_id
        assert item_eg.analytic_id == "EG-001"
        assert item_an.analytic_id == "AN-001"


# ===========================================================================
# 18. Deterministic repeated generation
# ===========================================================================

class TestRepeatedGeneration:

    def test_same_input_same_queue_ids(self, session):
        items_data = [
            _data(finding_key=f"EG-001:ENT-01:ALR-{i:06d}") for i in range(5)
        ]
        ids_first = set()
        for d in items_data:
            item, _ = upsert_queue_item(session, d)
            ids_first.add(item.queue_id)
        session.flush()
        ids_second = set()
        for d in items_data:
            item, _ = upsert_queue_item(session, d)
            ids_second.add(item.queue_id)
        assert ids_first == ids_second

    def test_review_state_preserved_across_runs(self, session):
        d = _data()
        item, _ = upsert_queue_item(session, d)
        transition_status(session, item.queue_id, ReviewStatus.REVIEWED, review_note="Confirmed.")
        session.flush()
        # Second run with identical data
        item2, created = upsert_queue_item(session, d)
        assert not created
        assert item2.status == ReviewStatus.REVIEWED
        assert item2.review_note == "Confirmed."


# ===========================================================================
# 19. fetch_queue_items — filtering and ordering
# ===========================================================================

class TestFetchQueueItems:

    def _populate(self, session):
        items = [
            _data("k1", "EG-001", "CRITICAL_ALERT_NO_ESCALATION", "ENT-01", "2024-01", "CRITICAL", EvidenceConfidence.HIGH, 3),
            _data("k2", "AN-001", "ANOMALOUS", "ENT-01", "2024-01", None, EvidenceConfidence.MODERATE, 2),
            _data("k3", "PB-001", "PEER_DEVIATION", "ENT-02", "2024-01", None, EvidenceConfidence.LOW, 1),
            _data("k4", "NS-001", "CRITICAL_ASSET_NO_TELEMETRY", "ENT-02", "2024-02", None, EvidenceConfidence.MODERATE, 2),
        ]
        inserted = []
        for d in items:
            item, _ = upsert_queue_item(session, d)
            inserted.append(item)
        session.flush()
        return inserted

    def test_fetch_all_returns_all(self, session):
        self._populate(session)
        rows = fetch_queue_items(session)
        assert len(rows) == 4

    def test_fetch_by_entity_id(self, session):
        self._populate(session)
        rows = fetch_queue_items(session, entity_id="ENT-01")
        assert all(r.entity_id == "ENT-01" for r in rows)
        assert len(rows) == 2

    def test_fetch_by_status_open(self, session):
        inserted = self._populate(session)
        # Review one item
        transition_status(session, inserted[0].queue_id, ReviewStatus.REVIEWED)
        rows = fetch_queue_items(session, status=ReviewStatus.OPEN)
        assert all(r.status == ReviewStatus.OPEN for r in rows)
        assert len(rows) == 3

    def test_fetch_priority_critical_first(self, session):
        self._populate(session)
        rows = fetch_queue_items(session)
        # CRITICAL (from EG+CRITICAL severity) should appear first or early
        priorities = [r.priority for r in rows]
        # At least first item should be CRITICAL or HIGH
        assert priorities[0] in (QueuePriority.CRITICAL, QueuePriority.HIGH)

    def test_fetch_empty_db_returns_empty(self, session):
        rows = fetch_queue_items(session)
        assert rows == []


# ===========================================================================
# 20. Neutral-language titles
# ===========================================================================

class TestNeutralTitles:

    def test_all_analytic_types_produce_neutral_titles(self):
        types = [
            ("EG-001", "CRITICAL_ALERT_NO_ESCALATION"),
            ("EG-001", "ACKNOWLEDGED_ALERT_NO_INVESTIGATION"),
            ("EG-001", "RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION"),
            ("NS-001", "CRITICAL_ASSET_NO_TELEMETRY"),
            ("NS-001", "TELEMETRY_CONTINUITY_GAP"),
            ("AN-001", "ANOMALOUS"),
            ("PB-001", "PEER_DEVIATION"),
            ("MRD-001", "METRIC_RISK_DIVERGENCE"),
            ("IF-REP-001", "REPETITIVE_WORKFLOW"),
            ("IF-DEV-002", "SEQUENCE_DEVIATION"),
            ("IF-MEA-003", "MISSING_EXPECTED_ACTION"),
        ]
        forbidden = ["negligence", "fraud", "misconduct", "blame", "manipulat", "guilty"]
        for analytic_id, finding_type in types:
            d = _data(analytic_id=analytic_id, finding_type=finding_type)
            fields = build_queue_item(d)
            title = fields["title"]
            assert title, f"Empty title for {analytic_id}/{finding_type}"
            assert "Potential" in title, f"Title does not start with 'Potential': {title!r}"
            for word in forbidden:
                assert word.lower() not in title.lower(), (
                    f"Forbidden word {word!r} in title {title!r}"
                )


# ===========================================================================
# 21. finding_category mapping
# ===========================================================================

class TestFindingCategoryMapping:

    def test_execution_gap(self):
        assert finding_category("EG-001") == FindingCategory.EXECUTION_GAP

    def test_negative_space(self):
        assert finding_category("NS-001") == FindingCategory.MONITORING_GAP

    def test_anomaly(self):
        assert finding_category("AN-001") == FindingCategory.ANOMALY

    def test_peer_benchmark(self):
        assert finding_category("PB-001") == FindingCategory.PEER_DEVIATION

    def test_metric_risk_divergence(self):
        assert finding_category("MRD-001") == FindingCategory.METRIC_RISK_DIVERGENCE

    def test_fingerprint_all_subtypes(self):
        assert finding_category("IF-REP-001") == FindingCategory.WORKFLOW_PATTERN
        assert finding_category("IF-DEV-002") == FindingCategory.WORKFLOW_PATTERN
        assert finding_category("IF-MEA-003") == FindingCategory.WORKFLOW_PATTERN
