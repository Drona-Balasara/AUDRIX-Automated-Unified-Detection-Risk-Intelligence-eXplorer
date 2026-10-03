"""Benchmarkable metrics for peer comparison.

Peer benchmarking compares the *externally reported* performance KPIs
(``performance_metrics``) of an entity-period against those of its genuinely
comparable peers in the same reporting period. Each metric declares its unit so
the 0–1 rate interpretation is preserved and duration/count units are explicit,
and a zero-spread (zero-MAD) absolute-difference threshold so a materially
different value can still be flagged when a tiny peer group has no measurable
dispersion.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import StrEnum


class MetricUnit(StrEnum):
    """Unit/interpretation of a benchmarked metric."""

    RATE = "RATE"  # proportion in [0, 1]
    DURATION_HOURS = "DURATION_HOURS"


@dataclass(frozen=True)
class BenchmarkMetricSpec:
    """One comparable KPI: its source field, unit, and zero-MAD threshold.

    ``zero_mad_abs_threshold`` is the minimum absolute difference from the peer
    median that counts as a deviation **when the peer spread (scaled MAD) is
    zero** — e.g. a single peer, or identical peers. It is expressed in the
    metric's own unit (a proportion for rates, hours for durations).
    """

    name: str
    unit: MetricUnit
    zero_mad_abs_threshold: float
    description: str


# The reported KPIs eligible for peer benchmarking (all of PerformanceMetric's
# numeric measures). Rates share a 0.15 absolute floor; MTTR uses 2 hours.
BENCHMARK_METRICS: tuple[BenchmarkMetricSpec, ...] = (
    BenchmarkMetricSpec("mttr_hours", MetricUnit.DURATION_HOURS, 2.0,
                        "Mean time to resolve, in hours."),
    BenchmarkMetricSpec("closure_rate", MetricUnit.RATE, 0.15,
                        "Share of investigations closed."),
    BenchmarkMetricSpec("sla_compliance", MetricUnit.RATE, 0.15,
                        "Share of work meeting SLA."),
    BenchmarkMetricSpec("escalation_rate", MetricUnit.RATE, 0.15,
                        "Share of work escalated."),
    BenchmarkMetricSpec("investigation_completeness", MetricUnit.RATE, 0.15,
                        "Reported investigation completeness."),
    BenchmarkMetricSpec("recurrence_rate", MetricUnit.RATE, 0.15,
                        "Share of recurring alerts."),
    BenchmarkMetricSpec("remediation_rate", MetricUnit.RATE, 0.15,
                        "Share of issues remediated."),
    BenchmarkMetricSpec("evidence_completeness", MetricUnit.RATE, 0.15,
                        "Reported evidence completeness."),
)

BENCHMARK_METRIC_NAMES: tuple[str, ...] = tuple(m.name for m in BENCHMARK_METRICS)
