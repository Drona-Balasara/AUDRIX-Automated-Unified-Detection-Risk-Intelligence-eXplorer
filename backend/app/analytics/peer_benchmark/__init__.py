"""Peer benchmarking (Phase 6).

A deterministic, read-only analytic that compares an entity-period's reported
performance KPIs against the robust baseline (median / scaled MAD) of its
genuinely comparable peers in the same reporting period. It never compares an
entity against itself, never mixes incomparable peer groups, and emits an
explicit insufficient-peers status rather than a misleading deviation when too
few comparable peers are present. Findings are observational, per metric, and
carry no performance verdict or risk score.

Public API:

- :func:`run_peer_benchmark` — service entry point (FastAPI-independent).
- :class:`PeerBenchmarkConfig` — typed, immutable configuration.
- :class:`PeerBenchmarkResult` — structured outcome.
- :class:`PeerDeviationFinding`, :class:`BenchmarkStatus`,
  :class:`DeviationDirection`, :class:`DeviationBasis` — finding schema.
- :data:`BENCHMARK_METRICS` — the benchmarkable KPI definitions.
"""

from __future__ import annotations

from app.analytics.peer_benchmark.config import (
    ALLOWED_PEER_DIMENSIONS,
    PeerBenchmarkConfig,
)
from app.analytics.peer_benchmark.engine import PeerBenchmarkResult, run_benchmark
from app.analytics.peer_benchmark.findings import (
    ANALYTIC_ID,
    BenchmarkStatus,
    BenchmarkStatusCode,
    DeviationBasis,
    DeviationDirection,
    PeerDeviationFinding,
)
from app.analytics.peer_benchmark.metrics import (
    BENCHMARK_METRIC_NAMES,
    BENCHMARK_METRICS,
    BenchmarkMetricSpec,
    MetricUnit,
)
from app.analytics.peer_benchmark.service import run_peer_benchmark

__all__ = [
    "run_peer_benchmark",
    "run_benchmark",
    "PeerBenchmarkConfig",
    "ALLOWED_PEER_DIMENSIONS",
    "PeerBenchmarkResult",
    "PeerDeviationFinding",
    "BenchmarkStatus",
    "BenchmarkStatusCode",
    "DeviationDirection",
    "DeviationBasis",
    "BENCHMARK_METRICS",
    "BENCHMARK_METRIC_NAMES",
    "BenchmarkMetricSpec",
    "MetricUnit",
    "ANALYTIC_ID",
]
