"""Read-only normalized input access for execution-gap detection.

A single :func:`load_context` performs a small, fixed number of bulk queries
(one per relevant table) and builds in-memory correlation maps, so rules can
reason about related records (alert -> investigation -> escalation/remediation)
without issuing per-row queries (no N+1). Detection is strictly read-only; this
module never writes, flushes, or mutates ORM state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Alert,
    Escalation,
    Investigation,
    Remediation,
    SocEntity,
)
from app.models.enums import RemediationStatus


@dataclass
class DetectionContext:
    """Immutable snapshot of the operational records in scope for a run."""

    entities: dict[str, SocEntity] = field(default_factory=dict)
    alerts_by_entity: dict[str, list[Alert]] = field(default_factory=dict)
    investigation_by_alert: dict[str, Investigation] = field(default_factory=dict)
    escalated_alert_ids: set[str] = field(default_factory=set)
    escalated_investigation_ids: set[str] = field(default_factory=set)
    completed_remediation_alert_ids: set[str] = field(default_factory=set)
    completed_remediation_investigation_ids: set[str] = field(default_factory=set)

    def entity_ids(self) -> list[str]:
        """Entities in scope, in a deterministic (sorted) order."""
        return sorted(self.entities)

    def alert_has_escalation(self, alert: Alert) -> bool:
        inv = self.investigation_by_alert.get(alert.alert_id)
        return alert.alert_id in self.escalated_alert_ids or (
            inv is not None and inv.investigation_id in self.escalated_investigation_ids
        )

    def alert_has_completed_remediation(self, alert: Alert) -> bool:
        inv = self.investigation_by_alert.get(alert.alert_id)
        return alert.alert_id in self.completed_remediation_alert_ids or (
            inv is not None
            and inv.investigation_id in self.completed_remediation_investigation_ids
        )


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
        ctx.alerts_by_entity[entity.entity_id] = []

    scope = set(ctx.entities)

    for alert in session.scalars(select(Alert)):
        if alert.entity_id in scope:
            ctx.alerts_by_entity[alert.entity_id].append(alert)

    for inv in session.scalars(select(Investigation)):
        if inv.entity_id in scope:
            ctx.investigation_by_alert[inv.alert_id] = inv

    for esc in session.scalars(select(Escalation)):
        if esc.alert_id is not None:
            ctx.escalated_alert_ids.add(esc.alert_id)
        if esc.investigation_id is not None:
            ctx.escalated_investigation_ids.add(esc.investigation_id)

    completed = RemediationStatus.COMPLETED.value
    for rem in session.scalars(select(Remediation)):
        if rem.status != completed:
            continue
        if rem.alert_id is not None:
            ctx.completed_remediation_alert_ids.add(rem.alert_id)
        if rem.investigation_id is not None:
            ctx.completed_remediation_investigation_ids.add(rem.investigation_id)

    # Deterministic per-entity alert ordering for stable iteration/output.
    for alerts in ctx.alerts_by_entity.values():
        alerts.sort(key=lambda a: a.alert_id)

    return ctx
