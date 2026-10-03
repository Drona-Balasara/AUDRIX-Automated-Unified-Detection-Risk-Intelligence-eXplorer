"""Read-only input access and feature construction for anomaly detection.

A single :func:`load_context` performs a small, fixed number of bulk queries
(one per relevant table) and builds in-memory correlation maps, so features can
be computed without per-row queries (no N+1). Loading and feature construction
are strictly read-only; this module never writes, flushes, or mutates ORM
state.

The reporting-period grid is taken from the ``performance_metrics`` table
(the authoritative per-entity reporting periods). Feature *values*, however,
are computed from the raw operational records so that externally reported KPIs
cannot shape the anomaly signal.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.anomaly.features import (
    ALL_FEATURES,
    EntityPeriodObservation,
)
from app.models import (
    Alert,
    Escalation,
    Investigation,
    InvestigationAction,
    PerformanceMetric,
    SocEntity,
)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Return ``dt`` as timezone-aware UTC (SQLite loses tz on read)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class ReportingPeriod:
    """One (entity, period) window from the authoritative reporting grid."""

    entity_id: str
    period_start: datetime
    period_end: datetime


@dataclass
class AnomalyContext:
    """Immutable snapshot of the records in scope for a run."""

    entities: dict[str, SocEntity] = field(default_factory=dict)
    periods: list[ReportingPeriod] = field(default_factory=list)
    alerts_by_entity: dict[str, list[Alert]] = field(default_factory=dict)
    investigations_by_entity: dict[str, list[Investigation]] = field(
        default_factory=dict
    )
    actions_by_investigation: dict[str, list[InvestigationAction]] = field(
        default_factory=dict
    )
    escalated_investigation_ids: set[str] = field(default_factory=set)
    completed_remediation_alert_ids: set[str] = field(default_factory=set)

    def entity_ids(self) -> list[str]:
        return sorted(self.entities)


def load_context(
    session: Session, entity_ids: list[str] | None = None
) -> AnomalyContext:
    """Load a read-only anomaly-detection context for all/given entities."""
    ctx = AnomalyContext()

    entity_stmt = select(SocEntity)
    if entity_ids is not None:
        entity_stmt = entity_stmt.where(SocEntity.entity_id.in_(entity_ids))
    for entity in session.scalars(entity_stmt):
        ctx.entities[entity.entity_id] = entity
        ctx.alerts_by_entity[entity.entity_id] = []
        ctx.investigations_by_entity[entity.entity_id] = []

    scope = set(ctx.entities)

    for pm in session.scalars(select(PerformanceMetric)):
        if pm.entity_id in scope:
            ctx.periods.append(
                ReportingPeriod(
                    entity_id=pm.entity_id,
                    period_start=ensure_utc(pm.period_start),
                    period_end=ensure_utc(pm.period_end),
                )
            )

    for alert in session.scalars(select(Alert)):
        if alert.entity_id in scope:
            ctx.alerts_by_entity[alert.entity_id].append(alert)

    for inv in session.scalars(select(Investigation)):
        if inv.entity_id in scope:
            ctx.investigations_by_entity[inv.entity_id].append(inv)

    inv_entity = {
        inv.investigation_id: inv.entity_id
        for invs in ctx.investigations_by_entity.values()
        for inv in invs
    }

    for action in session.scalars(select(InvestigationAction)):
        if action.investigation_id in inv_entity:
            ctx.actions_by_investigation.setdefault(
                action.investigation_id, []
            ).append(action)

    for esc in session.scalars(select(Escalation)):
        if esc.investigation_id and esc.investigation_id in inv_entity:
            ctx.escalated_investigation_ids.add(esc.investigation_id)

    # True-positive alert ids that carry a COMPLETED remediation (via the alert
    # link or the investigation link). Used for the rem_rate context feature.
    from app.models import Remediation  # local import: keep module graph flat

    inv_alert = {
        inv.investigation_id: inv.alert_id
        for invs in ctx.investigations_by_entity.values()
        for inv in invs
    }
    for rem in session.scalars(select(Remediation)):
        if getattr(rem, "status", None) is None or str(rem.status) != "COMPLETED":
            continue
        if rem.alert_id:
            ctx.completed_remediation_alert_ids.add(rem.alert_id)
        if rem.investigation_id and rem.investigation_id in inv_alert:
            ctx.completed_remediation_alert_ids.add(inv_alert[rem.investigation_id])

    # Deterministic ordering for stable iteration/output.
    ctx.periods.sort(key=lambda p: (p.entity_id, p.period_start))
    for alerts in ctx.alerts_by_entity.values():
        alerts.sort(key=lambda a: a.alert_id)
    for invs in ctx.investigations_by_entity.values():
        invs.sort(key=lambda i: i.investigation_id)

    return ctx


def _rate(numerator: float, denominator: float) -> float | None:
    """Proportion, or ``None`` when the denominator is undefined (zero)."""
    if denominator == 0:
        return None
    return numerator / denominator


def build_observations(ctx: AnomalyContext) -> list[EntityPeriodObservation]:
    """Compute the deterministic feature matrix, one row per reporting period."""
    observations: list[EntityPeriodObservation] = []

    for period in ctx.periods:
        entity = ctx.entities[period.entity_id]
        start, end = period.period_start, period.period_end

        alerts = [
            a
            for a in ctx.alerts_by_entity.get(period.entity_id, [])
            if start <= ensure_utc(a.created_at) < end
        ]
        invs = [
            i
            for i in ctx.investigations_by_entity.get(period.entity_id, [])
            if start <= ensure_utc(i.started_at) < end
        ]

        n_alerts = len(alerts)
        n_invs = len(invs)
        crit_high = sum(1 for a in alerts if str(a.severity) in ("HIGH", "CRITICAL"))
        resolved = [a for a in alerts if a.is_true_positive is not None]
        tps = [a for a in resolved if a.is_true_positive]
        closed = [i for i in invs if str(i.status) == "CLOSED"]
        durations = [
            i.duration_seconds for i in closed if i.duration_seconds is not None
        ]
        escalated = sum(
            1 for i in invs if i.investigation_id in ctx.escalated_investigation_ids
        )
        action_counts = [
            len(ctx.actions_by_investigation.get(i.investigation_id, [])) for i in invs
        ]
        evidence_counts = [i.evidence_count for i in invs]
        tp_remediated = sum(
            1 for a in tps if a.alert_id in ctx.completed_remediation_alert_ids
        )

        asset_estimate = max(entity.asset_count_estimate, 1)
        values: dict[str, float | None] = {
            "alerts_per_asset": n_alerts / asset_estimate,
            "crit_high_rate": _rate(crit_high, n_alerts),
            "tp_rate": _rate(len(tps), len(resolved)),
            "closure_rate": _rate(len(closed), n_invs),
            "inv_coverage": _rate(n_invs, n_alerts),
            "esc_rate": _rate(escalated, n_invs),
            "mean_inv_dur_h": (
                sum(durations) / len(durations) / 3600.0 if durations else None
            ),
            "mean_actions": (
                sum(action_counts) / len(action_counts) if action_counts else None
            ),
            "mean_evidence": (
                sum(evidence_counts) / len(evidence_counts)
                if evidence_counts
                else None
            ),
            "alert_count": float(n_alerts),
            "rem_rate": _rate(tp_remediated, len(tps)),
        }
        # Guard: schema and computed keys must agree exactly.
        assert set(values) == set(ALL_FEATURES)

        observations.append(
            EntityPeriodObservation(
                entity_id=period.entity_id,
                period_start=start,
                period_end=end,
                values=values,
            )
        )

    observations.sort(key=lambda o: (o.entity_id, o.period_start))
    return observations
