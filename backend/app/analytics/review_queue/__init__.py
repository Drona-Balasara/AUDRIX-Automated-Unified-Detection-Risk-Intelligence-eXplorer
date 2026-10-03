"""Supervisory Review Queue (Phase 10).

Converts the completed analytical findings (Phases 4–8) and evidence/confidence
information (Phase 9) into a structured human-review workflow.  The queue helps
a supervisor decide what to inspect, which evidence is available, how confident
the analytical system is in the signal, and what review action was taken.

Design references
-----------------
NIST SP 800-61 Rev. 3 (April 2025):
    Integrates incident response into cybersecurity risk management and
    emphasises structured, prioritized detection and response activities.
    SAT-SA's review queue provides the supervisory layer that enables
    systematic review of analytical signals within an operational context.

NIST SP 800-55 Vol. 1 (December 2024):
    Provides guidance on prioritizing information-security measures based on
    impact/likelihood/risk considerations.  The queue's priority logic applies
    these principles: finding type, alert severity, evidence quality, and
    confidence each contribute to a transparent, rule-based priority.

Architecture
------------
- ``ReviewQueueItem`` (ORM model in ``models.py``) is persisted in the
  existing SQLite database alongside the domain models.  It is created via
  ``Base.metadata.create_all()`` — no separate migration tool is required.

- ``build_review_queue(session, bundle)`` accepts a ``FindingsBundle`` of all
  Phase 4–8 annotated results and upserts the full set of queue items.

- Priority is separate from confidence.  Confidence reflects evidence
  sufficiency; priority reflects the attention a finding deserves for review.
  A LOW-confidence finding may still deserve HIGH priority.

- Status lifecycle: OPEN → IN_REVIEW → REVIEWED; OPEN → REVIEWED (shortcut);
  REVIEWED / DISMISSED → OPEN (reopen).

- Idempotent: repeated runs over unchanged data preserve existing review state.

Public API:

- :class:`ReviewQueueItem` — ORM model.
- :class:`FindingsBundle` — container for a full set of annotated results.
- :class:`QueueSummary` — aggregate counts from one queue run.
- :func:`build_review_queue` — main service entry point.
- :func:`fetch_queue_items` — filtered retrieval for supervisory display.
- :func:`transition_status` — status lifecycle operations.
- :class:`QueueItemData` — data transfer object for one queue item.
- :class:`InvalidTransitionError` — raised on invalid status transitions.
- :class:`QueueItemNotFoundError` — raised when a queue_id is not found.
- :class:`ReviewStatus` — status lifecycle enum.
- :class:`QueuePriority` — priority enum.
- :class:`FindingCategory` — broad analytical category enum.
"""

from __future__ import annotations

from app.analytics.review_queue.enums import (
    FindingCategory,
    QueuePriority,
    ReviewStatus,
    VALID_TRANSITIONS,
)
from app.analytics.review_queue.models import ReviewQueueItem
from app.analytics.review_queue.priority import derive_priority, finding_category
from app.analytics.review_queue.queue import (
    InvalidTransitionError,
    QueueItemData,
    QueueItemNotFoundError,
    build_queue_item,
    transition_status,
    upsert_queue_item,
)
from app.analytics.review_queue.service import (
    FindingsBundle,
    QueueSummary,
    build_review_queue,
    fetch_queue_items,
)

__all__ = [
    # ORM
    "ReviewQueueItem",
    # Enums
    "ReviewStatus",
    "QueuePriority",
    "FindingCategory",
    "VALID_TRANSITIONS",
    # Priority
    "derive_priority",
    "finding_category",
    # Queue operations
    "QueueItemData",
    "build_queue_item",
    "upsert_queue_item",
    "transition_status",
    "InvalidTransitionError",
    "QueueItemNotFoundError",
    # Service
    "FindingsBundle",
    "QueueSummary",
    "build_review_queue",
    "fetch_queue_items",
]
