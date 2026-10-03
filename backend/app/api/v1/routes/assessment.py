"""Assessment execution endpoint (Phase 11).

``POST /api/v1/assessment/run`` runs all Phase 4–8 analytics against the
current database contents, builds Phase 9 evidence/confidence, populates
the Phase 10 supervisory review queue, and returns aggregate counts.

Design notes
------------
- Analytical findings are *not* persisted — they are derived on demand from
  the ingested domain records each time assessment is run.
- Only the review queue is written (idempotent upsert via Phase 10 logic).
- The endpoint is synchronous; for the local SAT-SA application the full run
  completes in well under a second on the synthetic dataset.
- No ground truth is consulted; no analytical thresholds are altered.
"""

from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.analytics.anomaly import run_anomaly_detection
from app.analytics.anomaly.engine import AnomalyStatus
from app.analytics.evidence import (
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
from app.analytics.review_queue import FindingsBundle, build_review_queue
from app.core.logging import get_logger
from app.db.session import get_db
from app.schemas.analytics import AssessmentAnalyticSummary, AssessmentRunResponse

router = APIRouter(prefix="/assessment", tags=["assessment"])
logger = get_logger(__name__)


@router.post("/run", response_model=AssessmentRunResponse, summary="Run full analytical assessment")
def run_assessment(session: Session = Depends(get_db)) -> AssessmentRunResponse:
    """Execute all Phase 4–8 analytics, build evidence/confidence, and refresh the review queue.

    Analytical findings are derived from the current ingested domain records
    and are NOT stored in the database — they are recomputed on each run.
    The supervisory review queue IS updated (idempotent upsert).

    Returns aggregate counts per analytic and queue insertion summary.
    No ground truth is used; no analytical thresholds are modified.
    """
    t0 = perf_counter()

    # --- Run all analytics ---------------------------------------------------
    eg   = run_execution_gap_detection(session)
    ns   = run_negative_space_detection(session)
    an   = run_anomaly_detection(session)
    pb   = run_peer_benchmark(session)
    mrd  = run_metric_risk_divergence(session)
    fp   = run_investigation_fingerprinting(session)

    # --- Attach evidence/confidence (Phase 9) --------------------------------
    a_eg  = annotate_execution_gap(eg)
    a_ns  = annotate_negative_space(ns)
    a_an  = annotate_anomaly(an)
    a_pb  = annotate_peer_benchmark(pb)
    a_mrd = annotate_metric_risk_divergence(mrd)
    a_fp  = annotate_fingerprint(fp)

    # --- Populate review queue (Phase 10) ------------------------------------
    bundle = FindingsBundle(
        execution_gap=a_eg,
        negative_space=a_ns,
        anomaly=a_an,
        peer_benchmark=a_pb,
        metric_risk_div=a_mrd,
        fingerprint=a_fp,
    )
    queue_summary = build_review_queue(session, bundle)
    session.commit()

    duration_ms = (perf_counter() - t0) * 1000.0

    # --- Entity count --------------------------------------------------------
    from sqlalchemy import func, select
    from app.models.organization import SocEntity
    entity_count = session.scalar(select(func.count()).select_from(SocEntity)) or 0

    analytics_summaries = [
        AssessmentAnalyticSummary(analytic_id="EG-001", finding_count=eg.finding_count, status="ok"),
        AssessmentAnalyticSummary(analytic_id="NS-001", finding_count=ns.finding_count, status="ok"),
        AssessmentAnalyticSummary(
            analytic_id="AN-001",
            finding_count=an.finding_count,
            status=an.status.value if hasattr(an.status, "value") else str(an.status),
        ),
        AssessmentAnalyticSummary(analytic_id="PB-001", finding_count=pb.finding_count, status="ok"),
        AssessmentAnalyticSummary(analytic_id="MRD-001", finding_count=mrd.finding_count, status="ok"),
        AssessmentAnalyticSummary(analytic_id="IF", finding_count=fp.finding_count, status="ok"),
    ]

    total_findings = sum(s.finding_count for s in analytics_summaries)

    logger.info(
        "assessment run complete: entities=%d findings=%d queue_total=%d duration_ms=%.1f",
        entity_count, total_findings, queue_summary.total_items, duration_ms,
    )

    return AssessmentRunResponse(
        entity_count=entity_count,
        total_findings=total_findings,
        analytics=analytics_summaries,
        queue_items_total=queue_summary.total_items,
        queue_inserted=queue_summary.inserted,
        queue_unchanged=queue_summary.unchanged,
        duration_ms=round(duration_ms, 1),
    )
