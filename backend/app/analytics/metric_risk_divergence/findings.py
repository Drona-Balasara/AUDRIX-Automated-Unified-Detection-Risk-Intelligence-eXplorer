"""Structured metric-risk-divergence finding schema.

A divergence finding is a deterministic, strictly observational statement that,
for a given entity over a defined observation window, headline performance
metrics show an improving trend while operational-quality indicators show a
deteriorating trend — a pattern that warrants human supervisory review.

Language discipline
-------------------
The wording is *neutral and factual*: findings describe *what was observed*
(metric values, trend direction, period count) and never attribute cause, intent,
or blame.  In particular, this analytic:

- Does NOT accuse any SOC, analyst, or manager of manipulating metrics.
- Does NOT assert fraud, misconduct, deliberate gaming, or data falsification.
- Does NOT make a risk verdict; it surfaces a pattern for a human reviewer.

The term used is "Potential Metric-Risk Divergence" — the word "potential"
acknowledges that legitimate operational explanations exist and that human
judgement is required.

Confidence levels
-----------------
Confidence is a data-quality / observational-sufficiency indicator, NOT a risk
or severity score:

- HIGH    — ≥ 5 periods AND both composite scores are at least 2× the minimum
            threshold, indicating a clear, sustained signal.
- MODERATE — ≥ 4 periods AND at least one composite score exceeds 1× threshold.
- LOW     — minimum threshold met but fewer supporting periods or marginal trend
            magnitudes; the signal exists but the evidence base is thin.

Risk/severity is intentionally absent from this finding: assigning risk requires
contextual judgement that is reserved for later phases.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StrEnum

#: Stable analytic identifier for metric-risk divergence.
ANALYTIC_ID = "MRD-001"


class ConfidenceLevel(StrEnum):
    """Data-quality / observational-sufficiency indicator.

    Not a risk or severity score.  Reflects how much evidence supports the
    divergence observation, not how serious the divergence is.
    """

    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"


class MetricSnapshot(BaseModel):
    """Period-by-period values for one tracked metric.

    Carries only facts read from the database — never inferred scores or model
    outputs.  Used by supervisory reviewers to see the raw trajectory.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    group: str           # "HEADLINE" or "QUALITY"
    higher_is_better: bool
    # Ordered list of (period_label, value) pairs, earliest period first.
    # period_label is "YYYY-MM" derived from period_start.
    period_values: tuple[tuple[str, float], ...] = Field(default_factory=tuple)
    # Direction-adjusted trend score for this individual metric.
    # Positive = improving in its declared direction, negative = deteriorating.
    # None when fewer than min_observation_periods are available.
    trend_score: float | None = None


class MetricRiskDivergenceFinding(BaseModel):
    """One entity-level divergence pattern, keyed deterministically."""

    model_config = ConfigDict(frozen=True)

    # Deterministic identity: analytic id + entity + window bounds (no UUIDs).
    # window_start and window_end are YYYY-MM labels of first and last period.
    finding_key: str
    analytic_id: str = ANALYTIC_ID

    entity_id: str

    # Observation window: the first and last reporting periods included in the
    # trend calculation (timezone-aware UTC datetimes from the database).
    window_start: datetime
    window_end: datetime
    # Number of reporting periods that contributed to this finding.
    supporting_period_count: int

    # Composite trend scores (direction-adjusted, dimensionless).
    # Positive → improving; negative → deteriorating.
    headline_trend_score: float
    quality_trend_score: float

    # Per-metric snapshots (facts, not scores) for supervisory review.
    headline_snapshots: tuple[MetricSnapshot, ...] = Field(default_factory=tuple)
    quality_snapshots: tuple[MetricSnapshot, ...] = Field(default_factory=tuple)

    # Data-quality confidence (not a risk score — see module docstring).
    confidence: ConfidenceLevel

    # Human-readable, strictly observational wording.
    summary: str
    # One-line note on confidence rationale (data sufficiency, trend stability).
    confidence_note: str
