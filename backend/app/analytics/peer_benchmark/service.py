"""Public service boundary for peer benchmarking.

Programmatic entry point, independent of FastAPI: it loads a read-only context
from a SQLAlchemy session, computes peer-relative deviations, and returns the
structured :class:`PeerBenchmarkResult`.

Logging emits only safe operational metadata — analytic id, peer dimension,
entity-scope size, peer-group counts, finding counts, insufficient-peer counts,
and run duration. It never logs reported KPI values, operational rows,
credentials, or any sensitive record data.
"""

from __future__ import annotations

from collections import Counter
from time import perf_counter

from sqlalchemy.orm import Session

from app.analytics.peer_benchmark.config import PeerBenchmarkConfig
from app.analytics.peer_benchmark.context import load_context
from app.analytics.peer_benchmark.engine import PeerBenchmarkResult, run_benchmark
from app.analytics.peer_benchmark.findings import ANALYTIC_ID
from app.core.logging import get_logger

logger = get_logger(__name__)


def run_peer_benchmark(
    session: Session,
    config: PeerBenchmarkConfig | None = None,
    entity_ids: list[str] | None = None,
) -> PeerBenchmarkResult:
    """Run peer benchmarking over the given (or all) entities.

    Deterministic and strictly read-only: identical database state and config
    always yield an equal result, and no source records are mutated.
    """
    config = config or PeerBenchmarkConfig()

    start = perf_counter()
    ctx = load_context(session, entity_ids=entity_ids)
    result = run_benchmark(ctx, config)
    duration_ms = (perf_counter() - start) * 1000.0

    group_sizes = Counter(
        ctx.group_value(eid, config.peer_dimension) for eid in ctx.entity_ids()
    )
    logger.info(
        "peer benchmark complete: analytic=%s dimension=%s entities=%d "
        "groups=%d findings=%d insufficient_peer_statuses=%d by_metric=%s "
        "duration_ms=%.1f",
        ANALYTIC_ID,
        config.peer_dimension,
        len(result.entity_ids),
        len(group_sizes),
        result.finding_count,
        len(result.statuses),
        result.counts_by_metric(),
        duration_ms,
    )
    return result
