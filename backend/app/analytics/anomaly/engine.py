"""Anomaly-detection engine.

The engine builds the deterministic feature matrix from a pre-loaded context,
fits a scikit-learn :class:`~sklearn.ensemble.IsolationForest` over the
model-eligible observations, and returns structured, deterministically ordered
findings for the observations the model flags as anomalous.

Determinism
-----------
- The random state is fixed by configuration; no wall-clock time is read.
- Observations are built and scored in a fixed (entity_id, period) order.
- The normalized anomaly signal is a min-max transform over the scored
  population, so it depends only on the input records and configuration.

Safeguards
----------
- Observations missing any model-eligible feature are dropped (not imputed) and
  counted; the remaining *complete* observations form the training/scoring
  population.
- If fewer than ``min_training_samples`` complete observations exist, the engine
  returns an explicit INSUFFICIENT_SAMPLE result rather than fitting an
  unstable model.
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sklearn.ensemble import IsolationForest

from app.analytics.anomaly.config import AnomalyConfig
from app.analytics.anomaly.context import AnomalyContext, build_observations
from app.analytics.anomaly.features import ELIGIBLE_FEATURES, EntityPeriodObservation
from app.analytics.anomaly.findings import (
    ANALYTIC_ID,
    AnomalyDecision,
    AnomalyFinding,
    ModelMetadata,
    NotableFeature,
)
from app.models.enums import StrEnum


class AnomalyStatus(StrEnum):
    """Outcome status of a detection run."""

    OK = "OK"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"


class AnomalyResult(BaseModel):
    """Structured, deterministic outcome of one anomaly-detection run.

    Carries no wall-clock timestamp: identical records and configuration yield
    an equal result. ``model_metadata`` is ``None`` when the run was skipped for
    insufficient sample.
    """

    model_config = ConfigDict(frozen=True)

    status: AnomalyStatus
    entity_ids: tuple[str, ...] = Field(default_factory=tuple)
    observation_count: int = 0
    scored_observation_count: int = 0
    dropped_incomplete_count: int = 0
    model_metadata: ModelMetadata | None = None
    findings: tuple[AnomalyFinding, ...] = Field(default_factory=tuple)

    @property
    def finding_count(self) -> int:
        return len(self.findings)


def _normalized_signals(raw_scores: np.ndarray) -> np.ndarray:
    """Map raw scores (higher = normal) to [0, 1] (higher = anomalous)."""
    magnitude = -raw_scores
    low, high = float(magnitude.min()), float(magnitude.max())
    if high == low:
        return np.zeros_like(magnitude)
    return (magnitude - low) / (high - low)


def _notable_features(
    values: dict[str, float | None],
    medians: dict[str, float],
    iqrs: dict[str, float],
    limit: int = 3,
) -> tuple[NotableFeature, ...]:
    """The features whose values sit farthest from the population median.

    Deviation is normalized by the population inter-quartile range so features
    on different scales are comparable; ties break by feature name for stable,
    deterministic output. This is descriptive context, not a contribution score.
    """
    ranked: list[tuple[float, str]] = []
    for name in ELIGIBLE_FEATURES:
        value = values[name]
        assert value is not None  # complete observations only
        spread = iqrs[name] if iqrs[name] > 0 else 1.0
        ranked.append((abs(value - medians[name]) / spread, name))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return tuple(
        NotableFeature(
            name=name,
            value=float(values[name]),  # type: ignore[arg-type]
            population_median=medians[name],
        )
        for _, name in ranked[:limit]
    )


def detect_anomalies(
    ctx: AnomalyContext, config: AnomalyConfig
) -> AnomalyResult:
    """Fit the detector and return structured findings for the scope in ``ctx``."""
    entity_ids = tuple(ctx.entity_ids())
    observations = build_observations(ctx)
    complete = [o for o in observations if o.is_model_complete()]
    dropped = len(observations) - len(complete)

    if len(complete) < config.min_training_samples:
        return AnomalyResult(
            status=AnomalyStatus.INSUFFICIENT_SAMPLE,
            entity_ids=entity_ids,
            observation_count=len(observations),
            scored_observation_count=len(complete),
            dropped_incomplete_count=dropped,
        )

    matrix = np.array([o.eligible_vector() for o in complete], dtype=float)

    model = IsolationForest(
        n_estimators=config.n_estimators,
        max_samples=config.max_samples,
        contamination=config.contamination,
        random_state=config.random_state,
        bootstrap=False,
    )
    model.fit(matrix)
    predictions = model.predict(matrix)
    raw_scores = model.score_samples(matrix)
    normalized = _normalized_signals(raw_scores)

    medians = {
        name: float(np.median(matrix[:, idx]))
        for idx, name in enumerate(ELIGIBLE_FEATURES)
    }
    iqrs = {
        name: float(
            np.percentile(matrix[:, idx], 75) - np.percentile(matrix[:, idx], 25)
        )
        for idx, name in enumerate(ELIGIBLE_FEATURES)
    }

    metadata = ModelMetadata(
        model_version=config.model_version,
        feature_names=ELIGIBLE_FEATURES,
        n_estimators=config.n_estimators,
        max_samples=str(config.max_samples),
        contamination=float(config.contamination),
        random_state=config.random_state,
        training_observation_count=len(complete),
        scored_observation_count=len(complete),
    )

    findings: list[AnomalyFinding] = []
    for obs, pred, raw, norm in zip(complete, predictions, raw_scores, normalized):
        if pred != -1:
            continue
        findings.append(_build_finding(obs, float(raw), float(norm), medians, iqrs, len(complete)))

    findings.sort(key=lambda f: f.finding_key)

    return AnomalyResult(
        status=AnomalyStatus.OK,
        entity_ids=entity_ids,
        observation_count=len(observations),
        scored_observation_count=len(complete),
        dropped_incomplete_count=dropped,
        model_metadata=metadata,
        findings=tuple(findings),
    )


def _build_finding(
    obs: EntityPeriodObservation,
    raw: float,
    norm: float,
    medians: dict[str, float],
    iqrs: dict[str, float],
    population: int,
) -> AnomalyFinding:
    period_label = obs.period_start.strftime("%Y-%m")  # type: ignore[attr-defined]
    snapshot = {
        name: float(obs.values[name])  # type: ignore[arg-type]
        for name in ELIGIBLE_FEATURES
    }
    summary = (
        f"Entity {obs.entity_id} in reporting period {period_label} is unusual "
        f"relative to the modeled population of {population} entity-period "
        f"observations (normalized anomaly signal {norm:.2f}). This is an "
        f"analytical signal for supervisory review, not a risk judgement."
    )
    return AnomalyFinding(
        finding_key=f"{ANALYTIC_ID}:{obs.entity_id}:{period_label}",
        entity_id=obs.entity_id,
        period_start=obs.period_start,  # type: ignore[arg-type]
        period_end=obs.period_end,  # type: ignore[arg-type]
        decision=AnomalyDecision.ANOMALOUS,
        raw_anomaly_score=raw,
        normalized_anomaly_score=norm,
        feature_snapshot=snapshot,
        notable_features=_notable_features(obs.values, medians, iqrs),
        summary=summary,
    )
