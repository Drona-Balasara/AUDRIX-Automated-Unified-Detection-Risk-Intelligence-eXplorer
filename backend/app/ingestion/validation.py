"""The format-neutral validation and normalization engine.

Given a dataset schema and parsed records, this module produces a structured
:class:`ValidationReport` and, when the input is fully valid, a list of
normalized rows ready for persistence. The same engine serves both CSV and JSON
(they only differ in parsing) and is independent of the HTTP layer, so tests and
future command-line workflows reuse it unchanged.

Validation proceeds in stages: structural (columns), then per-row typed
normalization and semantic checks (enums, identifier formats, numeric ranges,
required values), then dataset-level checks (duplicate identifiers, unique
columns, temporal ordering), then relationship checks against the database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ingestion.errors import ErrorCode, ValidationReport
from app.ingestion.normalization import ValueConversionError, normalize_value
from app.ingestion.parsing import ParseError, ParsedDataset, parse
from app.ingestion.types import DatasetSchema
from app.models.enums import ImportMode


@dataclass
class ValidationContext:
    """Inputs the engine needs beyond the dataset itself.

    ``existing_ids`` are the primary identifiers already present in the target
    table (used for APPEND duplicate detection). ``reference_ids`` maps each
    referenced dataset type to the set of identifiers that exist in the database,
    for foreign-key relationship checks. Both default to empty, letting the
    engine run structural/semantic validation without a database.
    """

    mode: ImportMode = ImportMode.APPEND
    assume_naive_utc: bool = False
    existing_ids: set[str] = field(default_factory=set)
    reference_ids: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class ValidationResult:
    report: ValidationReport
    normalized_rows: list[dict[str, object]]


def validate_dataset(
    schema: DatasetSchema,
    content: bytes,
    fmt: str,
    ctx: ValidationContext,
) -> ValidationResult:
    """Parse and validate raw bytes against ``schema``."""
    report = ValidationReport(dataset_type=schema.dataset_type, detected_format=fmt)
    try:
        parsed = parse(content, fmt)
    except ParseError as exc:
        report.add_code(exc.code, exc.message)
        return ValidationResult(report, [])
    return _validate_parsed(schema, parsed, ctx, report)


def _validate_parsed(
    schema: DatasetSchema,
    parsed: ParsedDataset,
    ctx: ValidationContext,
    report: ValidationReport,
) -> ValidationResult:
    report.detected_format = parsed.fmt
    report.row_count = len(parsed.rows)

    field_map = schema.field_map()
    allowed_columns = set(field_map)

    # --- Structural: column allowlist ---------------------------------------
    present = set(parsed.columns)
    for required in schema.required_columns():
        if required not in present:
            report.add_code(
                ErrorCode.MISSING_REQUIRED_COLUMN,
                f"required column '{required}' is missing",
                field=required,
            )
    for column in parsed.columns:
        if column not in allowed_columns:
            report.add_code(
                ErrorCode.UNKNOWN_COLUMN,
                f"column '{column}' is not part of the {schema.dataset_type} schema",
                field=column,
            )

    if report.row_count == 0:
        report.add_code(ErrorCode.EMPTY_DATASET, "dataset contains no records")

    # If the structure is wrong, do not attempt row interpretation.
    if report.structural_errors:
        return ValidationResult(report, [])

    # --- Per-row typed normalization + semantic checks ----------------------
    normalized_rows: list[dict[str, object]] = []
    row_valid: list[bool] = []
    for index, raw_row in enumerate(parsed.rows, start=1):
        normalized, ok = _validate_row(schema, field_map, raw_row, index, ctx, report)
        normalized_rows.append(normalized)
        row_valid.append(ok)

    # --- Dataset-level checks -----------------------------------------------
    _check_duplicate_pk(schema, normalized_rows, row_valid, ctx, report)
    _check_unique_columns(schema, normalized_rows, row_valid, report)
    _check_temporal(schema, normalized_rows, row_valid, report)
    _check_references(schema, normalized_rows, row_valid, ctx, report)

    report.accepted_count = sum(1 for ok in row_valid if ok)
    valid_rows = [row for row, ok in zip(normalized_rows, row_valid) if ok]
    return ValidationResult(report, valid_rows)


def _validate_row(
    schema: DatasetSchema,
    field_map: dict,
    raw_row: dict,
    index: int,
    ctx: ValidationContext,
    report: ValidationReport,
) -> tuple[dict[str, object], bool]:
    normalized: dict[str, object] = {}
    ok = True
    for name, spec in field_map.items():
        raw = raw_row.get(name)
        try:
            value = normalize_value(spec, raw, assume_naive_utc=ctx.assume_naive_utc)
        except ValueConversionError as exc:
            report.add_code(exc.code, exc.message, row=index, field=name)
            ok = False
            continue

        if value is None:
            if spec.required and not spec.nullable:
                report.add_code(
                    ErrorCode.MISSING_REQUIRED_VALUE,
                    f"required value for '{name}' is missing",
                    row=index,
                    field=name,
                )
                ok = False
            normalized[name] = None
            continue

        if spec.id_format and isinstance(value, str) and not re.match(spec.id_format, value):
            report.add_code(
                ErrorCode.INVALID_IDENTIFIER_FORMAT,
                f"'{name}' does not match the required identifier format",
                row=index,
                field=name,
            )
            ok = False

        if spec.min_value is not None and isinstance(value, (int, float)) and value < spec.min_value:
            code = ErrorCode.NEGATIVE_VALUE if spec.min_value == 0 else ErrorCode.VALUE_OUT_OF_RANGE
            report.add_code(
                code,
                f"'{name}' is below the allowed minimum of {spec.min_value}",
                row=index,
                field=name,
            )
            ok = False

        if spec.max_value is not None and isinstance(value, (int, float)) and value > spec.max_value:
            report.add_code(
                ErrorCode.VALUE_OUT_OF_RANGE,
                f"'{name}' exceeds the allowed maximum of {spec.max_value}",
                row=index,
                field=name,
            )
            ok = False

        normalized[name] = value

    return normalized, ok


def _check_duplicate_pk(
    schema: DatasetSchema,
    rows: list[dict],
    row_valid: list[bool],
    ctx: ValidationContext,
    report: ValidationReport,
) -> None:
    pk = schema.primary_key
    seen: set[str] = set()
    for index, (row, ok) in enumerate(zip(rows, row_valid), start=1):
        value = row.get(pk)
        if value is None:
            continue
        if value in seen:
            report.add_code(
                ErrorCode.DUPLICATE_IDENTIFIER,
                f"duplicate {pk} within the dataset",
                row=index,
                field=pk,
            )
            row_valid[index - 1] = False
        seen.add(value)
        # APPEND must not collide with rows already in the database. REPLACE
        # deletes existing rows first, so a collision there is expected.
        if ctx.mode is ImportMode.APPEND and value in ctx.existing_ids:
            report.add_code(
                ErrorCode.DUPLICATE_IDENTIFIER,
                f"{pk} already exists in the database (append mode)",
                row=index,
                field=pk,
            )
            row_valid[index - 1] = False


def _check_unique_columns(
    schema: DatasetSchema,
    rows: list[dict],
    row_valid: list[bool],
    report: ValidationReport,
) -> None:
    for column in schema.unique_columns:
        seen: set[str] = set()
        for index, row in enumerate(rows, start=1):
            value = row.get(column)
            if value is None:
                continue
            if value in seen:
                report.add_code(
                    ErrorCode.DUPLICATE_UNIQUE_VALUE,
                    f"duplicate {column} within the dataset (must be unique)",
                    row=index,
                    field=column,
                )
                row_valid[index - 1] = False
            seen.add(value)


def _check_temporal(
    schema: DatasetSchema,
    rows: list[dict],
    row_valid: list[bool],
    report: ValidationReport,
) -> None:
    for index, row in enumerate(rows, start=1):
        for rule in schema.temporal_rules:
            earlier = row.get(rule.earlier)
            later = row.get(rule.later)
            if earlier is not None and later is not None and later < earlier:
                report.add_code(
                    ErrorCode.TEMPORAL_ORDER,
                    f"'{rule.later}' must not precede '{rule.earlier}'",
                    row=index,
                    field=rule.later,
                )
                row_valid[index - 1] = False


def _check_references(
    schema: DatasetSchema,
    rows: list[dict],
    row_valid: list[bool],
    ctx: ValidationContext,
    report: ValidationReport,
) -> None:
    for fk in schema.foreign_keys:
        known = ctx.reference_ids.get(fk.references)
        if known is None:
            # No reference universe supplied: skip (DB-less validation).
            continue
        for index, row in enumerate(rows, start=1):
            value = row.get(fk.column)
            if value is None:
                continue
            if value not in known:
                report.add_code(
                    ErrorCode.MISSING_REFERENCE,
                    f"'{fk.column}' references a {fk.references} record that does not exist",
                    row=index,
                    field=fk.column,
                )
                row_valid[index - 1] = False
