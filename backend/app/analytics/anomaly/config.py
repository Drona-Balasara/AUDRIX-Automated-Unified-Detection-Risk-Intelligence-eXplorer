"""Typed configuration for anomaly detection.

All model hyperparameters and safeguards live here as an immutable, documented
dataclass rather than as magic numbers inside the engine, mirroring the Phase 4
and Phase 5 config convention.

Choosing the defaults
---------------------
The defaults target the deterministic synthetic dataset (36 entity-period
observations, nine model-eligible features) and the goal of a *conservative*
unsupervised detector that surfaces a small minority of unusual observations
without flooding the normal baseline.

- ``n_estimators = 200`` — enough trees for a stable averaged path length on a
  small sample; cheap at this scale.
- ``max_samples = "auto"`` — scikit-learn uses ``min(256, n_samples)``; with 36
  observations every tree sees the whole sample, which is appropriate here.
- ``contamination = 0.1`` — the detector flags roughly a tenth of observations.
  This encodes the expectation that only a small minority of entity-periods are
  operationally unusual; it is **not** tuned to detect any planted scenario.
  Over 36 observations it yields ~3-4 flags, well under a 10% share of assets
  and far from flooding. Production deployments must recalibrate it.
- ``random_state = 20240601`` — fixed (matches the dataset seed) so repeated
  runs over identical data produce byte-identical scores and decisions. The
  detector never reads wall-clock time.
- ``min_training_samples = 20`` — below this many *complete* observations the
  forest is too unstable to fit responsibly; the engine returns an explicit
  INSUFFICIENT_SAMPLE result instead of fabricating findings.

Isolation Forest is tree-based and splits on one feature at a time, so it is
invariant to per-feature scale; no standardization is applied (and none is
needed). This is a deliberate choice, not an omission.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Model version id recorded on every result for reproducibility/traceability.
MODEL_VERSION = "anomaly-iforest-v1"


@dataclass(frozen=True)
class AnomalyConfig:
    """Isolation Forest hyperparameters and sample-size safeguards."""

    n_estimators: int = 200
    max_samples: int | str = "auto"
    contamination: float = 0.1
    random_state: int = 20240601
    min_training_samples: int = 20
    model_version: str = MODEL_VERSION

    def __post_init__(self) -> None:
        if self.n_estimators < 1:
            raise ValueError("n_estimators must be >= 1")
        if not (0.0 < self.contamination <= 0.5):
            raise ValueError("contamination must be in (0, 0.5]")
        if self.min_training_samples < 1:
            raise ValueError("min_training_samples must be >= 1")
        if isinstance(self.max_samples, int) and self.max_samples < 1:
            raise ValueError("max_samples must be >= 1 when given as an int")
