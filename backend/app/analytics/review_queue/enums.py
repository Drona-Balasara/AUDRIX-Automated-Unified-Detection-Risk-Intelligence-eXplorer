"""Controlled vocabularies for the Supervisory Review Queue (Phase 10).

All enums derive from ``StrEnum`` so they serialize cleanly to JSON/CSV and
compare equal to their stored string form, consistent with the rest of the
SAT-SA domain model.
"""

from __future__ import annotations

from app.models.enums import StrEnum


class ReviewStatus(StrEnum):
    """Lifecycle status of a supervisory review queue item.

    State-transition rules are enforced in :mod:`app.analytics.review_queue.queue`.

    Allowed transitions:
        OPEN      → IN_REVIEW   (supervisor begins review)
        OPEN      → REVIEWED    (shortcut: direct close without explicit in-review step)
        OPEN      → DISMISSED   (supervisor dismisses without full review)
        IN_REVIEW → REVIEWED    (supervisor completes review)
        IN_REVIEW → OPEN        (supervisor hands back for later)
        REVIEWED  → OPEN        (reopen if new evidence warrants re-examination)
        DISMISSED → OPEN        (undismiss)

    REVIEWED and DISMISSED are terminal statuses in the normal workflow, but
    can be reopened to preserve human oversight.
    """

    OPEN      = "OPEN"
    IN_REVIEW = "IN_REVIEW"
    REVIEWED  = "REVIEWED"
    DISMISSED = "DISMISSED"


class QueuePriority(StrEnum):
    """Relative attention a finding deserves for supervisory review.

    Priority is intentionally separate from ``EvidenceConfidence``.
    A LOW-confidence finding may still deserve HIGH priority (e.g. a
    potential escalation gap on a CRITICAL alert, where the evidence is
    thin but the operational risk is significant).

    Priority levels
    ---------------
    CRITICAL  Immediate supervisory attention warranted.
    HIGH      Should be reviewed in the current assessment cycle.
    MEDIUM    Standard queue item; review at normal cadence.
    LOW       Background signal; review when capacity allows.

    See :mod:`app.analytics.review_queue.priority` for the deterministic
    derivation rules.  Priority is never derived from ML.
    """

    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


class FindingCategory(StrEnum):
    """Coarse analytical category of the underlying finding.

    Maps each Phase 4–8 finding type to a category so the queue can be
    filtered and sorted without parsing analytic-specific finding keys.
    These values are intentionally broader than the fine-grained GapType /
    FingerprintFindingType enums in the individual analytics packages.
    """

    EXECUTION_GAP          = "EXECUTION_GAP"
    MONITORING_GAP         = "MONITORING_GAP"
    ANOMALY                = "ANOMALY"
    PEER_DEVIATION         = "PEER_DEVIATION"
    METRIC_RISK_DIVERGENCE = "METRIC_RISK_DIVERGENCE"
    WORKFLOW_PATTERN       = "WORKFLOW_PATTERN"


# Numeric rank used only for priority comparisons inside the priority engine.
# Lower number = higher urgency.
_PRIORITY_RANK: dict[str, int] = {
    QueuePriority.CRITICAL: 0,
    QueuePriority.HIGH:     1,
    QueuePriority.MEDIUM:   2,
    QueuePriority.LOW:      3,
}

_RANK_TO_PRIORITY: dict[int, QueuePriority] = {v: k for k, v in _PRIORITY_RANK.items()}  # type: ignore[assignment]


def priority_up(p: QueuePriority) -> QueuePriority:
    """Raise priority by one step (CRITICAL is the ceiling)."""
    rank = max(0, _PRIORITY_RANK[p] - 1)
    return _RANK_TO_PRIORITY[rank]


def priority_down(p: QueuePriority) -> QueuePriority:
    """Lower priority by one step (LOW is the floor)."""
    rank = min(3, _PRIORITY_RANK[p] + 1)
    return _RANK_TO_PRIORITY[rank]


# Valid status transitions: source → set of allowed targets.
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    ReviewStatus.OPEN:      frozenset({ReviewStatus.IN_REVIEW, ReviewStatus.REVIEWED, ReviewStatus.DISMISSED}),
    ReviewStatus.IN_REVIEW: frozenset({ReviewStatus.REVIEWED, ReviewStatus.OPEN}),
    ReviewStatus.REVIEWED:  frozenset({ReviewStatus.OPEN}),
    ReviewStatus.DISMISSED: frozenset({ReviewStatus.OPEN}),
}
