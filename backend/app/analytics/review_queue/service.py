"""Public service boundary for the Supervisory Review Queue.

This module is the primary programmatic entry point for Phase 10.  It is
FastAPI-independent and can be called from scripts, tests, or (later) thin
HTTP route handlers.

Main entry point:  ``build_review_queue(session, findings_bundle)``

    Takes a ``FindingsBundle`` (a container of all Phase 4–8 annotated results)
    and upserts the full set of review queue items into the database.

    Returns a ``QueueSummary`` with counts by priority, status, and category.

Status operations are delegated to ``app.analytics.review_queue.queue``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter

from sqlalchemy.orm import Session

from app.analytics.evidence.annotated import (
    AnnotatedAnomalyResult,
    AnnotatedExecutionGapResult,
    AnnotatedFingerprintResult,
    AnnotatedMetricRiskDivergenceResult,
    AnnotatedNegativeSpaceResult,
    AnnotatedPeerBenchmarkResult,
)
from app.analytics.evidence.model import EvidenceConfidence
from app.analytics.review_queue.enums import (
    FindingCategory,
    QueuePriority,
    ReviewStatus,
)
from app.analytics.review_queue.models import ReviewQueueItem
from app.analytics.review_queue.queue import QueueItemData, upsert_queue_item
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Findings bundle (typed container of all annotated results)
# ---------------------------------------------------------------------------

@dataclass
class FindingsBundle:
    """Container of all Phase 4–8 annotated results for one assessment run.

    All fields are optional: a bundle with only some analytics present will
    generate queue items only for the provided results.  This supports partial
    runs and re-runs where only some analytics are refreshed.
    """

    execution_gap:      AnnotatedExecutionGapResult | None      = None
    negative_space:     AnnotatedNegativeSpaceResult | None     = None
    anomaly:            AnnotatedAnomalyResult | None           = None
    peer_benchmark:     AnnotatedPeerBenchmarkResult | None     = None
    metric_risk_div:    AnnotatedMetricRiskDivergenceResult | None = None
    fingerprint:        AnnotatedFingerprintResult | None       = None


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

@dataclass
class QueueSummary:
    """Aggregate counts from one queue-generation run."""

    total_items:   int = 0
    inserted:      int = 0
    updated:       int = 0
    unchanged:     int = 0

    by_priority:   dict[str, int] = field(default_factory=dict)
    by_status:     dict[str, int] = field(default_factory=dict)
    by_category:   dict[str, int] = field(default_factory=dict)
    duration_ms:   float = 0.0


# ---------------------------------------------------------------------------
# Item-data extraction helpers (one per analytic)
# ---------------------------------------------------------------------------

def _eg_items(annotated: AnnotatedExecutionGapResult) -> list[QueueItemData]:
    items: list[QueueItemData] = []
    for f in annotated.result.findings:
        ev = annotated.evidence_for(f.finding_key)
        # Extract period from observed_at or window_start (whichever is present)
        ts = f.observed_at or f.window_start
        period = ts.strftime("%Y-%m") if ts else "unknown"
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="EG-001",
            finding_type=str(f.reason_code),
            entity_id=f.entity_id,
            period_label=period,
            alert_severity=f.alert_severity,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    return items


def _ns_items(annotated: AnnotatedNegativeSpaceResult) -> list[QueueItemData]:
    items: list[QueueItemData] = []
    for f in annotated.result.findings:
        ev = annotated.evidence_for(f.finding_key)
        ts = f.observation_start
        period = ts.strftime("%Y-%m") if ts else "unknown"
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="NS-001",
            finding_type=str(f.reason_code),
            entity_id=f.entity_id,
            period_label=period,
            alert_severity=None,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    return items


def _an_items(annotated: AnnotatedAnomalyResult) -> list[QueueItemData]:
    items: list[QueueItemData] = []
    for f in annotated.result.findings:
        ev = annotated.evidence_for(f.finding_key)
        period = f.period_start.strftime("%Y-%m")
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="AN-001",
            finding_type=str(f.decision),
            entity_id=f.entity_id,
            period_label=period,
            alert_severity=None,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    return items


def _pb_items(annotated: AnnotatedPeerBenchmarkResult) -> list[QueueItemData]:
    items: list[QueueItemData] = []
    for f in annotated.result.findings:
        ev = annotated.evidence_for(f.finding_key)
        period = f.period_start.strftime("%Y-%m")
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="PB-001",
            finding_type="PEER_DEVIATION",
            entity_id=f.entity_id,
            period_label=period,
            alert_severity=None,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    return items


def _mrd_items(annotated: AnnotatedMetricRiskDivergenceResult) -> list[QueueItemData]:
    items: list[QueueItemData] = []
    for f in annotated.result.findings:
        ev = annotated.evidence_for(f.finding_key)
        period = f.window_start.strftime("%Y-%m")
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="MRD-001",
            finding_type="METRIC_RISK_DIVERGENCE",
            entity_id=f.entity_id,
            period_label=period,
            alert_severity=None,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    return items


def _fp_items(annotated: AnnotatedFingerprintResult) -> list[QueueItemData]:
    items: list[QueueItemData] = []
    for f in annotated.result.repetitive_findings:
        ev = annotated.evidence_for(f.finding_key)
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="IF-REP-001",
            finding_type="REPETITIVE_WORKFLOW",
            entity_id=f.entity_id,
            period_label=f.period_label,
            alert_severity=None,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    for f in annotated.result.deviation_findings:
        ev = annotated.evidence_for(f.finding_key)
        period = f.period_start.strftime("%Y-%m")
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="IF-DEV-002",
            finding_type="SEQUENCE_DEVIATION",
            entity_id=f.entity_id,
            period_label=period,
            alert_severity=None,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    for f in annotated.result.missing_action_findings:
        ev = annotated.evidence_for(f.finding_key)
        period = f.period_start.strftime("%Y-%m")
        items.append(QueueItemData(
            finding_key=f.finding_key,
            analytic_id="IF-MEA-003",
            finding_type="MISSING_EXPECTED_ACTION",
            entity_id=f.entity_id,
            period_label=period,
            alert_severity=f.alert_severity,
            confidence=ev.confidence if ev else None,
            evidence_count=ev.evidence_count if ev else 0,
        ))
    return items


# ---------------------------------------------------------------------------
# Main service function
# ---------------------------------------------------------------------------

def build_review_queue(
    session: Session,
    bundle: FindingsBundle,
) -> QueueSummary:
    """Upsert all findings in ``bundle`` into the supervisory review queue.

    Deterministic, idempotent, read-only with respect to analytical findings
    and evidence.  Only the ``review_queue_item`` table is written.

    Returns a ``QueueSummary`` with aggregate counts.
    """
    t0 = perf_counter()
    summary = QueueSummary()

    # Collect all item-data objects.
    all_items: list[QueueItemData] = []
    if bundle.execution_gap is not None:
        all_items.extend(_eg_items(bundle.execution_gap))
    if bundle.negative_space is not None:
        all_items.extend(_ns_items(bundle.negative_space))
    if bundle.anomaly is not None:
        all_items.extend(_an_items(bundle.anomaly))
    if bundle.peer_benchmark is not None:
        all_items.extend(_pb_items(bundle.peer_benchmark))
    if bundle.metric_risk_div is not None:
        all_items.extend(_mrd_items(bundle.metric_risk_div))
    if bundle.fingerprint is not None:
        all_items.extend(_fp_items(bundle.fingerprint))

    # Upsert each item.
    for data in all_items:
        item, created = upsert_queue_item(session, data)
        summary.total_items += 1
        if created:
            summary.inserted += 1
        elif item.finding_version == _recompute_version(data):
            summary.unchanged += 1
        else:
            summary.updated += 1

        # Tally by priority, status, category.
        summary.by_priority[item.priority] = summary.by_priority.get(item.priority, 0) + 1
        summary.by_status[item.status]     = summary.by_status.get(item.status, 0) + 1
        summary.by_category[item.category] = summary.by_category.get(item.category, 0) + 1

    session.flush()

    summary.duration_ms = (perf_counter() - t0) * 1000.0

    logger.info(
        "review queue built: total=%d inserted=%d updated=%d unchanged=%d "
        "by_priority=%s by_status=%s duration_ms=%.1f",
        summary.total_items, summary.inserted, summary.updated, summary.unchanged,
        dict(summary.by_priority), dict(summary.by_status), summary.duration_ms,
    )
    return summary


def _recompute_version(data: QueueItemData) -> str:
    """Recompute the finding_version to detect changes post-upsert."""
    from app.analytics.review_queue.queue import _finding_version
    return _finding_version(data.finding_key, data.analytic_id, data.entity_id, data.period_label)


def fetch_queue_items(
    session: Session,
    *,
    status: ReviewStatus | None = None,
    priority: QueuePriority | None = None,
    entity_id: str | None = None,
    category: FindingCategory | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[ReviewQueueItem]:
    """Retrieve queue items with optional filters.

    Ordered by priority (CRITICAL first), then by created_at descending.
    """
    from sqlalchemy import select
    from app.analytics.review_queue.enums import _PRIORITY_RANK

    stmt = select(ReviewQueueItem)
    if status is not None:
        stmt = stmt.where(ReviewQueueItem.status == status)
    if priority is not None:
        stmt = stmt.where(ReviewQueueItem.priority == priority)
    if entity_id is not None:
        stmt = stmt.where(ReviewQueueItem.entity_id == entity_id)
    if category is not None:
        stmt = stmt.where(ReviewQueueItem.category == category)

    # Python-side sort by priority rank then created_at desc.
    rows = list(session.scalars(stmt.offset(offset).limit(limit)).all())
    rows.sort(
        key=lambda r: (_PRIORITY_RANK.get(str(r.priority), 99), -(r.created_at.timestamp() if r.created_at else 0))
    )
    return rows
