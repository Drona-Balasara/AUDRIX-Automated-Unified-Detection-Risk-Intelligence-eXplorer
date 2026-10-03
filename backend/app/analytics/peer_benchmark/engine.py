"""Peer-benchmarking engine.

For each eligible entity-period and each benchmarkable metric, the engine
compares the entity's reported value against the robust baseline (median) of its
genuinely comparable peers — same peer-dimension value, same reporting period,
with the entity's own value excluded. Dispersion is measured with the median
absolute deviation (MAD); a value is flagged when it lies more than the
configured number of scaled MADs from the peer median. When the peer spread is
zero (identical peers, or a single peer under a lowered minimum), the engine
falls back to a documented per-metric absolute-difference floor.

The engine is pure and side-effect free: identical records and configuration
yield an equal result. It reads no wall-clock time and performs no I/O.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.peer_benchmark.config import PeerBenchmarkConfig
from app.analytics.peer_benchmark.context import BenchmarkContext
from app.analytics.peer_benchmark.findings import (
    ANALYTIC_ID,
    BenchmarkStatus,
    BenchmarkStatusCode,
    DeviationBasis,
    DeviationDirection,
    PeerDeviationFinding,
)
from app.analytics.peer_benchmark.metrics import BENCHMARK_METRICS


class PeerBenchmarkResult(BaseModel):
    """Structured, deterministic outcome of one benchmarking run."""

    model_config = ConfigDict(frozen=True)

    analytic_id: str = ANALYTIC_ID
    peer_dimension: str = ""
    entity_ids: tuple[str, ...] = Field(default_factory=tuple)
    findings: tuple[PeerDeviationFinding, ...] = Field(default_factory=tuple)
    statuses: tuple[BenchmarkStatus, ...] = Field(default_factory=tuple)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    def counts_by_metric(self) -> dict[str, int]:
        return dict(Counter(f.metric_name for f in self.findings))


def _scaled_mad(values: list[float], median: float, scale: float) -> float:
    """Scaled median absolute deviation about ``median``."""
    deviations = [abs(v - median) for v in values]
    return scale * statistics.median(deviations)


def run_benchmark(
    ctx: BenchmarkContext, config: PeerBenchmarkConfig
) -> PeerBenchmarkResult:
    """Compute peer-relative deviations for the scope in ``ctx``."""
    # Group entity ids by the configured peer dimension (deterministic order).
    groups: dict[str, list[str]] = defaultdict(list)
    for entity_id in ctx.entity_ids():
        groups[ctx.group_value(entity_id, config.peer_dimension)].append(entity_id)

    findings: list[PeerDeviationFinding] = []
    statuses: list[BenchmarkStatus] = []

    for entity_id in ctx.entity_ids():
        group_value = ctx.group_value(entity_id, config.peer_dimension)
        peers = [p for p in groups[group_value] if p != entity_id]

        for start in ctx.period_starts:
            end = ctx.period_end_by_start[start]
            self_metric = ctx.metrics.get((entity_id, start))
            if self_metric is None:
                continue

            # Peers present in this exact reporting period (self excluded).
            present_peers = [
                p for p in peers if (p, start) in ctx.metrics
            ]
            if len(present_peers) < config.peer_min_count:
                statuses.append(
                    BenchmarkStatus(
                        status=BenchmarkStatusCode.INSUFFICIENT_PEERS,
                        entity_id=entity_id,
                        period_start=start,
                        period_end=end,
                        peer_dimension=config.peer_dimension,
                        peer_group=group_value,
                        peer_population_count=len(present_peers),
                    )
                )
                continue

            for spec in BENCHMARK_METRICS:
                self_value = getattr(self_metric, spec.name)
                if self_value is None:
                    continue
                peer_values = [
                    getattr(ctx.metrics[(p, start)], spec.name)
                    for p in present_peers
                    if getattr(ctx.metrics[(p, start)], spec.name) is not None
                ]
                if len(peer_values) < config.peer_min_count:
                    continue

                finding = _evaluate_metric(
                    entity_id=entity_id,
                    start=start,
                    end=end,
                    group_value=group_value,
                    spec=spec,
                    self_value=float(self_value),
                    peer_values=[float(v) for v in peer_values],
                    config=config,
                )
                if finding is not None:
                    findings.append(finding)

    findings.sort(key=lambda f: f.finding_key)
    statuses.sort(key=lambda s: (s.entity_id, s.period_start))

    return PeerBenchmarkResult(
        peer_dimension=config.peer_dimension,
        entity_ids=tuple(ctx.entity_ids()),
        findings=tuple(findings),
        statuses=tuple(statuses),
    )


def _evaluate_metric(
    *,
    entity_id: str,
    start,
    end,
    group_value: str,
    spec,
    self_value: float,
    peer_values: list[float],
    config: PeerBenchmarkConfig,
) -> PeerDeviationFinding | None:
    """Return a finding when ``self_value`` deviates materially from peers."""
    median = statistics.median(peer_values)
    scaled_mad = _scaled_mad(peer_values, median, config.mad_scale_factor)
    abs_diff = abs(self_value - median)

    if scaled_mad == 0.0:
        # No measurable peer spread: fall back to the absolute-difference floor.
        threshold = config.abs_threshold_overrides.get(
            spec.name, spec.zero_mad_abs_threshold
        )
        if abs_diff < threshold:
            return None
        basis = DeviationBasis.ABSOLUTE_ZERO_MAD
        measure = abs_diff
    else:
        distance = abs_diff / scaled_mad
        if distance < config.mad_threshold:
            return None
        basis = DeviationBasis.ROBUST_MAD
        measure = distance

    direction = (
        DeviationDirection.ABOVE
        if self_value > median
        else DeviationDirection.BELOW
    )
    period_label = start.strftime("%Y-%m")
    summary = (
        f"Entity {entity_id} reported {spec.name}={self_value:.3g} in period "
        f"{period_label}, materially {direction.value.lower()} the comparable "
        f"peer baseline of {median:.3g} ({len(peer_values)} peers, "
        f"{config.peer_dimension}={group_value}). Observational benchmark "
        f"signal for supervisory review, not a performance verdict."
    )
    return PeerDeviationFinding(
        finding_key=f"{ANALYTIC_ID}:{entity_id}:{period_label}:{spec.name}",
        entity_id=entity_id,
        period_start=start,
        period_end=end,
        metric_name=spec.name,
        metric_unit=spec.unit.value,
        entity_value=self_value,
        peer_baseline=median,
        peer_population_count=len(peer_values),
        peer_group=group_value,
        deviation_measure=measure,
        deviation_basis=basis,
        direction=direction,
        summary=summary,
    )
