"""Read-only normalized input access for negative-space detection.

A single :func:`load_context` performs a small, fixed number of bulk queries
(one per relevant table) and builds in-memory correlation maps, so rules can
reason about an asset's monitoring expectation and its telemetry history without
issuing per-row queries (no N+1). Detection is strictly read-only; this module
never writes, flushes, or mutates ORM state.

Tables that may legitimately be empty (e.g. no telemetry at all) are handled
without error and without interpreting emptiness as suspicious by default — a
rule establishes its own expectation before treating an absence as a gap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, SocEntity, TelemetryRecord


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Return ``dt`` as timezone-aware UTC (SQLite loses tz on read).

    The domain invariant is that every stored timestamp is UTC; a naive value
    read back from SQLite is therefore interpreted as UTC. Idempotent for
    already-aware datetimes.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class DetectionContext:
    """Immutable snapshot of the operational records in scope for a run."""

    entities: dict[str, SocEntity] = field(default_factory=dict)
    assets_by_entity: dict[str, list[Asset]] = field(default_factory=dict)
    telemetry_by_asset: dict[str, list[TelemetryRecord]] = field(default_factory=dict)

    def entity_ids(self) -> list[str]:
        """Entities in scope, in a deterministic (sorted) order."""
        return sorted(self.entities)

    def telemetry_for(self, asset_id: str) -> list[TelemetryRecord]:
        """Telemetry rows for one asset (possibly empty), sorted by period."""
        return self.telemetry_by_asset.get(asset_id, [])


def load_context(
    session: Session, entity_ids: list[str] | None = None
) -> DetectionContext:
    """Load a read-only detection context for all (or the given) entities."""
    ctx = DetectionContext()

    entity_stmt = select(SocEntity)
    if entity_ids is not None:
        entity_stmt = entity_stmt.where(SocEntity.entity_id.in_(entity_ids))
    for entity in session.scalars(entity_stmt):
        ctx.entities[entity.entity_id] = entity
        ctx.assets_by_entity[entity.entity_id] = []

    scope = set(ctx.entities)

    for asset in session.scalars(select(Asset)):
        if asset.entity_id in scope:
            ctx.assets_by_entity[asset.entity_id].append(asset)
            ctx.telemetry_by_asset.setdefault(asset.asset_id, [])

    for record in session.scalars(select(TelemetryRecord)):
        if record.entity_id in scope:
            ctx.telemetry_by_asset.setdefault(record.asset_id, []).append(record)

    # Deterministic ordering for stable iteration/output.
    for assets in ctx.assets_by_entity.values():
        assets.sort(key=lambda a: a.asset_id)
    for records in ctx.telemetry_by_asset.values():
        records.sort(key=lambda r: (ensure_utc(r.period_start), r.telemetry_id))

    return ctx
