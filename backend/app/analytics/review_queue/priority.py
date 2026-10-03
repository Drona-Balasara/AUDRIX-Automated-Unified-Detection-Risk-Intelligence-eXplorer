"""Deterministic, rule-based priority derivation for review queue items.

Priority is distinct from confidence (evidence sufficiency) and from
risk/severity.  NIST SP 800-55 Vol. 1 (December 2024) recommends prioritizing
measures based on their impact on the organization's risk posture; NIST SP
800-61 Rev. 3 (April 2025) emphasizes effective detection and response over
any period.  Together these justify a rule-based priority that accounts for
the type of analytical signal, the severity of the involved alerts (where
known), and data quality — without using ML or ground truth.

Priority derivation algorithm
------------------------------
Step 1: Assign a BASE priority from the finding category/type.

    CRITICAL alerts without escalation (EG)      → HIGH
    Acknowledged alerts without investigation (EG) → HIGH
    Recurring alerts without remediation (EG)    → MEDIUM
    Monitoring gap / telemetry disappearance (NS) → MEDIUM
    Anomaly detection (AN-001)                   → HIGH
    Peer deviation (PB-001)                      → MEDIUM
    Metric-risk divergence (MRD-001)             → HIGH
    Repetitive workflow (IF-REP-001)             → LOW
    Sequence deviation (IF-DEV-002)              → LOW
    Missing expected action (IF-MEA-003)         → MEDIUM

Step 2: Apply severity modifier (where alert_severity is known).
    If alert_severity in {HIGH, CRITICAL} and current priority < CRITICAL:
        priority_up by 1 step.

Step 3: Apply confidence modifier.
    If evidence_confidence == LOW:
        priority_down by 1 step.
    (A LOW-confidence HIGH-priority finding keeps its high priority signalling
    significance — the confidence note explains the data-quality limitation.
    This is the intended behaviour: supervisors see the signal and the caveat.)

Step 4: Apply evidence-absence modifier.
    If evidence_count == 0: priority_down by 1 step.

No step raises priority above CRITICAL or lowers it below LOW.

All modifiers are applied in order and are non-cumulative per step (i.e. two
severity modifiers do not stack — there is at most one severity modifier per
finding).  The logic is deterministic and reproducible.

Ground truth is never used here; this module contains no references to
``ScenarioType`` or ``DetectionCategory``.
"""

from __future__ import annotations

from app.analytics.review_queue.enums import (
    FindingCategory,
    QueuePriority,
    priority_down,
    priority_up,
)
from app.analytics.evidence.model import EvidenceConfidence

# ---------------------------------------------------------------------------
# Base-priority table (analytic_id + finding sub-type → base priority)
# ---------------------------------------------------------------------------

# Keys: (analytic_id, reason_code_or_finding_type)
# When no sub-type is available, just (analytic_id, "") is used.
_BASE_PRIORITY: dict[tuple[str, str], QueuePriority] = {
    # Execution gap (Phase 4)
    ("EG-001", "CRITICAL_ALERT_NO_ESCALATION"):         QueuePriority.HIGH,
    ("EG-001", "ACKNOWLEDGED_ALERT_NO_INVESTIGATION"):  QueuePriority.HIGH,
    ("EG-001", "RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION"): QueuePriority.MEDIUM,
    # Negative space (Phase 5)
    ("NS-001", "CRITICAL_ASSET_NO_TELEMETRY"):          QueuePriority.MEDIUM,
    ("NS-001", "TELEMETRY_CONTINUITY_GAP"):              QueuePriority.MEDIUM,
    # Anomaly detection (Phase 6)
    ("AN-001", ""):                                     QueuePriority.HIGH,
    # Peer benchmarking (Phase 6)
    ("PB-001", ""):                                     QueuePriority.MEDIUM,
    # Metric-risk divergence (Phase 7)
    ("MRD-001", ""):                                    QueuePriority.HIGH,
    # Investigation fingerprinting (Phase 8)
    ("IF-REP-001", "REPETITIVE_WORKFLOW"):              QueuePriority.LOW,
    ("IF-DEV-002", "SEQUENCE_DEVIATION"):               QueuePriority.LOW,
    ("IF-MEA-003", "MISSING_EXPECTED_ACTION"):          QueuePriority.MEDIUM,
}

# Fallback when neither (analytic_id, sub_type) nor (analytic_id, "") is found.
_DEFAULT_BASE_PRIORITY = QueuePriority.MEDIUM

# Severity values that trigger the upward modifier.
_HIGH_SEVERITY_VALUES: frozenset[str] = frozenset({"HIGH", "CRITICAL"})


def derive_priority(
    *,
    analytic_id: str,
    finding_sub_type: str = "",
    alert_severity: str | None = None,
    evidence_confidence: EvidenceConfidence | str | None = None,
    evidence_count: int = 0,
) -> QueuePriority:
    """Compute the deterministic review priority for one finding.

    Parameters
    ----------
    analytic_id:
        The stable analytic identifier, e.g. ``"EG-001"``, ``"AN-001"``.
    finding_sub_type:
        The finding's reason code or finding-type value, e.g.
        ``"CRITICAL_ALERT_NO_ESCALATION"``.  Empty string when not applicable.
    alert_severity:
        The alert severity string (``"LOW"`` / ``"MEDIUM"`` / ``"HIGH"`` /
        ``"CRITICAL"``) when the finding is directly linked to an alert.
        ``None`` when not applicable.
    evidence_confidence:
        The ``EvidenceConfidence`` value (``"HIGH"`` / ``"MODERATE"`` /
        ``"LOW"``) from the Phase 9 evidence layer.  ``None`` when not yet
        computed (treated as unknown; no modifier applied).
    evidence_count:
        Number of evidence references associated with this finding.  0 means
        no direct evidence was available (triggers a downward modifier).
    """
    # Step 1: base priority from table.
    key_specific = (analytic_id, finding_sub_type)
    key_generic  = (analytic_id, "")
    priority = _BASE_PRIORITY.get(key_specific) or _BASE_PRIORITY.get(key_generic) or _DEFAULT_BASE_PRIORITY

    # Step 2: severity modifier (upward).
    if alert_severity and str(alert_severity).upper() in _HIGH_SEVERITY_VALUES:
        priority = priority_up(priority)

    # Step 3: confidence modifier (downward only when LOW).
    conf_str = str(evidence_confidence) if evidence_confidence is not None else None
    if conf_str == EvidenceConfidence.LOW:
        priority = priority_down(priority)

    # Step 4: evidence-absence modifier.
    if evidence_count == 0:
        priority = priority_down(priority)

    return priority


def finding_category(analytic_id: str) -> FindingCategory:
    """Map an analytic_id to its broad ``FindingCategory``."""
    _map: dict[str, FindingCategory] = {
        "EG-001":  FindingCategory.EXECUTION_GAP,
        "NS-001":  FindingCategory.MONITORING_GAP,
        "AN-001":  FindingCategory.ANOMALY,
        "PB-001":  FindingCategory.PEER_DEVIATION,
        "MRD-001": FindingCategory.METRIC_RISK_DIVERGENCE,
        "IF-REP-001": FindingCategory.WORKFLOW_PATTERN,
        "IF-DEV-002": FindingCategory.WORKFLOW_PATTERN,
        "IF-MEA-003": FindingCategory.WORKFLOW_PATTERN,
    }
    return _map.get(analytic_id, FindingCategory.EXECUTION_GAP)
