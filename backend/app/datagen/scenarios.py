"""Ground-truth scenario vocabulary.

Each planted scenario is represented by *real* record relationships in the
generated dataset; this module only names the scenario types and the detection
category later analytics are expected to assign. The planting logic itself lives
in :mod:`app.datagen.generator`, where it has access to the record builders.

Ground truth is evaluation data. It is written to a separate ``ground_truth``
file and must never be exposed through production APIs or dashboards in later
phases.
"""

from __future__ import annotations


class ScenarioType:
    """Stable identifiers for the planted (and baseline) scenario kinds."""

    NORMAL_BASELINE = "NORMAL_BASELINE"
    CRITICAL_ALERT_WITHOUT_ESCALATION = "CRITICAL_ALERT_WITHOUT_ESCALATION"
    ACKNOWLEDGED_WITHOUT_INVESTIGATION = "ACKNOWLEDGED_WITHOUT_INVESTIGATION"
    SUSPICIOUSLY_FAST_INVESTIGATION = "SUSPICIOUSLY_FAST_INVESTIGATION"
    RECURRING_ALERTS_WITHOUT_REMEDIATION = "RECURRING_ALERTS_WITHOUT_REMEDIATION"
    MISSING_TELEMETRY_CRITICAL_ASSET = "MISSING_TELEMETRY_CRITICAL_ASSET"
    TELEMETRY_DISAPPEARANCE = "TELEMETRY_DISAPPEARANCE"
    REPETITIVE_INVESTIGATION_WORKFLOW = "REPETITIVE_INVESTIGATION_WORKFLOW"
    METRIC_RISK_DIVERGENCE = "METRIC_RISK_DIVERGENCE"


class DetectionCategory:
    """Expected detection category a later analytics phase should assign.

    ``NONE`` marks negative controls (normal baseline records that must *not* be
    flagged).
    """

    NONE = "NONE"
    ESCALATION_GAP = "ESCALATION_GAP"
    INVESTIGATION_GAP = "INVESTIGATION_GAP"
    INVESTIGATION_QUALITY = "INVESTIGATION_QUALITY"
    REMEDIATION_GAP = "REMEDIATION_GAP"
    MONITORING_GAP = "MONITORING_GAP"
    TELEMETRY_DISAPPEARANCE = "TELEMETRY_DISAPPEARANCE"
    WORKFLOW_REPETITION = "WORKFLOW_REPETITION"
    METRIC_RISK_DIVERGENCE = "METRIC_RISK_DIVERGENCE"


# Maps each scenario type to the detection category it should surface as.
SCENARIO_DETECTION: dict[str, str] = {
    ScenarioType.NORMAL_BASELINE: DetectionCategory.NONE,
    ScenarioType.CRITICAL_ALERT_WITHOUT_ESCALATION: DetectionCategory.ESCALATION_GAP,
    ScenarioType.ACKNOWLEDGED_WITHOUT_INVESTIGATION: DetectionCategory.INVESTIGATION_GAP,
    ScenarioType.SUSPICIOUSLY_FAST_INVESTIGATION: DetectionCategory.INVESTIGATION_QUALITY,
    ScenarioType.RECURRING_ALERTS_WITHOUT_REMEDIATION: DetectionCategory.REMEDIATION_GAP,
    ScenarioType.MISSING_TELEMETRY_CRITICAL_ASSET: DetectionCategory.MONITORING_GAP,
    ScenarioType.TELEMETRY_DISAPPEARANCE: DetectionCategory.TELEMETRY_DISAPPEARANCE,
    ScenarioType.REPETITIVE_INVESTIGATION_WORKFLOW: DetectionCategory.WORKFLOW_REPETITION,
    ScenarioType.METRIC_RISK_DIVERGENCE: DetectionCategory.METRIC_RISK_DIVERGENCE,
}
