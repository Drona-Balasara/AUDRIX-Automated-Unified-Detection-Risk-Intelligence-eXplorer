"""Typed configuration for peer benchmarking.

Thresholds and the peer-grouping dimension live here as an immutable dataclass,
mirroring the Phase 4/5 convention.

Choosing the defaults
---------------------
The synthetic dataset carries two real grouping fields: the dedicated
``peer_group`` string (which the generator sets equal to the entity's sector)
and ``scale``. The sector/``peer_group`` field partitions the six entities into
three pairs, so a same-sector comparison has exactly one peer — too few for a
robust median/dispersion estimate under the default ``peer_min_count``. The
``scale`` field puts three entities (ENT-02, ENT-04, ENT-05) in a MEDIUM group,
which does support robust statistics. The default dimension is therefore
``scale``; selecting ``peer_group`` is supported and will correctly report
*insufficient peers* for the pair-sized sector groups.

- ``peer_min_count = 2`` — at least two peers (excluding the entity itself) are
  required before any baseline is computed; otherwise an explicit
  insufficient-peers status is recorded and no finding is emitted.
- ``mad_threshold = 3.5`` — a value is flagged when its distance from the peer
  median exceeds 3.5 scaled MADs. 3.5 is the common modified-z-score cutoff
  (Iglewicz & Hoaglin) for robust outlier screening.
- ``mad_scale_factor = 1.4826`` — makes MAD a consistent estimator of the
  standard deviation under normality; applied before the threshold comparison.

When the scaled MAD is zero (identical peers, including a single peer admitted
under a lowered ``peer_min_count``), the robust ratio is undefined; the engine
falls back to each metric's ``zero_mad_abs_threshold`` absolute-difference rule
rather than dividing by zero or silently hiding the case.

These defaults suit the synthetic/local assessment context; production
deployments must recalibrate the peer dimension, minimum peer count, and
thresholds to their own population.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: SocEntity attributes usable as a peer-grouping dimension.
ALLOWED_PEER_DIMENSIONS: frozenset[str] = frozenset({"scale", "peer_group", "sector"})


@dataclass(frozen=True)
class PeerBenchmarkConfig:
    """Peer-grouping dimension, minimum sample, and deviation thresholds."""

    peer_dimension: str = "scale"
    peer_min_count: int = 2
    mad_threshold: float = 3.5
    mad_scale_factor: float = 1.4826
    # Optional per-metric overrides of the zero-MAD absolute-difference floor.
    abs_threshold_overrides: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.peer_dimension not in ALLOWED_PEER_DIMENSIONS:
            raise ValueError(
                f"peer_dimension must be one of {sorted(ALLOWED_PEER_DIMENSIONS)}"
            )
        if self.peer_min_count < 1:
            raise ValueError("peer_min_count must be >= 1")
        if self.mad_threshold <= 0:
            raise ValueError("mad_threshold must be > 0")
        if self.mad_scale_factor <= 0:
            raise ValueError("mad_scale_factor must be > 0")
        for value in self.abs_threshold_overrides.values():
            if value < 0:
                raise ValueError("abs_threshold_overrides values must be >= 0")
