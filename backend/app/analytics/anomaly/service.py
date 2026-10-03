"""Public service boundary for anomaly detection.

Programmatic entry point, independent of FastAPI: it loads a read-only context
from a SQLAlchemy session, fits the detector, and returns the structured
:class:`AnomalyResult`. It can be called from a script, a test, or (later) a
thin HTTP route.

Logging emits only safe operational metadata — analytic id, observation and
scored counts, feature count, finding count, model config metadata, and run
duration. It never logs operational rows, feature values, credentials, or any
sensitive record data.
"""

from __future__ import annotations

from time import perf_counter

from sqlalchemy.orm import Session

from app.analytics.anomaly.config import AnomalyConfig
from app.analytics.anomaly.context import load_context
from app.analytics.anomaly.engine import AnomalyResult, detect_anomalies
from app.analytics.anomaly.features import ELIGIBLE_FEATURES
from app.analytics.anomaly.findings import ANALYTIC_ID
from app.core.logging import get_logger

logger = get_logger(__name__)


def run_anomaly_detection(
    session: Session,
    config: AnomalyConfig | None = None,
    entity_ids: list[str] | None = None,
) -> AnomalyResult:
    """Run anomaly detection over the given (or all) entities.

    Deterministic and strictly read-only: identical database state and config
    always yield an equal result, and no source records are mutated.
    """
    config = config or AnomalyConfig()

    start = perf_counter()
    ctx = load_context(session, entity_ids=entity_ids)
    result = detect_anomalies(ctx, config)
    duration_ms = (perf_counter() - start) * 1000.0

    logger.info(
        "anomaly detection complete: analytic=%s status=%s entities=%d "
        "observations=%d scored=%d dropped=%d features=%d findings=%d "
        "model=%s contamination=%s random_state=%d duration_ms=%.1f",
        ANALYTIC_ID,
        result.status.value,
        len(result.entity_ids),
        result.observation_count,
        result.scored_observation_count,
        result.dropped_incomplete_count,
        len(ELIGIBLE_FEATURES),
        result.finding_count,
        config.model_version,
        config.contamination,
        config.random_state,
        duration_ms,
    )
    return result
