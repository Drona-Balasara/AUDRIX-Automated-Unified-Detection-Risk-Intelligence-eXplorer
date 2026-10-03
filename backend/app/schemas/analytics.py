"""Pydantic v2 response schemas for the SAT-SA analytics API (Phase 11).

These schemas define the safe, structured shapes returned to API clients.
They are deliberately separate from the internal Pydantic finding models used
by the analytics packages: the API layer controls serialization format,
field visibility, and backward compatibility independently of the analytical
domain models.

Design rules
------------
- Never leak ORM objects, SQLAlchemy row types, or internal model fields.
- Never expose stack traces, SQL queries, filesystem paths, or secrets.
- All datetime fields are ISO-8601 strings (Pydantic serializes datetime → str).
- Paginated responses carry ``total``, ``limit``, ``offset``, and ``items``.
- Finding keys are the stable string IDs already defined in each analytic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pagination envelope
# ---------------------------------------------------------------------------

class PaginatedResponse(BaseModel):
    """Generic pagination envelope for list responses."""

    total: int = Field(description="Total matching items (before pagination).")
    limit: int = Field(description="Page size used.")
    offset: int = Field(description="Page start offset used.")
    items: list[Any] = Field(description="Items on this page.")


# ---------------------------------------------------------------------------
# Entity schemas
# ---------------------------------------------------------------------------

class EntityResponse(BaseModel):
    """Public representation of one SOC entity."""

    entity_id: str
    name: str
    sector: str
    peer_group: str
    scale: str
    asset_count_estimate: int
    analyst_headcount: int
    data_period_start: datetime
    data_period_end: datetime


class EntityListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[EntityResponse]


# ---------------------------------------------------------------------------
# Evidence reference (safe, flat representation)
# ---------------------------------------------------------------------------

class EvidenceRefResponse(BaseModel):
    """One evidence reference supporting a finding."""

    source_type: str = Field(description="Type of the cited source record.")
    source_id: str = Field(description="Stable identifier for the source record.")
    role: str = Field(description="Role of this record relative to the finding.")
    period_label: str | None = Field(default=None, description="YYYY-MM period label.")
    reason: str = Field(description="Concise explanation of relevance.")


class FindingEvidenceResponse(BaseModel):
    """Evidence and confidence for one finding."""

    finding_key: str
    analytic_id: str
    confidence: str = Field(description="HIGH, MODERATE, or LOW.")
    confidence_note: str
    confidence_factors: list[str]
    evidence_count: int
    evidence_refs: list[EvidenceRefResponse]


# ---------------------------------------------------------------------------
# Generic finding (unified representation for the findings list)
# ---------------------------------------------------------------------------

class FindingResponse(BaseModel):
    """Unified representation of a finding from any Phase 4–8 analytic.

    Preserves the original ``finding_key`` and ``analytic_id`` so clients
    can always trace back to the originating analytic.  ``finding_type``
    and ``details`` carry analytic-specific fields without flattening
    them into ambiguous generic columns.
    """

    finding_key: str = Field(description="Stable deterministic finding identifier.")
    analytic_id: str = Field(description="Analytic that produced this finding.")
    finding_type: str = Field(description="Finding type / reason code.")
    category: str = Field(description="Broad category (EXECUTION_GAP, ANOMALY, etc.).")
    entity_id: str
    period_label: str = Field(description="YYYY-MM of the primary reporting period.")
    summary: str = Field(description="Neutral human-readable description.")
    # Confidence snapshot from Phase 9 evidence layer (may be None if evidence
    # was not pre-computed during this response).
    confidence: str | None = Field(default=None)
    evidence_count: int = Field(default=0)
    # Analytic-specific extra fields (read-only; not parsed by the client schema).
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Analytic-specific supplementary fields.",
    )


class FindingListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FindingResponse]


class FindingDetailResponse(BaseModel):
    """One finding with full evidence and confidence."""

    finding: FindingResponse
    evidence: FindingEvidenceResponse | None = None


# ---------------------------------------------------------------------------
# Assessment run
# ---------------------------------------------------------------------------

class AssessmentAnalyticSummary(BaseModel):
    """Counts for one analytic component of an assessment run."""

    analytic_id: str
    finding_count: int
    status: str = Field(description="'ok' or analytic-specific status string.")


class AssessmentRunResponse(BaseModel):
    """Result of a full analytical assessment run.

    Running an assessment executes all Phase 4–8 analytics against the current
    database contents, builds evidence/confidence (Phase 9), populates the
    supervisory review queue (Phase 10), and returns aggregate counts.
    No analytical findings are permanently stored — they are derived on demand.
    The review queue IS persisted.
    """

    entity_count: int
    total_findings: int
    analytics: list[AssessmentAnalyticSummary]
    queue_items_total: int
    queue_inserted: int
    queue_unchanged: int
    duration_ms: float
