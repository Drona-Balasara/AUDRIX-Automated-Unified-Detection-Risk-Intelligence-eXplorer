"""ORM model for the Supervisory Review Queue.

A ``ReviewQueueItem`` is a persistent record that tracks the human-review
lifecycle of one analytical finding.  It does NOT store the finding itself —
it references the finding by its stable ``finding_key`` and stores only the
queue-specific state (status, priority, reviewer reference, review note,
timestamps).

Separation of concerns
-----------------------
- Analytical findings (Phases 4–8) are the source of truth for *what was
  detected* and are never mutated by queue operations.
- Evidence (Phase 9) is the source of truth for *which records support the
  finding* and is never mutated by queue operations.
- ``ReviewQueueItem`` is the source of truth for *human review state* only.

Idempotency and versioning
---------------------------
``queue_id``: deterministic 12-char hex derived from SHA-256(finding_key).
    Stable across repeated analysis runs over identical data.

``finding_version``: deterministic 8-char hex derived from
    SHA-256(finding_key + analytic_id + entity_id + period_label).
    Used to detect material changes between analysis runs.  If
    ``finding_version`` changes on regeneration, the item's analytical
    fields are updated and status is reset to OPEN conservatively.

Re-running analytics never silently erases reviewed state when the underlying
finding is unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import enum_type, utcnow
from app.analytics.review_queue.enums import (
    FindingCategory,
    QueuePriority,
    ReviewStatus,
)


class ReviewQueueItem(Base):
    """One supervisory review queue entry, keyed by finding_key."""

    __tablename__ = "review_queue_item"

    # Stable deterministic identifier (12-char hex from SHA-256 of finding_key).
    queue_id: Mapped[str] = mapped_column(String(12), primary_key=True)

    # --- Finding reference (read-only after initial creation) ---------------
    # The stable key from the analytical finding (e.g. "EG-001:ENT-01:ALR-000001").
    finding_key: Mapped[str] = mapped_column(String(256), nullable=False, unique=True, index=True)
    analytic_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    finding_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # "YYYY-MM" label for the primary reporting period.
    period_label: Mapped[str] = mapped_column(String(16), nullable=False)

    # Concise, neutral title derived from analytic + finding type.
    title: Mapped[str] = mapped_column(String(256), nullable=False)

    # --- Queue-derived fields (recalculated on upsert) ----------------------
    category: Mapped[FindingCategory] = mapped_column(
        enum_type(FindingCategory), nullable=False
    )
    priority: Mapped[QueuePriority] = mapped_column(
        enum_type(QueuePriority), nullable=False, index=True
    )
    # Snapshot of the evidence confidence at queue-generation time.
    confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    # Number of EvidenceRef entries at queue-generation time.
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Change-detection fingerprint for idempotent upsert.
    finding_version: Mapped[str] = mapped_column(String(8), nullable=False)

    # --- Review lifecycle ---------------------------------------------------
    status: Mapped[ReviewStatus] = mapped_column(
        enum_type(ReviewStatus),
        nullable=False,
        default=ReviewStatus.OPEN,
        index=True,
    )

    # When the queue item was first created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    # When the item was last updated (status change, note update, etc.).
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    # When the review was completed (status became REVIEWED or DISMISSED).
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Optional opaque reviewer reference — not an account ID, just a label
    # (e.g. "analyst-team-a", "supervisor-1") for auditability.  V1 does not
    # implement authentication; this field is informational only.
    reviewer_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Free-form review note (plaintext, no sensitive data, not a raw payload).
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
