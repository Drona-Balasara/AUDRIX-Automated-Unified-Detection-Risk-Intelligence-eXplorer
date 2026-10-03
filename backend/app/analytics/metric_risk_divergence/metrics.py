"""Direction registry for metric-risk divergence.

Every ``PerformanceMetric`` field tracked by this analytic is declared here with
its group membership, improvement direction, and a zero-denominator policy note.
The registry is the *single authoritative source* of metric semantics for the
divergence detector; the engine reads from it and never hard-codes a metric name.

Design rationale
----------------
Two distinct groups are tracked:

``HEADLINE`` — the KPIs a SOC typically surfaces in management reports.  These
are the metrics whose apparent improvement triggers scrutiny when quality
indicators move in the opposite direction.

``QUALITY`` — operational-quality indicators that reflect the depth, coverage,
and completeness of the actual work performed.  These are the indicators whose
deterioration is the second half of a divergence signal.

``escalation_rate`` is intentionally absent from both groups.  Its directional
meaning is context-dependent: elevated escalation can indicate appropriate
risk-management behaviour or it can indicate operational overload; suppressed
escalation can indicate mature triage or missed incidents.  Including it in
either composite would introduce ambiguity that a deterministic trend detector
cannot resolve without richer context.

``higher_is_better`` and the "inverted" adjustment
---------------------------------------------------
When ``higher_is_better`` is ``False`` (e.g. ``mttr_hours``, ``recurrence_rate``),
the engine negates the raw value before trend fitting so that the resulting score
is direction-normalised: a positive composite score always means *improving*.
This inversion is applied uniformly and never silently assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import StrEnum


class MetricGroup(StrEnum):
    """Membership of a tracked metric."""

    HEADLINE = "HEADLINE"   # reported performance KPI
    QUALITY = "QUALITY"     # operational-quality indicator


@dataclass(frozen=True)
class MetricSpec:
    """Immutable declaration of one tracked metric.

    Attributes
    ----------
    name:
        Attribute name on ``PerformanceMetric`` (also the DB column name).
    group:
        Whether this metric belongs to the headline or quality composite.
    higher_is_better:
        If ``True`` an upward trend is improvement; if ``False`` a downward
        trend is improvement (the engine negates the value before scoring).
    description:
        Human-readable label for reports and documentation.
    missing_policy:
        How an undefined value is handled (informational; the field is NOT NULL
        in the current schema, so missingness arises only from scope filtering or
        a future schema extension — handled conservatively by exclusion).
    """

    name: str
    group: MetricGroup
    higher_is_better: bool
    description: str
    missing_policy: str


# Ordered registry.  Order is stable and matters for deterministic composite
# scoring: the engine iterates this tuple, not a dict.
METRIC_REGISTRY: tuple[MetricSpec, ...] = (
    # ----- Headline KPIs ----------------------------------------------------
    MetricSpec(
        name="closure_rate",
        group=MetricGroup.HEADLINE,
        higher_is_better=True,
        description="Share of investigations closed in the reporting period.",
        missing_policy="Field is NOT NULL; treated as available whenever a "
                       "PerformanceMetric row exists for the entity-period.",
    ),
    MetricSpec(
        name="sla_compliance",
        group=MetricGroup.HEADLINE,
        higher_is_better=True,
        description="Share of work items meeting the SLA threshold.",
        missing_policy="Field is NOT NULL; treated as available whenever a "
                       "PerformanceMetric row exists for the entity-period.",
    ),
    MetricSpec(
        name="mttr_hours",
        group=MetricGroup.HEADLINE,
        higher_is_better=False,  # lower MTTR is better → inverted before scoring
        description="Mean time to resolve (hours). Lower values indicate faster resolution.",
        missing_policy="Field is NOT NULL; treated as available whenever a "
                       "PerformanceMetric row exists for the entity-period.",
    ),
    # ----- Operational-quality indicators -----------------------------------
    MetricSpec(
        name="investigation_completeness",
        group=MetricGroup.QUALITY,
        higher_is_better=True,
        description="Reported share of investigations reaching a complete workflow.",
        missing_policy="Field is NOT NULL; treated as available whenever a "
                       "PerformanceMetric row exists for the entity-period.",
    ),
    MetricSpec(
        name="evidence_completeness",
        group=MetricGroup.QUALITY,
        higher_is_better=True,
        description="Reported average evidence completeness across investigations.",
        missing_policy="Field is NOT NULL; treated as available whenever a "
                       "PerformanceMetric row exists for the entity-period.",
    ),
    MetricSpec(
        name="remediation_rate",
        group=MetricGroup.QUALITY,
        higher_is_better=True,
        description="Share of true-positive alerts with a completed remediation.",
        missing_policy="Field is NOT NULL; treated as available whenever a "
                       "PerformanceMetric row exists for the entity-period.",
    ),
    MetricSpec(
        name="recurrence_rate",
        group=MetricGroup.QUALITY,
        higher_is_better=False,  # lower recurrence is better → inverted before scoring
        description="Share of alerts belonging to a recurring (un-resolved) pattern. "
                    "Higher recurrence signals unresolved underlying issues.",
        missing_policy="Field is NOT NULL; treated as available whenever a "
                       "PerformanceMetric row exists for the entity-period.",
    ),
)

# Convenience subsets — derived from the registry, never re-declared.
HEADLINE_METRICS: tuple[MetricSpec, ...] = tuple(
    m for m in METRIC_REGISTRY if m.group is MetricGroup.HEADLINE
)
QUALITY_METRICS: tuple[MetricSpec, ...] = tuple(
    m for m in METRIC_REGISTRY if m.group is MetricGroup.QUALITY
)
