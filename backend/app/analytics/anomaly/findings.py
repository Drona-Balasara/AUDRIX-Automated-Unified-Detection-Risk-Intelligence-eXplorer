"""Structured anomaly-finding schema.

An anomaly finding is a deterministic, strictly *analytical* statement that one
entity-period observation is unusual relative to the modeled population of
entity-period observations. It is **not** a risk score, a confidence score, or
a judgement of misconduct: the wording never asserts policy violation, malice,
manipulation, or root cause. Converting an anomaly signal into a supervisory
risk judgement is reserved for later phases.

The model emits a raw anomaly score (the Isolation Forest ``score_samples``
value, where higher means *more normal*) and a normalized anomaly signal in
[0, 1] (higher means *more anomalous*), together with a binary decision. Both
are retained so downstream phases can see the underlying signal, not only the
thresholded decision.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StrEnum

#: Stable identifier for the anomaly analytic (one analytic in Phase 6).
ANALYTIC_ID = "AN-001"


class AnomalyDecision(StrEnum):
    """Binary, thresholded decision from the detector."""

    ANOMALOUS = "ANOMALOUS"
    NOT_ANOMALOUS = "NOT_ANOMALOUS"


class NotableFeature(BaseModel):
    """A feature whose value is among the farthest from the population median.

    Descriptive context only (a fact about where the value sits relative to the
    population), never a contribution score or risk weight.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    value: float
    population_median: float


class ModelMetadata(BaseModel):
    """Deterministic, safe description of the fitted model and population.

    Carries no model binary and no raw operational rows — only reproducibility
    metadata (feature names, hyperparameters, population sizes, random state).
    """

    model_config = ConfigDict(frozen=True)

    model_version: str
    feature_names: tuple[str, ...]
    n_estimators: int
    max_samples: str
    contamination: float
    random_state: int
    training_observation_count: int
    scored_observation_count: int


class AnomalyFinding(BaseModel):
    """One entity-period flagged as anomalous, keyed deterministically."""

    model_config = ConfigDict(frozen=True)

    # Deterministic identity from analytic id + entity + period (no UUIDs).
    finding_key: str
    analytic_id: str = ANALYTIC_ID

    entity_id: str
    period_start: datetime
    period_end: datetime

    decision: AnomalyDecision
    # Raw Isolation Forest score (higher = more normal); documented orientation.
    raw_anomaly_score: float
    # Normalized anomaly signal in [0, 1] (higher = more anomalous).
    normalized_anomaly_score: float

    # Compact, safe snapshot of the model-eligible feature values that produced
    # the decision (facts read from the records, not scores).
    feature_snapshot: dict[str, float] = Field(default_factory=dict)
    notable_features: tuple[NotableFeature, ...] = Field(default_factory=tuple)

    # Human-readable, strictly observational wording.
    summary: str
