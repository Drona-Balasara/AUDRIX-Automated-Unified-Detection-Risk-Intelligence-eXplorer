"""Structured negative-space finding schema.

A negative-space finding is a conservative, deterministic description of an
*observable* absence of expected evidence in the SOC operational record — for
example, a critical monitored asset that produced no telemetry at all, or a
monitored asset whose telemetry stopped partway through the observed window.

The central discipline of this phase is that *absence is not automatically
evidence of failure*. A finding is only emitted when existing data establishes
that a signal was reasonably expected during a defined window. The wording is
strictly observational: it describes a *potential* monitoring or continuity
gap and never asserts that telemetry was intentionally disabled, tampered with,
or maliciously suppressed.

Deliberately NOT present (reserved for later phases): numeric risk/confidence
scores, review-priority fields, and any duplication of the future evidence
model. This phase reports *what was observed*, not how severe or how certain.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StrEnum


class GapType(StrEnum):
    """Family of negative-space gap. Values intentionally mirror the Phase 2
    ground-truth ``DetectionCategory`` names so later phases and evaluation
    tooling share one vocabulary (the detector never reads ground truth)."""

    MONITORING_GAP = "MONITORING_GAP"
    TELEMETRY_DISAPPEARANCE = "TELEMETRY_DISAPPEARANCE"


class ReasonCode(StrEnum):
    """Stable, machine-readable condition code for a specific rule outcome."""

    CRITICAL_ASSET_NO_TELEMETRY = "CRITICAL_ASSET_NO_TELEMETRY"
    TELEMETRY_CONTINUITY_GAP = "TELEMETRY_CONTINUITY_GAP"


class NegativeSpaceFinding(BaseModel):
    """One observed negative-space gap, keyed deterministically for dedup."""

    model_config = ConfigDict(frozen=True)

    # Deterministic identity: identical database state + config always yields the
    # same key for the same underlying condition. No random UUIDs.
    finding_key: str
    rule_id: str
    gap_type: GapType
    reason_code: ReasonCode

    # Scope + stable record references for later evidence resolution.
    entity_id: str
    asset_id: str | None = None
    # Stable references to the source telemetry rows that establish the observed
    # baseline / boundary (empty when the expectation is schema-derived, as for a
    # total monitoring gap where no telemetry exists at all).
    related_telemetry_ids: tuple[str, ...] = Field(default_factory=tuple)

    # Observation window (timezone-aware UTC, anchored to entity data-period
    # bounds or to the asset's own last-observed telemetry — never wall-clock).
    observation_start: datetime | None = None
    observation_end: datetime | None = None
    # For continuity gaps: the asset's last observed telemetry boundary.
    last_evidence_at: datetime | None = None

    # Human-readable, strictly observational wording.
    summary: str
    expected_evidence: str
    observed_evidence: str

    # Non-scoring observed context only (facts read from the records, not scores).
    asset_criticality: str | None = None
    expected_telemetry: str | None = None
    baseline_observation_count: int | None = None
    silence_hours: float | None = None
