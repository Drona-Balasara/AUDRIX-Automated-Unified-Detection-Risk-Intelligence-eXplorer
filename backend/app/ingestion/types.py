"""Typed schema-definition primitives for ingestion.

These dataclasses describe, for each supported dataset type, exactly which
columns are expected, their canonical types, enum vocabularies, identifier
formats, numeric ranges, timestamp fields, and relationship requirements. The
definitions are declarative data (an explicit, auditable registry) — they are
*not* executable model names taken from client input. The same definitions
drive validation and normalization for both CSV and JSON so the two formats can
never diverge in business rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ColumnType(str, Enum):
    """Canonical column value types understood by the normalizer."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    ENUM = "enum"


@dataclass(frozen=True)
class FieldSpec:
    """Declarative specification for a single column.

    Attributes:
        name: Canonical column name (matches the ORM attribute exactly).
        type: Canonical value type for normalization.
        required: Whether the column must be present in the input. A required
            column must appear as a header/key; see ``nullable`` for whether an
            individual value may be empty.
        nullable: Whether an individual value may be absent/null after parsing.
            Required identifiers are never nullable.
        enum: For ``ColumnType.ENUM``, the controlled vocabulary (the Phase 2
            enum class is the single source of truth; no second list is kept).
        id_format: Optional regex a non-null identifier value must match.
        min_value / max_value: Inclusive numeric bounds (ints/floats) when set.
        is_primary_key: Marks the dataset's primary identifier column.
    """

    name: str
    type: ColumnType
    required: bool = True
    nullable: bool = False
    enum: type[Enum] | None = None
    id_format: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    is_primary_key: bool = False


@dataclass(frozen=True)
class ForeignKeySpec:
    """A relationship requirement validated against the database (or batch)."""

    column: str
    # Dataset type whose primary identifiers the column must reference.
    references: str
    # If True, the referenced value must exist when the column is non-null.
    required: bool = True


@dataclass(frozen=True)
class TemporalRule:
    """Requires ``earlier`` <= ``later`` when both values are present."""

    earlier: str
    later: str


@dataclass(frozen=True)
class DatasetSchema:
    """Everything ingestion needs to validate and persist one dataset type."""

    # Stable dataset-type key exposed in the API allowlist.
    dataset_type: str
    # Fixed physical table name. Never taken from client input.
    table_name: str
    # Fully-qualified ORM model path, resolved lazily to avoid import cycles.
    model_name: str
    primary_key: str
    fields: tuple[FieldSpec, ...]
    foreign_keys: tuple[ForeignKeySpec, ...] = ()
    temporal_rules: tuple[TemporalRule, ...] = ()
    # Columns (besides the primary key) that must be unique within the dataset
    # and against existing rows — e.g. an investigation's one-to-one alert_id.
    unique_columns: tuple[str, ...] = ()

    def field_map(self) -> dict[str, FieldSpec]:
        return {f.name: f for f in self.fields}

    def required_columns(self) -> list[str]:
        return [f.name for f in self.fields if f.required]
