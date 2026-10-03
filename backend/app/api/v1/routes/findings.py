"""Findings endpoints (Phase 11).

Run analytics on demand and return finding lists with optional filters and
pagination.  Findings are read-only.  Evidence and confidence are included
when the ``include_evidence`` query parameter is true (default false) to
avoid a large default response for list endpoints.

Endpoints
---------
GET /api/v1/findings                    – paginated list, optional filters
GET /api/v1/findings/{finding_key}      – single finding with evidence

Design notes
------------
- Analytics are re-run per request.  For the local SAT-SA application with
  its deterministic synthetic dataset this takes ~100–500 ms and avoids
  the complexity of a result cache.  Future phases can add caching if needed.
- Findings from all Phase 4–8 analytics are collected into a unified list.
  Each finding carries its ``analytic_id``, ``finding_type``, and ``category``
  so consumers can always identify the source analytic.
- ``GET /findings/{finding_key}`` URL-encodes the finding key (which may
  contain colons).  FastAPI path parameters handle the full key string
  transparently via {finding_key:path} matching.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analytics.anomaly import run_anomaly_detection
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
from app.analytics.review_queue.priority import finding_category
from app.db.session import get_db
from app.schemas.analytics import (
    EvidenceRefResponse,
    FindingDetailResponse,
    FindingEvidenceResponse,
    FindingListResponse,
    FindingResponse,
)

router = APIRouter(prefix="/findings", tags=["findings"])

_MAX_LIMIT = 500


def _period_from_finding(finding) -> str:
    """Extract YYYY-MM period label from any finding type."""
    for attr in ("period_start", "observed_at", "window_start", "observation_start"):
        dt = getattr(finding, attr, None)
        if dt is not None:
            return dt.strftime("%Y-%m")
    if hasattr(finding, "period_label"):
        return finding.period_label
    return "unknown"


def _details_from_finding(finding) -> dict:
    """Extract analytic-specific supplementary fields as a safe dict."""
    d: dict = {}
    # Execution gap
    for attr in ("gap_type", "reason_code", "alert_severity", "alert_id",
                 "investigation_id", "asset_id", "recurrence_key",
                 "expected_condition", "observed_condition"):
        v = getattr(finding, attr, None)
        if v is not None:
            d[attr] = str(v)
    # Anomaly
    for attr in ("decision", "normalized_anomaly_score", "raw_anomaly_score"):
        v = getattr(finding, attr, None)
        if v is not None:
            d[attr] = v if not hasattr(v, "value") else str(v)
    # Peer benchmark
    for attr in ("metric_name", "metric_unit", "entity_value", "peer_baseline",
                 "peer_population_count", "deviation_measure", "deviation_basis", "direction"):
        v = getattr(finding, attr, None)
        if v is not None:
            d[attr] = v if not hasattr(v, "value") else str(v)
    # MRD
    for attr in ("headline_trend_score", "quality_trend_score", "supporting_period_count", "confidence"):
        v = getattr(finding, attr, None)
        if v is not None:
            d[attr] = v if not hasattr(v, "value") else str(v)
    # Fingerprinting
    for attr in ("finding_type", "normalized_distance", "baseline_investigation_count",
                 "matching_investigation_count", "repetition_rate", "alert_severity",
                 "alert_id", "investigation_id"):
        v = getattr(finding, attr, None)
        if v is not None and attr not in d:
            d[attr] = v if not hasattr(v, "value") else str(v)
    # Negative space
    for attr in ("gap_type", "reason_code", "asset_id", "asset_criticality",
                 "expected_telemetry", "silence_hours"):
        v = getattr(finding, attr, None)
        if v is not None and attr not in d:
            d[attr] = v if not hasattr(v, "value") else str(v)
    return d


def _finding_type_from(analytic_id: str, finding) -> str:
    """Extract the machine-readable type/reason code for a finding."""
    for attr in ("reason_code", "finding_type", "gap_type", "decision"):
        v = getattr(finding, attr, None)
        if v is not None:
            return str(v)
    return analytic_id


def _to_response(analytic_id: str, finding, evidence=None) -> FindingResponse:
    ftype = _finding_type_from(analytic_id, finding)
    period = _period_from_finding(finding)
    cat = str(finding_category(analytic_id))

    # Confidence from evidence layer if available.
    conf: str | None = None
    ev_count: int = 0
    if evidence is not None:
        conf = str(evidence.confidence)
        ev_count = evidence.evidence_count
    # Some findings already carry a native confidence field.
    if conf is None:
        native = getattr(finding, "confidence", None)
        if native is not None:
            conf = str(native)

    return FindingResponse(
        finding_key=finding.finding_key,
        analytic_id=analytic_id,
        finding_type=ftype,
        category=cat,
        entity_id=finding.entity_id,
        period_label=period,
        summary=finding.summary,
        confidence=conf,
        evidence_count=ev_count,
        details=_details_from_finding(finding),
    )


def _evidence_response(ev) -> FindingEvidenceResponse:
    return FindingEvidenceResponse(
        finding_key=ev.finding_key,
        analytic_id=ev.analytic_id,
        confidence=str(ev.confidence),
        confidence_note=ev.confidence_note,
        confidence_factors=[str(f) for f in ev.confidence_factors],
        evidence_count=ev.evidence_count,
        evidence_refs=[
            EvidenceRefResponse(
                source_type=str(r.source_type),
                source_id=r.source_id,
                role=r.role,
                period_label=r.period_label,
                reason=r.reason,
            )
            for r in ev.evidence_refs
        ],
    )


def _collect_all(session: Session, include_evidence: bool):
    """Run all analytics and return list of (analytic_id, finding, evidence|None)."""
    eg  = run_execution_gap_detection(session)
    ns  = run_negative_space_detection(session)
    an  = run_anomaly_detection(session)
    pb  = run_peer_benchmark(session)
    mrd = run_metric_risk_divergence(session)
    fp  = run_investigation_fingerprinting(session)

    if include_evidence:
        a_eg  = annotate_execution_gap(eg)
        a_ns  = annotate_negative_space(ns)
        a_an  = annotate_anomaly(an)
        a_pb  = annotate_peer_benchmark(pb)
        a_mrd = annotate_metric_risk_divergence(mrd)
        a_fp  = annotate_fingerprint(fp)
    else:
        a_eg = a_ns = a_an = a_pb = a_mrd = a_fp = None

    items: list[tuple[str, object, object]] = []
    for f in eg.findings:
        ev = a_eg.evidence_for(f.finding_key) if a_eg else None
        items.append(("EG-001", f, ev))
    for f in ns.findings:
        ev = a_ns.evidence_for(f.finding_key) if a_ns else None
        items.append(("NS-001", f, ev))
    for f in an.findings:
        ev = a_an.evidence_for(f.finding_key) if a_an else None
        items.append(("AN-001", f, ev))
    for f in pb.findings:
        ev = a_pb.evidence_for(f.finding_key) if a_pb else None
        items.append(("PB-001", f, ev))
    for f in mrd.findings:
        ev = a_mrd.evidence_for(f.finding_key) if a_mrd else None
        items.append(("MRD-001", f, ev))
    for f in fp.all_findings:
        analytic = f.finding_key.split(":")[0]  # IF-REP-001, IF-DEV-002, IF-MEA-003
        ev = a_fp.evidence_for(f.finding_key) if a_fp else None
        items.append((analytic, f, ev))

    return items


@router.get("", response_model=FindingListResponse, summary="List analytical findings")
def list_findings(
    entity_id: str | None = Query(default=None, description="Filter by entity identifier."),
    analytic_id: str | None = Query(default=None, description="Filter by analytic (e.g. EG-001)."),
    category: str | None = Query(default=None, description="Filter by broad category."),
    period_label: str | None = Query(default=None, description="Filter by YYYY-MM period."),
    include_evidence: bool = Query(default=False, description="Include evidence refs and confidence."),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT, description="Page size (max 500)."),
    offset: int = Query(default=0, ge=0, description="Page start offset."),
    session: Session = Depends(get_db),
) -> FindingListResponse:
    """Return a paginated, optionally filtered list of findings from all Phase 4–8 analytics.

    Results are ordered deterministically by finding_key.  Each finding
    carries its ``analytic_id``, ``finding_type``, and ``category`` so clients
    can always identify the source analytic.
    """
    all_items = _collect_all(session, include_evidence)

    # Apply filters.
    if entity_id:
        all_items = [(a, f, e) for a, f, e in all_items if f.entity_id == entity_id]
    if analytic_id:
        all_items = [(a, f, e) for a, f, e in all_items if a == analytic_id]
    if category:
        all_items = [(a, f, e) for a, f, e in all_items
                     if str(finding_category(a)) == category]
    if period_label:
        all_items = [(a, f, e) for a, f, e in all_items
                     if _period_from_finding(f) == period_label]

    # Stable sort by finding_key.
    all_items.sort(key=lambda t: t[1].finding_key)
    total = len(all_items)
    page = all_items[offset: offset + limit]

    return FindingListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_response(a, f, e) for a, f, e in page],
    )


@router.get("/{finding_key:path}", response_model=FindingDetailResponse,
            summary="Get one finding with evidence")
def get_finding(
    finding_key: str,
    session: Session = Depends(get_db),
) -> FindingDetailResponse:
    """Return a single finding with full evidence and confidence.

    The finding key may contain colons and is matched after URL-decoding.
    Evidence is always included for single-finding requests.
    """
    all_items = _collect_all(session, include_evidence=True)
    for analytic, f, ev in all_items:
        if f.finding_key == finding_key:
            return FindingDetailResponse(
                finding=_to_response(analytic, f, ev),
                evidence=_evidence_response(ev) if ev is not None else None,
            )
    raise HTTPException(status_code=404, detail="finding not found")
