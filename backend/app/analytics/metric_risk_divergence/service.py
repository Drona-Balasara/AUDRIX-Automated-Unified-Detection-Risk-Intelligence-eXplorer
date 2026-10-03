"""Public service boundary for metric-risk divergence detection.

Programmatic entry point, independent of FastAPI: it loads a read-only context
from a SQLAlchemy session, runs the divergence detector, and returns the
structured :class:`MetricRiskDivergenceResult`.  It can be called from a
script, a test, or (later) a thin HTTP route.

Logging emits only safe operational metadata — analytic id, entity count,
period counts, finding count, skipped entity count, and run duration.  It
never logs metric values, operational rows, credentials, or any sensitive
record data.
"""

from __future__ import annotations

from time import perf_counter

from sqlalchemy.orm import Session

from app.analytics.metric_risk_divergence.config import MetricRiskDivergenceConfig
from app.analytics.metric_risk_divergence.context import load_context
from app.analytics.metric_risk_divergence.engine import (
    MetricRiskDivergenceResult,
    run_divergence_detection,
)
from app.analytics.metric_risk_divergence.findings import ANALYTIC_ID
from app.analytics.metric_risk_divergence.metrics import METRIC_REGISTRY
from app.core.logging import get_logger

logger = get_logger(__name__)


def run_metric_risk_divergence(
    session: Session,
    config: MetricRiskDivergenceConfig | None = None,
    entity_ids: list[str] | None = None,
) -> MetricRiskDivergenceResult:
    """Run metric-risk divergence detection over the given (or all) entities.

    Deterministic and strictly read-only: identical database state and config
    always yield an equal result, and no source records are mutated.

    Parameters
    ----------
    session:
        A live SQLAlchemy session.  The function performs only SELECT queries.
    config:
        Detection configuration.  Defaults to :class:`MetricRiskDivergenceConfig`
        with its documented defaults if not provided.
    entity_ids:
        Optional scope filter.  When ``None`` all entities are assessed.

    Returns
    -------
    MetricRiskDivergenceResult
        Structured, immutable result containing any divergence findings and the
        list of entities skipped for insufficient observation periods.
    """
    config = config or MetricRiskDivergenceConfig()

    start = perf_counter()
    ctx = load_context(session, entity_ids=entity_ids)
    result = run_divergence_detection(ctx, config)
    duration_ms = (perf_counter() - start) * 1000.0

    total_periods = sum(
        ctx.entity_metrics[eid].period_count for eid in ctx.entity_ids()
    )

    logger.info(
        "metric-risk divergence detection complete: "
        "analytic=%s entities=%d total_periods=%d "
        "findings=%d skipped=%d tracked_metrics=%d "
        "min_obs_periods=%d headline_threshold=%.3f quality_threshold=%.3f "
        "duration_ms=%.1f",
        ANALYTIC_ID,
        len(result.entity_ids),
        total_periods,
        result.finding_count,
        len(result.skipped_entity_ids),
        len(METRIC_REGISTRY),
        config.min_observation_periods,
        config.headline_min_improvement,
        config.quality_min_deterioration,
        duration_ms,
    )
    return result
