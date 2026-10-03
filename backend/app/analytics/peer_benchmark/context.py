"""Read-only input access for peer benchmarking.

A single :func:`load_context` performs a small, fixed number of bulk queries
and builds in-memory maps keyed by entity and reporting period, so comparisons
need no per-row queries (no N+1). Loading is strictly read-only; this module
never writes, flushes, or mutates ORM state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PerformanceMetric, SocEntity


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Return ``dt`` as timezone-aware UTC (SQLite loses tz on read)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class BenchmarkContext:
    """Immutable snapshot of entities and their reported KPIs in scope."""

    entities: dict[str, SocEntity] = field(default_factory=dict)
    # (entity_id, period_start) -> PerformanceMetric
    metrics: dict[tuple[str, datetime], PerformanceMetric] = field(
        default_factory=dict
    )
    # Distinct reporting-period starts, sorted.
    period_starts: list[datetime] = field(default_factory=list)
    # period_start -> period_end (periods are uniform across entities).
    period_end_by_start: dict[datetime, datetime] = field(default_factory=dict)

    def entity_ids(self) -> list[str]:
        return sorted(self.entities)

    def group_value(self, entity_id: str, dimension: str) -> str:
        """The entity's value on the configured peer dimension, as a string."""
        value = getattr(self.entities[entity_id], dimension)
        return str(value)


def load_context(
    session: Session, entity_ids: list[str] | None = None
) -> BenchmarkContext:
    """Load a read-only benchmark context for all/given entities."""
    ctx = BenchmarkContext()

    entity_stmt = select(SocEntity)
    if entity_ids is not None:
        entity_stmt = entity_stmt.where(SocEntity.entity_id.in_(entity_ids))
    for entity in session.scalars(entity_stmt):
        ctx.entities[entity.entity_id] = entity

    scope = set(ctx.entities)
    period_set: set[datetime] = set()

    for pm in session.scalars(select(PerformanceMetric)):
        if pm.entity_id not in scope:
            continue
        start = ensure_utc(pm.period_start)
        ctx.metrics[(pm.entity_id, start)] = pm
        ctx.period_end_by_start[start] = ensure_utc(pm.period_end)
        period_set.add(start)

    ctx.period_starts = sorted(period_set)
    return ctx
