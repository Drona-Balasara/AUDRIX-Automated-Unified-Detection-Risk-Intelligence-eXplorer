"""Read-only input access for investigation fingerprinting.

A single :func:`load_context` performs a small, fixed number of bulk queries
(one per relevant table) and builds in-memory maps, following the established
Phase 4–7 convention.  The context carries everything the engine needs:
investigation metadata, the associated alert's severity, the action sequences,
and the reporting-period grid (from ``PerformanceMetric`` rows).

No N+1 queries are issued: all records are fetched with one ``select`` per
table and correlated in memory.

Future-leakage is prevented at the engine level (the engine compares each
investigation only against investigations that occur no later than the same
reporting period).  The context itself loads all available records; the engine
slices by period.

This module is strictly read-only and never mutates ORM state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.investigation_fingerprinting.sequence import (
    Fingerprint,
    build_fingerprint,
    has_duplicate_sequence_numbers,
)
from app.models import (
    Alert,
    Investigation,
    InvestigationAction,
    PerformanceMetric,
    SocEntity,
)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Return ``dt`` as timezone-aware UTC (SQLite strips tz on read)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class InvestigationRecord:
    """All fingerprinting-relevant fields for one investigation.

    Derived and cached once during context loading; the engine never re-queries.
    """

    investigation_id: str
    entity_id: str
    alert_id: str
    alert_severity: str         # raw severity string from the Alert row
    analyst_id: str
    status: str                 # InvestigationStatus value
    started_at: datetime | None
    ended_at: datetime | None
    period_start: datetime      # from the PerformanceMetric grid
    period_end: datetime
    fingerprint: Fingerprint    # canonical ordered action-type tuple
    action_count: int           # len(fingerprint)
    has_duplicate_seq_nums: bool


@dataclass
class FingerprintContext:
    """Immutable snapshot of all records needed for fingerprinting."""

    # entity_id → SocEntity
    entities: dict[str, SocEntity] = field(default_factory=dict)

    # All InvestigationRecord objects in scope, sorted by
    # (entity_id, period_start, investigation_id) for determinism.
    records: list[InvestigationRecord] = field(default_factory=list)

    # (entity_id, period_start) → (period_start, period_end)
    # Derived from PerformanceMetric so detection windows match reporting periods.
    period_grid: dict[tuple[str, datetime], tuple[datetime, datetime]] = field(
        default_factory=dict
    )

    def entity_ids(self) -> list[str]:
        return sorted(self.entities)


def load_context(
    session: Session,
    entity_ids: list[str] | None = None,
) -> FingerprintContext:
    """Load all records needed for fingerprinting in a small fixed number of queries.

    Query order:
      1. SocEntity (scope filter)
      2. PerformanceMetric (reporting-period grid)
      3. Alert (severity lookup)
      4. Investigation
      5. InvestigationAction (bulk load then group by investigation_id)

    All subsequent operations are in-memory.
    """
    ctx = FingerprintContext()

    # 1. Entities -----------------------------------------------------------
    stmt = select(SocEntity)
    if entity_ids is not None:
        stmt = stmt.where(SocEntity.entity_id.in_(entity_ids))
    for entity in session.scalars(stmt):
        ctx.entities[entity.entity_id] = entity

    scope = set(ctx.entities)
    if not scope:
        return ctx

    # 2. Reporting-period grid from PerformanceMetric -----------------------
    # Maps each (entity_id, period_start_utc) → (period_start, period_end).
    for pm in session.scalars(select(PerformanceMetric)):
        if pm.entity_id not in scope:
            continue
        ps = _ensure_utc(pm.period_start)
        pe = _ensure_utc(pm.period_end)
        ctx.period_grid[(pm.entity_id, ps)] = (ps, pe)

    # 3. Alerts (severity lookup) -------------------------------------------
    alert_severity: dict[str, str] = {}  # alert_id → severity string
    for alert in session.scalars(select(Alert)):
        if alert.entity_id in scope:
            alert_severity[alert.alert_id] = str(alert.severity)

    # 4. Investigations -----------------------------------------------------
    # inv_id → Investigation ORM row (entity filter applied)
    inv_rows: dict[str, Investigation] = {}
    for inv in session.scalars(select(Investigation)):
        if inv.entity_id in scope:
            inv_rows[inv.investigation_id] = inv

    # 5. InvestigationAction (bulk load → group by investigation_id) ---------
    # actions_raw: inv_id → list of (sequence_number, occurred_at, action_type)
    actions_raw: dict[str, list[tuple[int, datetime | None, str]]] = {
        iid: [] for iid in inv_rows
    }
    for action in session.scalars(select(InvestigationAction)):
        if action.investigation_id not in actions_raw:
            continue
        actions_raw[action.investigation_id].append(
            (
                action.sequence_number,
                _ensure_utc(action.occurred_at),
                str(action.action_type),
            )
        )

    # 6. Assign each investigation to its reporting period ------------------
    # Strategy: match investigation.started_at to the reporting period whose
    # [period_start, period_end) contains it.
    #
    # Build a per-entity period list for fast lookup.
    entity_periods: dict[str, list[tuple[datetime, datetime]]] = {
        eid: [] for eid in scope
    }
    for (eid, ps), (ps2, pe) in ctx.period_grid.items():
        entity_periods[eid].append((ps2, pe))
    for periods in entity_periods.values():
        periods.sort()

    def _find_period(
        eid: str, started_at: datetime | None
    ) -> tuple[datetime, datetime] | None:
        """Return the (period_start, period_end) that contains started_at."""
        if started_at is None:
            return None
        for ps, pe in entity_periods.get(eid, []):
            if ps <= started_at < pe:
                return (ps, pe)
        return None

    # 7. Build InvestigationRecord objects ----------------------------------
    records: list[InvestigationRecord] = []
    for inv_id, inv in inv_rows.items():
        raw_actions = actions_raw.get(inv_id, [])
        fingerprint = build_fingerprint(raw_actions)
        dup = has_duplicate_sequence_numbers(raw_actions)
        sev = alert_severity.get(inv.alert_id, "")
        started = _ensure_utc(inv.started_at)
        ended   = _ensure_utc(inv.ended_at)
        period  = _find_period(inv.entity_id, started)
        if period is None:
            # Investigation has no matching reporting period; exclude from
            # window-based detection rather than silently assigning a wrong
            # period.  It can still trigger missing-expected-action findings
            # if we later choose to include it; for now, skip it.
            continue
        records.append(
            InvestigationRecord(
                investigation_id=inv_id,
                entity_id=inv.entity_id,
                alert_id=inv.alert_id,
                alert_severity=sev,
                analyst_id=str(inv.analyst_id),
                status=str(inv.status),
                started_at=started,
                ended_at=ended,
                period_start=period[0],
                period_end=period[1],
                fingerprint=fingerprint,
                action_count=len(fingerprint),
                has_duplicate_seq_nums=dup,
            )
        )

    # Deterministic ordering.
    records.sort(key=lambda r: (r.entity_id, r.period_start, r.investigation_id))
    ctx.records = records
    return ctx
