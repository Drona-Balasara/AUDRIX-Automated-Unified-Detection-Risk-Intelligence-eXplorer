"""Typed configuration for negative-space detection.

All rule thresholds and temporal windows live here as an immutable, documented
dataclass rather than as magic numbers scattered through the rules. This mirrors
the Phase 4 ``ExecutionGapConfig`` and the datagen ``GenerationConfig``
convention, keeping detection strategy separate from the individual analytics.

Choosing the defaults
----------------------
The defaults are calibrated against the deterministic synthetic dataset
(monthly reporting cadence, ~30-day periods, a six-month observed window) and
are deliberately conservative:

- ``monitoring_min_criticality = CRITICAL`` — a *total* missing-telemetry
  finding is only raised for the highest-criticality assets, where an
  expectation of monitoring is strongest. Lower-criticality assets that happen
  to lack telemetry are not treated as gaps by default.
- ``telemetry_gap_threshold_hours = 720`` (~30 days) — trailing silence must
  exceed roughly one full reporting period before a monitored source is treated
  as having disappeared, so merely missing the single most-recent report is not
  flagged.
- ``disappearance_min_baseline_periods = 2`` — an asset must show a meaningful
  prior observation pattern (more than a single isolated record) before its
  later silence can count as a *disappearance* rather than "never monitored".

These values are reasonable for the synthetic/local assessment context only;
production deployments must calibrate them to their own asset inventory and
telemetry cadence.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import Criticality


@dataclass(frozen=True)
class NegativeSpaceConfig:
    """Thresholds and windows governing which absences count as a gap.

    Temporal reasoning is anchored to each entity's ``data_period_start`` /
    ``data_period_end`` and to each asset's own observed telemetry, never to
    wall-clock time, so detection is reproducible.
    """

    # --- Shared monitoring expectation --------------------------------------
    # Only consider assets explicitly flagged as monitoring-expected. When False,
    # the ``monitoring_expected`` column is ignored (not recommended).
    monitoring_require_expected_flag: bool = True

    # --- Missing-telemetry (monitoring coverage) gap, NS-001 ----------------
    # Minimum asset criticality for which a *total* absence of telemetry is
    # treated as a monitoring gap. Default CRITICAL keeps the rule conservative.
    monitoring_min_criticality: Criticality = Criticality.CRITICAL
    # The entity's observed window must be at least this long before a total
    # absence of telemetry is meaningful (guards against trivially short windows).
    monitoring_min_observation_hours: float = 24.0

    # --- Telemetry-disappearance (continuity) gap, NS-002 -------------------
    # Trailing silence (asset's last telemetry -> entity data horizon) must reach
    # this many hours before the source is treated as having disappeared.
    telemetry_gap_threshold_hours: float = 720.0
    # Minimum number of prior telemetry records required to establish a baseline;
    # assets with fewer are considered to have no demonstrated prior visibility
    # and are never classified as a disappearance.
    disappearance_min_baseline_periods: int = 2

    def __post_init__(self) -> None:
        if self.monitoring_min_observation_hours < 0:
            raise ValueError("monitoring_min_observation_hours must be >= 0")
        if self.telemetry_gap_threshold_hours < 0:
            raise ValueError("telemetry_gap_threshold_hours must be >= 0")
        if self.disappearance_min_baseline_periods < 1:
            raise ValueError("disappearance_min_baseline_periods must be >= 1")


# Criticality ordering for threshold comparisons (low -> high).
_CRITICALITY_RANK: dict[str, int] = {
    Criticality.LOW.value: 0,
    Criticality.MEDIUM.value: 1,
    Criticality.HIGH.value: 2,
    Criticality.CRITICAL.value: 3,
}


def criticality_at_least(criticality: str, minimum: Criticality) -> bool:
    """Return True if ``criticality`` ranks at or above ``minimum``."""
    return _CRITICALITY_RANK.get(criticality, -1) >= _CRITICALITY_RANK[minimum.value]
