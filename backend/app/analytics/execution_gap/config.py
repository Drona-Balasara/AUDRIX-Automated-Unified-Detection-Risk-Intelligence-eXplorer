"""Typed configuration for execution-gap detection.

All rule thresholds and temporal windows live here as an immutable, documented
dataclass rather than as magic numbers scattered through the rules. This follows
the project's existing typed-configuration convention (cf. the datagen
``GenerationConfig`` dataclass) and keeps detection strategy separate from the
individual analytics. Callers may pass a customized instance to the engine; the
defaults below are the safe, documented production defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import AlertSeverity


@dataclass(frozen=True)
class ExecutionGapConfig:
    """Thresholds and windows governing which conditions count as a gap.

    Temporal windows are measured against each entity's ``data_period_end`` (the
    observed data horizon), never wall-clock time, so detection is reproducible.
    """

    # --- Escalation gap -----------------------------------------------------
    # Minimum alert severity whose confirmed, closed alerts are expected to carry
    # an escalation. Default CRITICAL keeps the rule conservative.
    escalation_min_severity: AlertSeverity = AlertSeverity.CRITICAL
    # Only consider confirmed (true-positive) alerts. A disproven alert that was
    # not escalated is not an execution gap.
    escalation_require_true_positive: bool = True

    # --- Investigation gap --------------------------------------------------
    # Severities for which an acknowledged alert is expected to be investigated.
    investigation_min_severity: AlertSeverity = AlertSeverity.HIGH
    # Hours that must have elapsed between acknowledgment and the entity's data
    # horizon before a missing investigation is treated as a gap (avoids flagging
    # alerts acknowledged too recently to have been investigated yet).
    investigation_expected_within_hours: float = 24.0

    # --- Remediation gap ----------------------------------------------------
    # Minimum number of confirmed (true-positive) alerts sharing a recurrence key
    # before an un-remediated recurring group is treated as a gap.
    remediation_min_recurring_true_positives: int = 3

    def __post_init__(self) -> None:
        if self.investigation_expected_within_hours < 0:
            raise ValueError("investigation_expected_within_hours must be >= 0")
        if self.remediation_min_recurring_true_positives < 1:
            raise ValueError("remediation_min_recurring_true_positives must be >= 1")


# Severity ordering for threshold comparisons (low -> high).
_SEVERITY_RANK: dict[str, int] = {
    AlertSeverity.LOW.value: 0,
    AlertSeverity.MEDIUM.value: 1,
    AlertSeverity.HIGH.value: 2,
    AlertSeverity.CRITICAL.value: 3,
}


def severity_at_least(severity: str, minimum: AlertSeverity) -> bool:
    """Return True if ``severity`` ranks at or above ``minimum``."""
    return _SEVERITY_RANK.get(severity, -1) >= _SEVERITY_RANK[minimum.value]
