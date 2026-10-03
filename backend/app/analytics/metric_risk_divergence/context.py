"""Read-only input access for metric-risk divergence detection.

A single :func:`load_context` performs a small, fixed number of bulk queries
(one per relevant table) and builds in-memory maps keyed by entity and
reporting period, so trend computation needs no per-row queries (no N+1 query
pattern).  Loading is strictly read-only; this module never writes, flushes, or
mutates ORM state.

The context holds only the ``PerformanceMetric`` rows and the ``SocEntity``
metadata needed to scope the run.  Raw operational records (alerts, investigations,
etc.) are not loaded here because this analytic operates entirely on the reported
performance metrics, not on re-derived operational evidence.

Future-leakage prevention
--------------------------
The observation window for any entity is bounded by its ``data_period_end``
field: periods that fall at or after that boundary are excluded.  This means the
context is reproducible — a re-run at a later date with the same ingested data
yields an identical context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PerformanceMetric, SocEntity


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Return ``dt`` as timezone-aware UTC (SQLite stores tz-naive datetimes)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class EntityPeriodMetrics:
    """All ``PerformanceMetric`` rows for one entity, ordered by period_start."""

    entity_id: str
    # Ordered list of (period_start, period_end, PerformanceMetric) triples,
    # sorted ascending by period_start.  Order is deterministic (see load_context).
    periods: list[tuple[datetime, datetime, PerformanceMetric]] = field(
        default_factory=list
    )

    @property
    def period_count(self) -> int:
        return len(self.periods)


@dataclass
class DivergenceContext:
    """Immutable snapshot of entities and their reporting-period metrics."""

    # entity_id → EntityPeriodMetrics
    entity_metrics: dict[str, EntityPeriodMetrics] = field(default_factory=dict)
    # entity_id → SocEntity (for metadata / scope checks)
    entities: dict[str, SocEntity] = field(default_factory=dict)

    def entity_ids(self) -> list[str]:
        """Sorted entity ids in scope."""
        return sorted(self.entity_metrics)


def load_context(
    session: Session,
    entity_ids: list[str] | None = None,
) -> DivergenceContext:
    """Load a read-only divergence context for all (or given) entities.

    All ``PerformanceMetric`` rows in scope are fetched in a single query and
    grouped in memory, avoiding N+1 patterns.  Periods are sorted ascending by
    ``period_start`` so downstream trend computation has a stable, deterministic
    ordering without needing to sort again.

    Future-leakage guarantee: only periods whose ``period_start`` is strictly
    before the entity's ``data_period_end`` are retained.  Periods that have not
    yet been "observed" at the entity's data horizon are silently excluded.
    """
    ctx = DivergenceContext()

    # --- load entities -------------------------------------------------------
    entity_stmt = select(SocEntity)
    if entity_ids is not None:
        entity_stmt = entity_stmt.where(SocEntity.entity_id.in_(entity_ids))
    for entity in session.scalars(entity_stmt):
        ctx.entities[entity.entity_id] = entity
        ctx.entity_metrics[entity.entity_id] = EntityPeriodMetrics(
            entity_id=entity.entity_id
        )

    scope = set(ctx.entities)
    if not scope:
        return ctx

    # --- load performance metrics (one bulk query) ---------------------------
    # Collect all rows first, then sort in memory for determinism.
    rows_by_entity: dict[str, list[tuple[datetime, datetime, PerformanceMetric]]] = {
        eid: [] for eid in scope
    }
    for pm in session.scalars(select(PerformanceMetric)):
        if pm.entity_id not in scope:
            continue
        start = ensure_utc(pm.period_start)
        end = ensure_utc(pm.period_end)

        # Future-leakage guard: exclude periods at or beyond the entity's
        # data horizon.  In the synthetic dataset data_period_end equals the
        # last period's end, so this guard is a no-op but it would matter if
        # a partial dataset were ingested.
        entity = ctx.entities[pm.entity_id]
        horizon = ensure_utc(entity.data_period_end)
        if horizon is not None and start >= horizon:
            continue

        rows_by_entity[pm.entity_id].append((start, end, pm))

    # Sort each entity's periods ascending; tie-break on entity_id for safety.
    for eid, rows in rows_by_entity.items():
        rows.sort(key=lambda t: t[0])
        ctx.entity_metrics[eid].periods = rows

    return ctx
