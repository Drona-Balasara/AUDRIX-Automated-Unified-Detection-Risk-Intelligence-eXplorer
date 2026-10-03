"""Supervisory review queue endpoints (Phase 11).

Expose queue item retrieval, summary counts, and valid status-transition
operations.  Analytical findings and evidence are NEVER mutated through
these endpoints.  Only review state (status, reviewer_ref, review_note)
is changed.

Endpoints
---------
GET  /api/v1/queue                     – paginated queue list with filters
GET  /api/v1/queue/summary             – aggregate counts by status/priority/category
GET  /api/v1/queue/{queue_id}          – single queue item
PATCH /api/v1/queue/{queue_id}/status  – transition status / update review metadata
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.review_queue import (
    FindingCategory,
    InvalidTransitionError,
    QueueItemNotFoundError,
    QueuePriority,
    ReviewStatus,
    fetch_queue_items,
    transition_status,
)
from app.analytics.review_queue.models import ReviewQueueItem
from app.db.session import get_db
from app.schemas.queue import (
    QueueItemResponse,
    QueueListResponse,
    QueueSummaryResponse,
    StatusTransitionRequest,
)

router = APIRouter(prefix="/queue", tags=["review-queue"])

_MAX_LIMIT = 200


def _to_response(item: ReviewQueueItem) -> QueueItemResponse:
    return QueueItemResponse(
        queue_id=item.queue_id,
        finding_key=item.finding_key,
        analytic_id=item.analytic_id,
        finding_type=item.finding_type,
        entity_id=item.entity_id,
        period_label=item.period_label,
        title=item.title,
        category=str(item.category),
        priority=str(item.priority),
        confidence=item.confidence,
        evidence_count=item.evidence_count,
        status=str(item.status),
        created_at=item.created_at,
        updated_at=item.updated_at,
        reviewed_at=item.reviewed_at,
        reviewer_ref=item.reviewer_ref,
        review_note=item.review_note,
    )


def _parse_status(value: str | None, param_name: str) -> ReviewStatus | None:
    if value is None:
        return None
    try:
        return ReviewStatus(value.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"invalid {param_name} '{value}'; allowed: {[s.value for s in ReviewStatus]}",
        ) from None


def _parse_priority(value: str | None, param_name: str) -> QueuePriority | None:
    if value is None:
        return None
    try:
        return QueuePriority(value.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"invalid {param_name} '{value}'; allowed: {[p.value for p in QueuePriority]}",
        ) from None


def _parse_category(value: str | None) -> FindingCategory | None:
    if value is None:
        return None
    try:
        return FindingCategory(value.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"invalid category '{value}'; allowed: {[c.value for c in FindingCategory]}",
        ) from None


@router.get("", response_model=QueueListResponse, summary="List review queue items")
def list_queue(
    status: str | None = Query(default=None, description="Filter by status (OPEN, IN_REVIEW, REVIEWED, DISMISSED)."),
    priority: str | None = Query(default=None, description="Filter by priority (CRITICAL, HIGH, MEDIUM, LOW)."),
    entity_id: str | None = Query(default=None, description="Filter by entity identifier."),
    category: str | None = Query(default=None, description="Filter by finding category."),
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT, description="Page size (max 200)."),
    offset: int = Query(default=0, ge=0, description="Page start offset."),
    session: Session = Depends(get_db),
) -> QueueListResponse:
    """Return a paginated review queue, ordered by priority (CRITICAL first) then created_at desc."""
    parsed_status   = _parse_status(status, "status")
    parsed_priority = _parse_priority(priority, "priority")
    parsed_category = _parse_category(category)

    # Count total matching items (without pagination).
    count_stmt = select(func.count()).select_from(ReviewQueueItem)
    if parsed_status is not None:
        count_stmt = count_stmt.where(ReviewQueueItem.status == parsed_status)
    if parsed_priority is not None:
        count_stmt = count_stmt.where(ReviewQueueItem.priority == parsed_priority)
    if entity_id is not None:
        count_stmt = count_stmt.where(ReviewQueueItem.entity_id == entity_id)
    if parsed_category is not None:
        count_stmt = count_stmt.where(ReviewQueueItem.category == parsed_category)
    total = session.scalar(count_stmt) or 0

    items = fetch_queue_items(
        session,
        status=parsed_status,
        priority=parsed_priority,
        entity_id=entity_id,
        category=parsed_category,
        limit=limit,
        offset=offset,
    )

    return QueueListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[_to_response(i) for i in items],
    )


@router.get("/summary", response_model=QueueSummaryResponse, summary="Queue aggregate counts")
def queue_summary(session: Session = Depends(get_db)) -> QueueSummaryResponse:
    """Return aggregate counts by status, priority, and category."""
    total = session.scalar(select(func.count()).select_from(ReviewQueueItem)) or 0

    by_status: dict[str, int] = {}
    for s in ReviewStatus:
        count = session.scalar(
            select(func.count()).select_from(ReviewQueueItem).where(
                ReviewQueueItem.status == s
            )
        ) or 0
        by_status[s.value] = count

    by_priority: dict[str, int] = {}
    for p in QueuePriority:
        count = session.scalar(
            select(func.count()).select_from(ReviewQueueItem).where(
                ReviewQueueItem.priority == p
            )
        ) or 0
        by_priority[p.value] = count

    by_category: dict[str, int] = {}
    for c in FindingCategory:
        count = session.scalar(
            select(func.count()).select_from(ReviewQueueItem).where(
                ReviewQueueItem.category == c
            )
        ) or 0
        by_category[c.value] = count

    return QueueSummaryResponse(
        total=total,
        by_status=by_status,
        by_priority=by_priority,
        by_category=by_category,
    )


@router.get("/{queue_id}", response_model=QueueItemResponse, summary="Get one queue item")
def get_queue_item(queue_id: str, session: Session = Depends(get_db)) -> QueueItemResponse:
    """Return a single review queue item by its stable queue_id."""
    item: ReviewQueueItem | None = session.get(ReviewQueueItem, queue_id)
    if item is None:
        raise HTTPException(status_code=404, detail="queue item not found")
    return _to_response(item)


@router.patch("/{queue_id}/status", response_model=QueueItemResponse,
              summary="Transition review status")
def update_queue_status(
    queue_id: str,
    body: StatusTransitionRequest,
    session: Session = Depends(get_db),
) -> QueueItemResponse:
    """Transition a queue item to a new review status.

    Validates the transition against the defined lifecycle; returns 409 for
    invalid transitions and 404 when the queue_id does not exist.

    Analytical findings and evidence are NEVER modified by this endpoint.
    Only the review state fields (status, reviewer_ref, review_note,
    reviewed_at) are updated.
    """
    # Validate the requested status value.
    new_status = _parse_status(body.new_status, "new_status")
    if new_status is None:
        raise HTTPException(status_code=400, detail="new_status is required")

    # Sanitize reviewer_ref: reject values that look like sensitive data
    # (paths, URLs, tokens).  V1 is a local app with no authentication so
    # this is a minimal guard rather than a full security layer.
    reviewer_ref = body.reviewer_ref
    if reviewer_ref is not None:
        reviewer_ref = reviewer_ref.strip()
        if not reviewer_ref:
            reviewer_ref = None

    try:
        item = transition_status(
            session,
            queue_id,
            new_status,
            reviewer_ref=reviewer_ref,
            review_note=body.review_note,
        )
        session.commit()
    except QueueItemNotFoundError:
        raise HTTPException(status_code=404, detail="queue item not found") from None
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    return _to_response(item)
