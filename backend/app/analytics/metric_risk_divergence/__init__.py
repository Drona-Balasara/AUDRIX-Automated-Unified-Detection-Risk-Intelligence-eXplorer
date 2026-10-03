"""Metric-Risk Divergence detection (Phase 7).

A deterministic, read-only detector that identifies reporting periods where
headline SOC performance metrics appear to improve while underlying
operational-quality indicators materially deteriorate — a divergence pattern
that warrants human supervisory review.

This analytic does NOT accuse any SOC, analyst, or manager of manipulation,
fraud, or deliberate KPI gaming. Findings use neutral language and describe the
observed metric relationship with concrete evidence, leaving interpretation to
the human reviewer.

Analytical framework
--------------------
Consistent with NIST SP 800-55 Vol. 2 (December 2024), which distinguishes
output/efficiency metrics (what the SOC reports upward) from effectiveness and
quality indicators (how sound the operational work actually was), the detector
separates ``PerformanceMetric`` fields into two groups:

Headline KPIs (reported performance, direction-adjusted so "improving" = +):
  - closure_rate (higher = better)
  - sla_compliance (higher = better)
  - mttr_hours (lower = better → inverted to score)

Operational-quality indicators (direction-adjusted so "improving" = +):
  - investigation_completeness (higher = better)
  - evidence_completeness (higher = better)
  - remediation_rate (higher = better)
  - recurrence_rate (lower = better → inverted to score)

escalation_rate is excluded from both groups: its direction is context-
dependent (a spike may reflect good escalation practice or operational overload)
and including it would produce ambiguous composite signals.

Trend method
------------
For each group a composite *direction-adjusted* trend score is computed as the
signed least-squares slope over the entity's available reporting periods,
normalised by the value range to produce a dimensionless score in roughly
[-1, +1]. When fewer than three periods are available, a simpler first-vs-last
relative-change measure is used instead and flagged in the confidence note.

Divergence condition
--------------------
A finding is emitted when:
  - At least ``min_observation_periods`` periods are available for the entity.
  - The headline composite trend score ≥ ``headline_min_improvement``.
  - The quality composite trend score ≤ −``quality_min_deterioration``.

Conservative defaults are chosen for the small six-entity, six-period synthetic
dataset; production deployments must recalibrate.

Public API:

- :func:`run_metric_risk_divergence` — service entry point (FastAPI-independent).
- :class:`MetricRiskDivergenceConfig` — typed, immutable configuration.
- :class:`MetricRiskDivergenceResult` — structured outcome.
- :class:`MetricRiskDivergenceFinding` — finding schema.
- :data:`METRIC_REGISTRY` — the direction registry for all tracked metrics.
"""

from __future__ import annotations

from app.analytics.metric_risk_divergence.config import MetricRiskDivergenceConfig
from app.analytics.metric_risk_divergence.engine import (
    MetricRiskDivergenceResult,
    run_divergence_detection,
)
from app.analytics.metric_risk_divergence.findings import (
    ANALYTIC_ID,
    ConfidenceLevel,
    MetricRiskDivergenceFinding,
    MetricSnapshot,
)
from app.analytics.metric_risk_divergence.metrics import (
    METRIC_REGISTRY,
    MetricGroup,
    MetricSpec,
)
from app.analytics.metric_risk_divergence.service import run_metric_risk_divergence

__all__ = [
    "run_metric_risk_divergence",
    "run_divergence_detection",
    "MetricRiskDivergenceConfig",
    "MetricRiskDivergenceResult",
    "MetricRiskDivergenceFinding",
    "MetricSnapshot",
    "ConfidenceLevel",
    "METRIC_REGISTRY",
    "MetricGroup",
    "MetricSpec",
    "ANALYTIC_ID",
]
