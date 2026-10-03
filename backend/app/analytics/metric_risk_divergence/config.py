"""Typed configuration for metric-risk divergence detection.

All thresholds and window parameters live here as a frozen, documented
dataclass, mirroring the Phase 4 / Phase 5 / Phase 6 configuration convention.
Callers may pass a customised instance; the defaults below are appropriate for
the six-entity, six-period synthetic dataset and intentionally conservative.

Choosing the defaults
---------------------
The synthetic dataset has 6 entities × 6 monthly periods = 36 entity-period
observations.  With only 6 periods per entity the trend estimates are noisy;
the defaults are deliberately modest so only a clear, sustained divergence is
flagged:

- ``min_observation_periods = 4`` — require at least 4 reporting periods before
  producing a finding.  With 4 points a least-squares slope is meaningful but
  not over-sensitive to a single noisy period.  The value 4 is chosen so that
  the 6-period synthetic scenario (which uses all 6 periods) is always above
  the threshold, while a 3-period or shorter history is considered insufficient.

- ``headline_min_improvement = 0.20`` — the direction-adjusted composite
  headline trend score (dimensionless, roughly in [-1, +1]) must reach at
  least +0.20 to count as a meaningful improvement signal.  The threshold is
  deliberately set above the noise floor observed in the six-entity synthetic
  dataset (where stochastic metric variation reaches ~0.14 on the headline
  composite) so only a clear, sustained improvement registers.

- ``quality_min_deterioration = 0.20`` — the direction-adjusted composite
  quality trend score must reach at least −0.20 (i.e. trend ≤ −0.20) to count
  as a meaningful deterioration signal.  Same rationale as above.

- ``min_trend_magnitude = 0.05`` — below this absolute magnitude an individual
  metric's trend is treated as flat and does not contribute to the composite.
  Prevents tiny floating-point noise from producing artificial directionality.

These defaults are documented limitations: on a 6-period series, slope estimates
have wide uncertainty bands.  The thresholds are intentionally low enough to
detect the clearly planted synthetic scenario while avoiding false positives on
the normal-baseline entities; production deployments serving larger datasets
must recalibrate all four parameters.

Future-leakage prevention
--------------------------
The detector never reads wall-clock time.  Assessment scope is bounded by the
entity's ``data_period_end`` field, so a run is reproducible at any point in
time after ingestion.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricRiskDivergenceConfig:
    """Thresholds and window parameters for divergence detection."""

    # Minimum number of reporting periods required before a finding is emitted.
    # Fewer periods do not provide a reliable trend estimate.
    min_observation_periods: int = 4

    # Headline composite trend score must be >= this value (positive → improving).
    headline_min_improvement: float = 0.20

    # Quality composite trend score must be <= -(this value) (negative → deteriorating).
    quality_min_deterioration: float = 0.20

    # Per-metric trend scores whose absolute value falls below this are treated
    # as flat and excluded from the composite to avoid noise amplification.
    min_trend_magnitude: float = 0.05

    def __post_init__(self) -> None:
        if self.min_observation_periods < 2:
            raise ValueError("min_observation_periods must be >= 2")
        if self.headline_min_improvement <= 0:
            raise ValueError("headline_min_improvement must be > 0")
        if self.quality_min_deterioration <= 0:
            raise ValueError("quality_min_deterioration must be > 0")
        if self.min_trend_magnitude < 0:
            raise ValueError("min_trend_magnitude must be >= 0")
