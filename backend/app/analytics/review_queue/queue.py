"""Core queue operations: item construction, idempotent upsert, status transitions.

All public functions are FastAPI-independent and can be called from scripts,
tests, or (later) thin HTTP route handlers.

Idempotency contract
--------------------
``upsert_queue_item(session, item_data)`` is safe to call repeatedly with the
same analytical findings.

- If ``queue_id`` does not exist: insert a new OPEN item.
- If ``queue_id`` exists and ``finding_version`` is unchanged:
    → preserve all review state (status, reviewer_ref, review_note,
      reviewed_at, created_at) and update only ``updated_at``.
- If ``queue_id`` exists and ``finding_version`` changed (finding materially
    changed between runs):
    → update all analytical fields (title, priority, confidence,
      evidence_count, category, finding_type, period_label);
    → reset status to OPEN and clear reviewer fields conservatively;
    → this ensures stale human review decisions are not silently left in
      place when the underlying finding changed.

Status transitions
------------------
``transition_status(session, queue_id, new_status, ...)`` validates the
requested transition against ``VALID_TRANSITIONS`` and raises
``InvalidTransitionError`` for disallowed moves.  The analytical finding and
evidence are never touched by transition operations.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.analytics.evidence.model import EvidenceConfidence
from app.analytics.review_queue.enums import (
    FindingCategory,
    QueuePriority,
    ReviewStatus,
    VALID_TRANSITIONS,
)
from app.analytics.review_queue.models import ReviewQueueItem
from app.analytics.review_queue.priority import derive_priority, finding_category


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class InvalidTransitionError(ValueError):
    """Raised when a requested status transition is not permitted."""

    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            f"Cannot transition from {current!r} to {requested!r}. "
            f"Allowed targets from {current!r}: "
            f"{sorted(VALID_TRANSITIONS.get(current, set()))}."
        )
        self.current = current
        self.requested = requested


class QueueItemNotFoundError(KeyError):
    """Raised when a queue_id does not exist in the database."""

    def __init__(self, queue_id: str) -> None:
        super().__init__(f"No review queue item with queue_id={queue_id!r}")
        self.queue_id = queue_id


# ---------------------------------------------------------------------------
# ID / version utilities
# ---------------------------------------------------------------------------

def _queue_id(finding_key: str) -> str:
    """Deterministic 12-char hex queue identifier derived from ``finding_key``."""
    return hashlib.sha256(finding_key.encode()).hexdigest()[:12]


def _finding_version(
    finding_key: str,
    analytic_id: str,
    entity_id: str,
    period_label: str,
) -> str:
    """Deterministic 8-char hex version fingerprint.

    Changes if any of the four fields change, allowing idempotent upsert to
    detect a materially different finding mapped to the same ``queue_id``.
    """
    raw = f"{finding_key}|{analytic_id}|{entity_id}|{period_label}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


def _neutral_title(analytic_id: str, finding_type: str, entity_id: str) -> str:
    """Generate a concise, neutral display title from analytic metadata.

    Titles are deliberately generic and observational.  They never assert
    intent, blame, negligence, or misconduct.
    """
    _titles: dict[tuple[str, str], str] = {
        ("EG-001", "ESCALATION_GAP"):    "Potential Escalation Gap",
        ("EG-001", "INVESTIGATION_GAP"): "Potential Investigation Gap",
        ("EG-001", "REMEDIATION_GAP"):   "Potential Remediation Gap",
        ("NS-001", "MONITORING_GAP"):     "Potential Monitoring Gap",
        ("NS-001", "TELEMETRY_DISAPPEARANCE"): "Potential Telemetry Disappearance",
        ("AN-001", "ANOMALOUS"):          "Potential Operational Anomaly",
        ("PB-001", "PEER_DEVIATION"):     "Potential Peer Benchmark Deviation",
        ("MRD-001", "METRIC_RISK_DIVERGENCE"): "Potential Metric-Risk Divergence",
        ("IF-REP-001", "REPETITIVE_WORKFLOW"):  "Potential Template-Driven Investigation Pattern",
        ("IF-DEV-002", "SEQUENCE_DEVIATION"):   "Potential Investigation Sequence Deviation",
        ("IF-MEA-003", "MISSING_EXPECTED_ACTION"): "Potential Missing Investigation Action",
    }
    key = (analytic_id, finding_type)
    base = _titles.get(key, f"Potential {finding_type.replace('_', ' ').title()}")
    return f"{base} — {entity_id}"


# ---------------------------------------------------------------------------
# Item-data dataclass (passed to upsert, no ORM dependency)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class QueueItemData:
    """All information needed to construct or update a ``ReviewQueueItem``.

    This dataclass is the boundary between the analytical layer (findings +
    evidence) and the persistence layer.  It is computed by the service and
    passed to ``upsert_queue_item``.
    """

    finding_key: str
    analytic_id: str
    finding_type: str           # reason_code / gap_type / finding_type string
    entity_id: str
    period_label: str           # "YYYY-MM"

    # Optional signal fields for priority derivation.
    alert_severity: str | None = None

    # From Phase 9 evidence layer (may be None if evidence was not computed).
    confidence: EvidenceConfidence | str | None = None
    evidence_count: int = 0


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def build_queue_item(data: QueueItemData) -> dict:
    """Compute all derived fields for a queue item from ``QueueItemData``.

    Returns a plain dict suitable for constructing or updating a
    ``ReviewQueueItem``.  Does not touch the database.
    """
    qid      = _queue_id(data.finding_key)
    version  = _finding_version(
        data.finding_key, data.analytic_id, data.entity_id, data.period_label
    )
    priority = derive_priority(
        analytic_id=data.analytic_id,
        finding_sub_type=data.finding_type,
        alert_severity=data.alert_severity,
        evidence_confidence=data.confidence,
        evidence_count=data.evidence_count,
    )
    category = finding_category(data.analytic_id)
    title    = _neutral_title(data.analytic_id, data.finding_type, data.entity_id)
    conf_str = str(data.confidence) if data.confidence is not None else "UNKNOWN"

    return {
        "queue_id":        qid,
        "finding_key":     data.finding_key,
        "analytic_id":     data.analytic_id,
        "finding_type":    data.finding_type,
        "entity_id":       data.entity_id,
        "period_label":    data.period_label,
        "title":           title,
        "category":        category,
        "priority":        priority,
        "confidence":      conf_str,
        "evidence_count":  data.evidence_count,
        "finding_version": version,
    }


def upsert_queue_item(
    session: Session,
    data: QueueItemData,
) -> tuple[ReviewQueueItem, bool]:
    """Insert or update one review queue item.

    Returns ``(item, created)`` where ``created`` is ``True`` on insert.

    Idempotency rules:
    - New finding_key → INSERT with status OPEN.
    - Existing, same finding_version → update updated_at only (preserve review state).
    - Existing, different finding_version → update all analytical fields,
      reset status to OPEN and clear reviewer fields.
    """
    fields = build_queue_item(data)
    qid    = fields["queue_id"]
    now    = datetime.now(timezone.utc)

    existing: ReviewQueueItem | None = session.get(ReviewQueueItem, qid)

    if existing is None:
        item = ReviewQueueItem(
            **fields,
            status=ReviewStatus.OPEN,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        return item, True

    # Existing item.
    if existing.finding_version == fields["finding_version"]:
        # Unchanged finding — touch only updated_at.
        existing.updated_at = now
        return existing, False

    # Finding materially changed — update analytical fields, reset review state.
    existing.finding_key     = fields["finding_key"]
    existing.analytic_id     = fields["analytic_id"]
    existing.finding_type    = fields["finding_type"]
    existing.entity_id       = fields["entity_id"]
    existing.period_label    = fields["period_label"]
    existing.title           = fields["title"]
    existing.category        = fields["category"]
    existing.priority        = fields["priority"]
    existing.confidence      = fields["confidence"]
    existing.evidence_count  = fields["evidence_count"]
    existing.finding_version = fields["finding_version"]
    existing.status          = ReviewStatus.OPEN
    existing.reviewer_ref    = None
    existing.review_note     = None
    existing.reviewed_at     = None
    existing.updated_at      = now
    return existing, False


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def transition_status(
    session: Session,
    queue_id: str,
    new_status: ReviewStatus,
    *,
    reviewer_ref: str | None = None,
    review_note: str | None = None,
) -> ReviewQueueItem:
    """Transition a queue item to ``new_status`` with validation.

    Raises
    ------
    QueueItemNotFoundError
        When ``queue_id`` does not exist.
    InvalidTransitionError
        When the transition from the current status to ``new_status`` is not
        in ``VALID_TRANSITIONS``.

    The analytical finding and evidence are never touched.
    """
    item: ReviewQueueItem | None = session.get(ReviewQueueItem, queue_id)
    if item is None:
        raise QueueItemNotFoundError(queue_id)

    allowed = VALID_TRANSITIONS.get(item.status, frozenset())
    if new_status not in allowed:
        raise InvalidTransitionError(item.status, new_status)

    now = datetime.now(timezone.utc)
    item.status     = new_status
    item.updated_at = now

    if new_status in (ReviewStatus.REVIEWED, ReviewStatus.DISMISSED):
        item.reviewed_at  = now
        if reviewer_ref is not None:
            item.reviewer_ref = reviewer_ref
        if review_note is not None:
            item.review_note  = review_note
    elif new_status == ReviewStatus.IN_REVIEW:
        if reviewer_ref is not None:
            item.reviewer_ref = reviewer_ref

    return item
