"""Structured execution-gap finding schema.

A finding is a conservative, deterministic description of an *observable*
execution deviation in the SOC operational record — never an accusation. The
schema is deliberately shaped so later phases (Evidence, Confidence,
Prioritization, Supervisory Review) can resolve supporting records and group
findings by machine-readable codes without re-parsing human text.

Deliberately NOT present (reserved for later phases): numeric risk/confidence
scores, review-priority fields, and any duplication of the future evidence
model. This phase reports *what was observed*, not how severe or how certain.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StrEnum


class GapType(StrEnum):
    """Family of execution gap. Values intentionally mirror the Phase 2
    ground-truth ``DetectionCategory`` names so later phases and evaluation
    tooling share one vocabulary (the detector never reads ground truth)."""

    ESCALATION_GAP = "ESCALATION_GAP"
    INVESTIGATION_GAP = "INVESTIGATION_GAP"
    REMEDIATION_GAP = "REMEDIATION_GAP"


class ReasonCode(StrEnum):
    """Stable, machine-readable condition code for a specific rule outcome."""

    CRITICAL_ALERT_NO_ESCALATION = "CRITICAL_ALERT_NO_ESCALATION"
    ACKNOWLEDGED_ALERT_NO_INVESTIGATION = "ACKNOWLEDGED_ALERT_NO_INVESTIGATION"
    RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION = "RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION"


class ExecutionGapFinding(BaseModel):
    """One observed execution gap, keyed deterministically for deduplication."""

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
    alert_id: str | None = None
    investigation_id: str | None = None
    related_alert_ids: tuple[str, ...] = Field(default_factory=tuple)
    recurrence_key: str | None = None

    # Observation time / window (timezone-aware UTC, from the domain records).
    observed_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None

    # Human-readable, strictly observational wording.
    summary: str
    expected_condition: str
    observed_condition: str

    # Non-scoring context only; present because it is an observed fact.
    alert_severity: str | None = None
