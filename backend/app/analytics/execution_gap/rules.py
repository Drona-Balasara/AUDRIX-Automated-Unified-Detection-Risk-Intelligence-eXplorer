"""Execution-gap rule definitions and the rule registry.

Each rule is a small, self-contained unit with a stable identifier, a
human-readable name/description, an explicit enabled flag, and a pure
``evaluate`` method that reads the shared :class:`DetectionContext` and returns
structured findings. Rules never touch the database or HTTP layer; the engine
owns orchestration. Thresholds come from :class:`ExecutionGapConfig` — no magic
numbers live in the rule bodies.

Rule ownership (avoiding duplicate findings for one underlying problem):
- EG-001 owns "confirmed critical alert closed without escalation".
- EG-002 owns "acknowledged high/critical alert never investigated".
- EG-003 owns "recurring confirmed alerts never successfully remediated".
These conditions are disjoint, so a single alert cannot be double-reported by
two rules for the same reason.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from app.analytics.execution_gap.config import ExecutionGapConfig, severity_at_least
from app.analytics.execution_gap.context import DetectionContext
from app.analytics.execution_gap.findings import (
    ExecutionGapFinding,
    GapType,
    ReasonCode,
)
from app.models.enums import AlertStatus


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


class ExecutionGapRule(ABC):
    """Base class for a deterministic, read-only execution-gap rule."""

    rule_id: str
    name: str
    description: str
    gap_type: GapType
    enabled: bool = True

    @abstractmethod
    def evaluate(
        self, ctx: DetectionContext, config: ExecutionGapConfig
    ) -> list[ExecutionGapFinding]:
        """Return findings for this rule given the context and configuration."""
        raise NotImplementedError


class CriticalAlertWithoutEscalationRule(ExecutionGapRule):
    rule_id = "EG-001"
    name = "Critical alert without escalation"
    description = (
        "A confirmed critical alert whose investigation was completed and closed "
        "but for which no escalation was ever recorded."
    )
    gap_type = GapType.ESCALATION_GAP

    def evaluate(self, ctx, config):
        findings: list[ExecutionGapFinding] = []
        for entity_id in ctx.entity_ids():
            for alert in ctx.alerts_by_entity[entity_id]:
                if not severity_at_least(alert.severity, config.escalation_min_severity):
                    continue
                if alert.status != AlertStatus.CLOSED.value:
                    continue
                if config.escalation_require_true_positive and alert.is_true_positive is not True:
                    continue
                inv = ctx.investigation_by_alert.get(alert.alert_id)
                if inv is None:
                    # No recorded investigation: a different concern, not this rule's.
                    continue
                if ctx.alert_has_escalation(alert):
                    continue
                findings.append(
                    ExecutionGapFinding(
                        finding_key=f"{self.rule_id}:{alert.alert_id}",
                        rule_id=self.rule_id,
                        gap_type=self.gap_type,
                        reason_code=ReasonCode.CRITICAL_ALERT_NO_ESCALATION,
                        entity_id=entity_id,
                        asset_id=alert.asset_id,
                        alert_id=alert.alert_id,
                        investigation_id=inv.investigation_id,
                        observed_at=ensure_utc(alert.closed_at or alert.created_at),
                        alert_severity=alert.severity,
                        summary=(
                            f"Potential execution gap: confirmed {alert.severity} alert "
                            f"{alert.alert_id} was investigated and closed but has no "
                            "recorded escalation."
                        ),
                        expected_condition=(
                            f"A confirmed {alert.severity} alert is expected to have at "
                            "least one recorded escalation."
                        ),
                        observed_condition=(
                            "No escalation record references this alert or its investigation."
                        ),
                    )
                )
        return findings


class AcknowledgedAlertWithoutInvestigationRule(ExecutionGapRule):
    rule_id = "EG-002"
    name = "Acknowledged alert without investigation"
    description = (
        "A high or critical alert that was acknowledged but for which no "
        "investigation was ever opened, with enough elapsed time in the observed "
        "data for one to have been expected."
    )
    gap_type = GapType.INVESTIGATION_GAP

    def evaluate(self, ctx, config):
        findings: list[ExecutionGapFinding] = []
        for entity_id in ctx.entity_ids():
            entity = ctx.entities[entity_id]
            horizon = ensure_utc(entity.data_period_end)
            for alert in ctx.alerts_by_entity[entity_id]:
                if alert.acknowledged_at is None:
                    continue
                if alert.status != AlertStatus.ACKNOWLEDGED.value:
                    continue
                if not severity_at_least(alert.severity, config.investigation_min_severity):
                    continue
                if alert.alert_id in ctx.investigation_by_alert:
                    continue
                acked = ensure_utc(alert.acknowledged_at)
                elapsed_hours = (horizon - acked).total_seconds() / 3600.0
                if elapsed_hours < config.investigation_expected_within_hours:
                    # Acknowledged too recently (within the observed window) to
                    # conclude an investigation is missing rather than pending.
                    continue
                findings.append(
                    ExecutionGapFinding(
                        finding_key=f"{self.rule_id}:{alert.alert_id}",
                        rule_id=self.rule_id,
                        gap_type=self.gap_type,
                        reason_code=ReasonCode.ACKNOWLEDGED_ALERT_NO_INVESTIGATION,
                        entity_id=entity_id,
                        asset_id=alert.asset_id,
                        alert_id=alert.alert_id,
                        observed_at=acked,
                        window_start=acked,
                        window_end=horizon,
                        alert_severity=alert.severity,
                        summary=(
                            f"Potential execution gap: {alert.severity} alert "
                            f"{alert.alert_id} was acknowledged but no investigation "
                            "was recorded."
                        ),
                        expected_condition=(
                            f"An acknowledged {alert.severity} alert is expected to have a "
                            f"recorded investigation within "
                            f"{config.investigation_expected_within_hours:g}h."
                        ),
                        observed_condition=(
                            "The alert remains in ACKNOWLEDGED status with no linked "
                            "investigation record."
                        ),
                    )
                )
        return findings


class RecurringAlertsWithoutRemediationRule(ExecutionGapRule):
    rule_id = "EG-003"
    name = "Recurring confirmed alerts without remediation"
    description = (
        "A group of confirmed (true-positive) alerts sharing a recurrence key for "
        "one entity where no alert in the group has a completed remediation."
    )
    gap_type = GapType.REMEDIATION_GAP

    def evaluate(self, ctx, config):
        findings: list[ExecutionGapFinding] = []
        for entity_id in ctx.entity_ids():
            groups: dict[str, list] = {}
            for alert in ctx.alerts_by_entity[entity_id]:
                if alert.recurrence_key is None or alert.is_true_positive is not True:
                    continue
                groups.setdefault(alert.recurrence_key, []).append(alert)

            for recurrence_key in sorted(groups):
                confirmed = groups[recurrence_key]
                if len(confirmed) < config.remediation_min_recurring_true_positives:
                    continue
                if any(ctx.alert_has_completed_remediation(a) for a in confirmed):
                    continue
                confirmed.sort(key=lambda a: a.alert_id)
                created = [ensure_utc(a.created_at) for a in confirmed]
                findings.append(
                    ExecutionGapFinding(
                        finding_key=f"{self.rule_id}:{entity_id}:{recurrence_key}",
                        rule_id=self.rule_id,
                        gap_type=self.gap_type,
                        reason_code=ReasonCode.RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION,
                        entity_id=entity_id,
                        asset_id=confirmed[0].asset_id,
                        related_alert_ids=tuple(a.alert_id for a in confirmed),
                        recurrence_key=recurrence_key,
                        window_start=min(created),
                        window_end=max(created),
                        alert_severity=confirmed[0].severity,
                        summary=(
                            f"Potential execution gap: {len(confirmed)} confirmed recurring "
                            f"alerts on asset {confirmed[0].asset_id} have no completed "
                            "remediation."
                        ),
                        expected_condition=(
                            "Confirmed recurring alerts on one asset are expected to have at "
                            "least one completed remediation."
                        ),
                        observed_condition=(
                            "No remediation with status COMPLETED references any alert in the "
                            "recurrence group."
                        ),
                    )
                )
        return findings


# Explicit allowlist of execution-gap rules (discoverable and testable).
REGISTRY: tuple[ExecutionGapRule, ...] = (
    CriticalAlertWithoutEscalationRule(),
    AcknowledgedAlertWithoutInvestigationRule(),
    RecurringAlertsWithoutRemediationRule(),
)


def get_rules(enabled_only: bool = True) -> tuple[ExecutionGapRule, ...]:
    """Return the registered rules, optionally only the enabled ones."""
    if enabled_only:
        return tuple(rule for rule in REGISTRY if rule.enabled)
    return REGISTRY
