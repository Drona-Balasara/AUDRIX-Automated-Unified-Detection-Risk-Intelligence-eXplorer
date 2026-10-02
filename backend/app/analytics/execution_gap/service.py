"""Public service boundary for execution-gap detection.

This module is the programmatic entry point: it is independent of FastAPI and
can be called from a script, a test, or (later) a thin HTTP route. It loads a
read-only detection context from a SQLAlchemy session, runs the enabled rules,
and returns the structured :class:`ExecutionGapResult`.

Logging emits only safe operational metadata — rule ids, entity-scope size,
finding counts, and run duration. It never logs alert, investigation, or any
record contents.
"""

from __future__ import annotations

from time import perf_counter

from sqlalchemy.orm import Session

from app.analytics.execution_gap.config import ExecutionGapConfig
from app.analytics.execution_gap.context import load_context
from app.analytics.execution_gap.engine import ExecutionGapResult, run_rules
from app.analytics.execution_gap.rules import get_rules
from app.core.logging import get_logger

logger = get_logger(__name__)


def run_execution_gap_detection(
    session: Session,
    config: ExecutionGapConfig | None = None,
    entity_ids: list[str] | None = None,
) -> ExecutionGapResult:
    """Run execution-gap detection over the given (or all) entities.

    Deterministic and strictly read-only: identical database state and config
    always yield an equal result, and no source records are mutated.
    """
    config = config or ExecutionGapConfig()
    rules = get_rules(enabled_only=True)

    start = perf_counter()
    ctx = load_context(session, entity_ids=entity_ids)
    result = run_rules(ctx, config, rules=rules)
    duration_ms = (perf_counter() - start) * 1000.0

    logger.info(
        "execution-gap detection complete: entities=%d rules=%d findings=%d "
        "by_rule=%s duration_ms=%.1f",
        len(result.entity_ids),
        len(result.rule_ids),
        result.finding_count,
        result.counts_by_rule(),
        duration_ms,
    )
    return result
