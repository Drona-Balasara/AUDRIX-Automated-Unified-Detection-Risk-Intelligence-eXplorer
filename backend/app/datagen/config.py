"""Configuration for synthetic dataset generation.

Everything that controls dataset shape and the probability model lives here so a
run is fully determined by the configuration plus the random seed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models.enums import EntityScale, Sector

# Documented default seed. A fixed seed reproduces an identical dataset.
DEFAULT_SEED = 20240601

# Fixed reference start so timestamps do not depend on wall-clock time.
BASE_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
NUM_PERIODS = 6  # consecutive ~monthly reporting periods


@dataclass(frozen=True)
class EntitySpec:
    """Static description of one SOC entity to generate."""

    sector: Sector
    scale: EntityScale


# Fixed roster. Multiple entities share a sector (peer group) so later peer
# benchmarking has legitimate comparisons; scale drives volume differences.
ENTITY_SPECS: tuple[EntitySpec, ...] = (
    EntitySpec(Sector.FINANCE, EntityScale.LARGE),
    EntitySpec(Sector.FINANCE, EntityScale.MEDIUM),
    EntitySpec(Sector.TECHNOLOGY, EntityScale.LARGE),
    EntitySpec(Sector.TECHNOLOGY, EntityScale.MEDIUM),
    EntitySpec(Sector.HEALTHCARE, EntityScale.MEDIUM),
    EntitySpec(Sector.HEALTHCARE, EntityScale.SMALL),
)

# Scale-dependent sizing (approximate; the generator adds controlled variance).
SCALE_ASSETS: dict[EntityScale, int] = {
    EntityScale.SMALL: 14,
    EntityScale.MEDIUM: 28,
    EntityScale.LARGE: 54,
}
SCALE_ALERTS_PER_PERIOD: dict[EntityScale, int] = {
    EntityScale.SMALL: 7,
    EntityScale.MEDIUM: 16,
    EntityScale.LARGE: 34,
}
SCALE_ANALYSTS: dict[EntityScale, int] = {
    EntityScale.SMALL: 5,
    EntityScale.MEDIUM: 12,
    EntityScale.LARGE: 24,
}


@dataclass(frozen=True)
class GenerationConfig:
    """Top-level knobs for a generation run."""

    seed: int = DEFAULT_SEED
    num_periods: int = NUM_PERIODS
    base_start: datetime = BASE_START
    entity_specs: tuple[EntitySpec, ...] = ENTITY_SPECS
    # Mild month-to-month volume variation applied to every entity in lockstep
    # so later phases can observe shared temporal trends.
    seasonal_factors: tuple[float, ...] = field(
        default=(0.9, 1.0, 1.05, 1.1, 0.95, 1.15)
    )


def add_months(start: datetime, months: int) -> datetime:
    """Return ``start`` advanced by whole months (day pinned to the 1st)."""
    month_index = (start.year * 12 + (start.month - 1)) + months
    year, month = divmod(month_index, 12)
    return start.replace(year=year, month=month + 1, day=1)


def build_periods(base_start: datetime, num_periods: int) -> list[tuple[datetime, datetime]]:
    """Return ``num_periods`` consecutive [start, end) monthly windows."""
    periods: list[tuple[datetime, datetime]] = []
    for i in range(num_periods):
        start = add_months(base_start, i)
        end = add_months(base_start, i + 1)
        periods.append((start, end))
    return periods


# SLA acknowledgement targets (minutes) by severity, used to derive SLA metrics.
SLA_ACK_MINUTES: dict[str, int] = {
    "LOW": 240,
    "MEDIUM": 120,
    "HIGH": 45,
    "CRITICAL": 15,
}


def period_bounds_seconds(start: datetime, end: datetime) -> float:
    """Length of a period in seconds (used to scatter timestamps)."""
    return (end - start) / timedelta(seconds=1)
