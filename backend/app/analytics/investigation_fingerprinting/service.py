"""Public service boundary for investigation fingerprinting.

Programmatic entry point, independent of FastAPI: loads a read-only context
from a SQLAlchemy session, runs the three fingerprinting detectors, and
returns the structured :class:`FingerprintResult`.  Can be called from a
script, a test, or (later) a thin HTTP route.

Logging emits only safe operational metadata: analytic id, entity count,
investigation count, finding counts per detector type, and run duration.
It never logs action-sequence values, investigation content, analyst
identifiers, or any sensitive operational record data.
"""

from __future__ import annotations

from time import perf_counter

from sqlalchemy.orm import Session

from app.analytics.investigation_fingerprinting.config import FingerprintConfig
from app.analytics.investigation_fingerprinting.context import load_context
from app.analytics.investigation_fingerprinting.engine import (
    FingerprintResult,
    run_fingerprint_detection,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

ANALYTIC_ID = "IF"   # umbrella id for logging


def run_investigation_fingerprinting(
    session: Session,
    config: FingerprintConfig | None = None,
    entity_ids: list[str] | None = None,
) -> FingerprintResult:
    """Run investigation fingerprinting over the given (or all) entities.

    Deterministic and strictly read-only: identical database state and config
    always yield an equal result, and no source records are mutated.

    Parameters
    ----------
    session:
        A live SQLAlchemy session.  Only SELECT queries are issued.
    config:
        Detection configuration.  Defaults to :class:`FingerprintConfig` with
        its documented defaults if not provided.
    entity_ids:
        Optional scope filter.  When ``None`` all entities are assessed.
    """
    config = config or FingerprintConfig()

    start = perf_counter()
    ctx = load_context(session, entity_ids=entity_ids)
    result = run_fingerprint_detection(ctx, config)
    duration_ms = (perf_counter() - start) * 1000.0

    logger.info(
        "investigation fingerprinting complete: "
        "analytic=%s entities=%d investigations=%d "
        "repetitive_findings=%d deviation_findings=%d "
        "missing_action_findings=%d total_findings=%d "
        "rep_threshold=%d dev_threshold=%.2f "
        "duration_ms=%.1f",
        ANALYTIC_ID,
        len(result.entity_ids),
        result.total_investigations_assessed,
        len(result.repetitive_findings),
        len(result.deviation_findings),
        len(result.missing_action_findings),
        result.finding_count,
        config.repetition_threshold,
        config.deviation_threshold,
        duration_ms,
    )
    return result
