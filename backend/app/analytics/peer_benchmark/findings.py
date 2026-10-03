"""Structured peer-deviation finding schema.

A peer-deviation finding is a deterministic, strictly observational statement
that one entity-period's reported KPI is materially above or below the baseline
of its comparable peers in the *same* reporting period. The wording is neutral:
"materially above/below the comparable peer baseline" — never "performing
badly" or any value judgement. Each breached metric produces its own
machine-readable finding so downstream consumers can reason per metric.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import StrEnum

#: Stable identifier for the peer-benchmark analytic.
ANALYTIC_ID = "PB-001"


class DeviationDirection(StrEnum):
    """Whether the entity value sits above or below the peer baseline."""

    ABOVE = "ABOVE"
    BELOW = "BELOW"


class DeviationBasis(StrEnum):
    """How the deviation was judged."""

    ROBUST_MAD = "ROBUST_MAD"  # distance in scaled-MAD units exceeded threshold
    ABSOLUTE_ZERO_MAD = "ABSOLUTE_ZERO_MAD"  # zero peer spread -> absolute floor


class BenchmarkStatusCode(StrEnum):
    """Per-(entity, period) benchmark status for the non-finding cases."""

    INSUFFICIENT_PEERS = "INSUFFICIENT_PEERS"


class BenchmarkStatus(BaseModel):
    """Explicit record that an entity-period could not be benchmarked.

    Emitted (instead of a misleading percentile/deviation) when fewer than the
    configured minimum number of comparable peers are present in the period.
    """

    model_config = ConfigDict(frozen=True)

    status: BenchmarkStatusCode
    entity_id: str
    period_start: datetime
    period_end: datetime
    peer_dimension: str
    peer_group: str
    peer_population_count: int


class PeerDeviationFinding(BaseModel):
    """One reported KPI that deviates materially from the peer baseline."""

    model_config = ConfigDict(frozen=True)

    finding_key: str
    analytic_id: str = ANALYTIC_ID

    entity_id: str
    period_start: datetime
    period_end: datetime

    metric_name: str
    metric_unit: str
    entity_value: float
    peer_baseline: float  # peer median (entity's own value excluded)
    peer_population_count: int
    peer_group: str

    deviation_measure: float  # scaled-MAD distance, or absolute diff (zero-MAD)
    deviation_basis: DeviationBasis
    direction: DeviationDirection

    summary: str
