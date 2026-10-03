"""Immutable feature schema and deterministic feature construction.

Anomaly detection operates at **entity-plus-reporting-period** grain: one
observation per (SOC entity, reporting period). Features are computed from the
*raw operational records* (alerts, investigations, investigation actions,
escalations) — never from the externally reported ``performance_metrics`` KPIs
(which peer benchmarking consumes instead) and never from ground truth,
scenario labels, planted-anomaly identifiers, or any synthetic-only metadata.

Design discipline
------------------
- **No leakage.** No raw identifiers, primary keys, categorical codes,
  timestamps, or free text ever enter the model. ``entity_id`` is *not* a
  predictive feature.
- **Volume is normalized.** Raw alert volume is retained only as reporting
  context; the modeled volume signal is ``alerts_per_asset`` so a single
  high-volume entity cannot dominate the model purely by size.
- **Missing is explicit.** A feature whose denominator is undefined (e.g. a
  remediation rate for a period with zero true-positive alerts) is represented
  as ``None`` — never a silent zero. Observations missing any *model-eligible*
  feature are excluded from fitting/scoring and counted, rather than imputed.
- **Deterministic.** Feature order and numeric types are fixed by
  ``FEATURE_SCHEMA``; identical records always yield an identical matrix.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import StrEnum


class FeatureUnit(StrEnum):
    """Unit/interpretation of a feature value (reported, never scored)."""

    RATE = "RATE"  # dimensionless proportion in [0, 1]
    RATIO_PER_ASSET = "RATIO_PER_ASSET"  # count normalized by asset inventory
    DURATION_HOURS = "DURATION_HOURS"
    COUNT_PER_INVESTIGATION = "COUNT_PER_INVESTIGATION"
    COUNT = "COUNT"


@dataclass(frozen=True)
class FeatureSpec:
    """One immutable feature definition: where it comes from and how to read it.

    ``anomaly_eligible`` marks whether the feature feeds the unsupervised model.
    Features that are useful context but unsafe or ill-defined for the model
    (raw volume that would let large entities dominate; a rate with a frequently
    undefined denominator) are retained for reporting/peer context only.
    """

    name: str
    source: str
    aggregation: str
    unit: FeatureUnit
    missing_policy: str
    anomaly_eligible: bool
    description: str


# Ordered, immutable schema. The order here is the column order of the matrix.
FEATURE_SCHEMA: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name="alerts_per_asset",
        source="alert (count) / soc_entity.asset_count_estimate",
        aggregation="count of alerts created in period / asset inventory",
        unit=FeatureUnit.RATIO_PER_ASSET,
        missing_policy="asset_count_estimate is always >= 1; never missing",
        anomaly_eligible=True,
        description="Alert workload normalized by entity size (volume signal).",
    ),
    FeatureSpec(
        name="crit_high_rate",
        source="alert.severity",
        aggregation="(HIGH + CRITICAL alerts) / all alerts in period",
        unit=FeatureUnit.RATE,
        missing_policy="None if the period has zero alerts",
        anomaly_eligible=True,
        description="Share of alerts at high or critical severity.",
    ),
    FeatureSpec(
        name="tp_rate",
        source="alert.is_true_positive",
        aggregation="true-positive alerts / alerts with a resolved disposition",
        unit=FeatureUnit.RATE,
        missing_policy="None if no alert in period has a resolved disposition",
        anomaly_eligible=True,
        description="True-positive rate among dispositioned alerts.",
    ),
    FeatureSpec(
        name="closure_rate",
        source="investigation.status",
        aggregation="CLOSED investigations / investigations started in period",
        unit=FeatureUnit.RATE,
        missing_policy="None if no investigation started in period",
        anomaly_eligible=True,
        description="Share of started investigations that reached CLOSED.",
    ),
    FeatureSpec(
        name="inv_coverage",
        source="investigation vs alert",
        aggregation="investigations started / alerts created in period",
        unit=FeatureUnit.RATE,
        missing_policy="None if the period has zero alerts",
        anomaly_eligible=True,
        description="Investigation coverage relative to alert volume.",
    ),
    FeatureSpec(
        name="esc_rate",
        source="escalation linked to investigation",
        aggregation="investigations with >=1 escalation / investigations",
        unit=FeatureUnit.RATE,
        missing_policy="None if no investigation started in period",
        anomaly_eligible=True,
        description="Share of investigations that were escalated.",
    ),
    FeatureSpec(
        name="mean_inv_dur_h",
        source="investigation.duration_seconds (CLOSED)",
        aggregation="mean duration of closed investigations, in hours",
        unit=FeatureUnit.DURATION_HOURS,
        missing_policy="None if no closed investigation has a duration",
        anomaly_eligible=True,
        description="Average closed-investigation handling time.",
    ),
    FeatureSpec(
        name="mean_actions",
        source="investigation_action (count per investigation)",
        aggregation="mean number of actions per investigation in period",
        unit=FeatureUnit.COUNT_PER_INVESTIGATION,
        missing_policy="None if no investigation started in period",
        anomaly_eligible=True,
        description="Average workflow depth per investigation.",
    ),
    FeatureSpec(
        name="mean_evidence",
        source="investigation.evidence_count",
        aggregation="mean evidence_count across investigations in period",
        unit=FeatureUnit.COUNT_PER_INVESTIGATION,
        missing_policy="None if no investigation started in period",
        anomaly_eligible=True,
        description="Average evidence captured per investigation.",
    ),
    # --- Reporting/context only (NOT fed to the model) ----------------------
    FeatureSpec(
        name="alert_count",
        source="alert",
        aggregation="count of alerts created in period",
        unit=FeatureUnit.COUNT,
        missing_policy="always present (0 when no alerts)",
        anomaly_eligible=False,
        description="Raw alert volume; context only (see alerts_per_asset).",
    ),
    FeatureSpec(
        name="rem_rate",
        source="alert.is_true_positive + remediation.status",
        aggregation="TP alerts with a COMPLETED remediation / TP alerts",
        unit=FeatureUnit.RATE,
        missing_policy="None if the period has zero true-positive alerts",
        anomaly_eligible=False,
        description="Remediation completion among true positives; context only.",
    ),
)

ELIGIBLE_FEATURES: tuple[str, ...] = tuple(
    spec.name for spec in FEATURE_SCHEMA if spec.anomaly_eligible
)
ALL_FEATURES: tuple[str, ...] = tuple(spec.name for spec in FEATURE_SCHEMA)


@dataclass(frozen=True)
class EntityPeriodObservation:
    """One (entity, reporting-period) feature vector.

    ``values`` maps every feature name in :data:`ALL_FEATURES` to a float or
    ``None`` (undefined denominator). ``period_start`` / ``period_end`` are kept
    only as scope/anchoring metadata and are never used as model features.
    """

    entity_id: str
    period_start: object  # tz-aware datetime; typed loosely to avoid import here
    period_end: object
    values: dict[str, float | None]

    def is_model_complete(self) -> bool:
        """True when every model-eligible feature is present (non-None)."""
        return all(self.values.get(name) is not None for name in ELIGIBLE_FEATURES)

    def eligible_vector(self) -> list[float]:
        """The model-eligible feature values, in schema order.

        Raises if any eligible feature is missing; callers must gate on
        :meth:`is_model_complete` first (missing observations are dropped, not
        imputed).
        """
        vector: list[float] = []
        for name in ELIGIBLE_FEATURES:
            value = self.values.get(name)
            if value is None:
                raise ValueError(f"eligible feature {name!r} is missing")
            vector.append(float(value))
        return vector

