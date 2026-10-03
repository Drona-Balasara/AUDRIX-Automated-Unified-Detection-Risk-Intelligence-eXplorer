"""Pydantic v2 response and request schemas for the review queue API (Phase 11).

Separate from the ORM model and the analytics-layer dataclasses: the API
layer controls serialization format and field visibility independently.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class QueueItemResponse(BaseModel):
    """Public representation of one review queue item."""

    queue_id: str
    finding_key: str
    analytic_id: str
    finding_type: str
    entity_id: str
    period_label: str
    title: str
    category: str
    priority: str = Field(description="CRITICAL, HIGH, MEDIUM, or LOW.")
    confidence: str = Field(description="Confidence snapshot: HIGH, MODERATE, LOW, or UNKNOWN.")
    evidence_count: int
    status: str = Field(description="OPEN, IN_REVIEW, REVIEWED, or DISMISSED.")
    created_at: datetime
    updated_at: datetime
    reviewed_at: datetime | None = None
    reviewer_ref: str | None = None
    review_note: str | None = None


class QueueListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[QueueItemResponse]


class QueueSummaryResponse(BaseModel):
    """Aggregate counts for the review queue, useful for dashboard widgets."""

    total: int
    by_status: dict[str, int]
    by_priority: dict[str, int]
    by_category: dict[str, int]


class StatusTransitionRequest(BaseModel):
    """Request body for a review-queue status transition."""

    new_status: str = Field(
        description="Target status: IN_REVIEW, REVIEWED, DISMISSED, or OPEN (reopen)."
    )
    reviewer_ref: str | None = Field(
        default=None,
        max_length=128,
        description="Optional opaque reviewer label (not an account ID).",
    )
    review_note: str | None = Field(
        default=None,
        max_length=4096,
        description="Free-form review note. Must not contain secrets or sensitive data.",
    )
