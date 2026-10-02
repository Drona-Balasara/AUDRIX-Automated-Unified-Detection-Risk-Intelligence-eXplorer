"""Validation engine tests (format-neutral; no HTTP, no database).

These exercise the shared engine directly so CSV and JSON are proven to apply
identical business rules, and so each structural/semantic rule is covered.
"""

from __future__ import annotations

import pytest

from app.ingestion.errors import ErrorCode, ErrorSeverity
from app.ingestion.schemas import get_schema
from app.ingestion.validation import ValidationContext, validate_dataset
from tests.conftest import make_csv, make_json

_ENTITY_COLUMNS = [
    "entity_id",
    "name",
    "sector",
    "peer_group",
    "scale",
    "asset_count_estimate",
    "analyst_headcount",
    "created_at",
    "data_period_start",
    "data_period_end",
]


def _entity(**overrides) -> dict[str, object]:
    row = {
        "entity_id": "ENT-01",
        "name": "Example SOC",
        "sector": "FINANCE",
        "peer_group": "FINANCE",
        "scale": "MEDIUM",
        "asset_count_estimate": 10,
        "analyst_headcount": 5,
        "created_at": "2024-01-01T00:00:00+00:00",
        "data_period_start": "2024-01-01T00:00:00+00:00",
        "data_period_end": "2024-06-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _codes(report) -> set[str]:
    return {i.code.value for i in report.issues}


def _validate(rows, fmt, ctx=None, columns=_ENTITY_COLUMNS):
    schema = get_schema("entities")
    content = make_csv(rows, columns) if fmt == "csv" else make_json(rows)
    return validate_dataset(schema, content, fmt, ctx or ValidationContext())


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_valid_dataset_passes(fmt) -> None:
    result = _validate([_entity()], fmt)
    assert result.report.is_valid, _codes(result.report)
    assert result.report.row_count == 1
    assert result.report.accepted_count == 1
    assert len(result.normalized_rows) == 1


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_missing_required_column(fmt) -> None:
    cols = [c for c in _ENTITY_COLUMNS if c != "sector"]
    row = _entity()
    row.pop("sector")
    schema = get_schema("entities")
    content = make_csv([row], cols) if fmt == "csv" else make_json([row])
    result = validate_dataset(schema, content, fmt, ValidationContext())
    assert ErrorCode.MISSING_REQUIRED_COLUMN.value in _codes(result.report)
    assert result.report.structural_errors


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_invalid_enum_value_is_semantic(fmt) -> None:
    result = _validate([_entity(sector="BANKING")], fmt)
    assert ErrorCode.INVALID_ENUM_VALUE.value in _codes(result.report)
    assert result.report.semantic_errors
    assert not result.report.structural_errors


def test_enum_case_insensitive_normalization() -> None:
    result = _validate([_entity(sector="finance")], "json")
    assert result.report.is_valid, _codes(result.report)
    assert result.normalized_rows[0]["sector"] == "FINANCE"


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_invalid_identifier_format(fmt) -> None:
    result = _validate([_entity(entity_id="XYZ-9")], fmt)
    assert ErrorCode.INVALID_IDENTIFIER_FORMAT.value in _codes(result.report)


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_negative_value_rejected(fmt) -> None:
    result = _validate([_entity(asset_count_estimate=-1)], fmt)
    assert ErrorCode.NEGATIVE_VALUE.value in _codes(result.report)


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_invalid_integer_type(fmt) -> None:
    result = _validate([_entity(analyst_headcount="five")], fmt)
    assert ErrorCode.INVALID_FIELD_TYPE.value in _codes(result.report)


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_temporal_order_violation(fmt) -> None:
    result = _validate(
        [_entity(data_period_start="2024-06-01T00:00:00+00:00", data_period_end="2024-01-01T00:00:00+00:00")],
        fmt,
    )
    assert ErrorCode.TEMPORAL_ORDER.value in _codes(result.report)


@pytest.mark.parametrize("fmt", ["csv", "json"])
def test_duplicate_primary_identifier(fmt) -> None:
    result = _validate([_entity(), _entity()], fmt)
    assert ErrorCode.DUPLICATE_IDENTIFIER.value in _codes(result.report)


def test_naive_timestamp_rejected_by_default() -> None:
    result = _validate([_entity(created_at="2024-01-01 00:00:00")], "json")
    assert ErrorCode.NAIVE_TIMESTAMP.value in _codes(result.report)


def test_naive_timestamp_accepted_when_opted_in() -> None:
    ctx = ValidationContext(assume_naive_utc=True)
    result = _validate([_entity(created_at="2024-01-01 00:00:00")], "json", ctx)
    assert result.report.is_valid, _codes(result.report)


def test_invalid_timestamp_value() -> None:
    result = _validate([_entity(created_at="not-a-date")], "json")
    assert ErrorCode.INVALID_TIMESTAMP.value in _codes(result.report)


def test_percentage_out_of_range_on_metrics() -> None:
    schema = get_schema("performance_metrics")
    row = {
        "metric_id": "PMET-0001",
        "entity_id": "ENT-01",
        "period_start": "2024-01-01T00:00:00+00:00",
        "period_end": "2024-02-01T00:00:00+00:00",
        "mttr_hours": 2.0,
        "closure_rate": 1.5,  # out of [0, 1]
        "sla_compliance": 0.9,
        "escalation_rate": 0.1,
        "investigation_completeness": 0.8,
        "recurrence_rate": 0.1,
        "remediation_rate": 0.7,
        "evidence_completeness": 0.9,
    }
    result = validate_dataset(schema, make_json([row]), "json", ValidationContext())
    assert ErrorCode.VALUE_OUT_OF_RANGE.value in _codes(result.report)


def test_missing_foreign_key_reference() -> None:
    schema = get_schema("assets")
    cols = ["asset_id", "entity_id", "name", "category", "criticality", "monitoring_expected", "expected_telemetry", "created_at"]
    row = {
        "asset_id": "AST-00001",
        "entity_id": "ENT-99",
        "name": "Orphan",
        "category": "SERVER",
        "criticality": "HIGH",
        "monitoring_expected": True,
        "expected_telemetry": "AUTHENTICATION",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    ctx = ValidationContext(reference_ids={"entities": {"ENT-01"}})
    result = validate_dataset(schema, make_json([row]), "json", ctx)
    assert ErrorCode.MISSING_REFERENCE.value in _codes(result.report)


def test_append_duplicate_against_existing_db_ids() -> None:
    ctx = ValidationContext(existing_ids={"ENT-01"})
    result = _validate([_entity(entity_id="ENT-01")], "json", ctx)
    assert ErrorCode.DUPLICATE_IDENTIFIER.value in _codes(result.report)


def test_unknown_column_rejected() -> None:
    cols = _ENTITY_COLUMNS + ["surprise"]
    row = _entity()
    row["surprise"] = "x"
    result = _validate([row], "csv", columns=cols)
    assert ErrorCode.UNKNOWN_COLUMN.value in _codes(result.report)
