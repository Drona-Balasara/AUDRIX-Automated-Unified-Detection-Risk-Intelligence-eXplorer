"""Anomaly detection (Phase 6).

A deterministic, read-only, unsupervised anomaly detector over entity-period
observations. Features are built from raw operational records (never from
reported KPIs or ground truth), and a scikit-learn Isolation Forest with a
fixed random state flags a small minority of observations as unusual relative
to the modeled population. The analytic emits an anomaly *signal* and a binary
decision — not a risk or confidence score.

Public API:

- :func:`run_anomaly_detection` — service entry point (FastAPI-independent).
- :class:`AnomalyConfig` — typed, immutable model configuration.
- :class:`AnomalyResult`, :class:`AnomalyStatus` — structured outcome.
- :class:`AnomalyFinding`, :class:`AnomalyDecision`, :class:`ModelMetadata` —
  finding schema.
- :data:`FEATURE_SCHEMA` — the immutable feature definitions.
"""

from __future__ import annotations

from app.analytics.anomaly.config import MODEL_VERSION, AnomalyConfig
from app.analytics.anomaly.engine import (
    AnomalyResult,
    AnomalyStatus,
    detect_anomalies,
)
from app.analytics.anomaly.features import (
    ALL_FEATURES,
    ELIGIBLE_FEATURES,
    FEATURE_SCHEMA,
    FeatureSpec,
    FeatureUnit,
)
from app.analytics.anomaly.findings import (
    ANALYTIC_ID,
    AnomalyDecision,
    AnomalyFinding,
    ModelMetadata,
    NotableFeature,
)
from app.analytics.anomaly.service import run_anomaly_detection

__all__ = [
    "run_anomaly_detection",
    "detect_anomalies",
    "AnomalyConfig",
    "MODEL_VERSION",
    "AnomalyResult",
    "AnomalyStatus",
    "AnomalyFinding",
    "AnomalyDecision",
    "ModelMetadata",
    "NotableFeature",
    "FEATURE_SCHEMA",
    "FeatureSpec",
    "FeatureUnit",
    "ALL_FEATURES",
    "ELIGIBLE_FEATURES",
    "ANALYTIC_ID",
]
