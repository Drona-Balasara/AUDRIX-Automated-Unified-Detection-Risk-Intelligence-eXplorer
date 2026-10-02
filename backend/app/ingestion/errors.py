"""Structured, safe validation error types for ingestion.

Errors are machine-readable (code + field + row) and carry only safe messages —
never raw row contents, filesystem paths, stack traces, the database URL, or
other internals. Structural errors concern format/shape; semantic errors concern
domain meaning. The two classes are reported separately so a caller can tell a
malformed file from a well-formed file carrying invalid data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ErrorSeverity(str, Enum):
    STRUCTURAL = "structural"
    SEMANTIC = "semantic"


class ErrorCode(str, Enum):
    """Stable, machine-readable error codes."""

    # Structural
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNSUPPORTED_DATASET_TYPE = "unsupported_dataset_type"
    MALFORMED_CSV = "malformed_csv"
    MALFORMED_JSON = "malformed_json"
    INVALID_JSON_STRUCTURE = "invalid_json_structure"
    EMPTY_DATASET = "empty_dataset"
    MISSING_REQUIRED_COLUMN = "missing_required_column"
    UNKNOWN_COLUMN = "unknown_column"
    INVALID_FIELD_TYPE = "invalid_field_type"
    CONTENT_FORMAT_MISMATCH = "content_format_mismatch"

    # Semantic
    MISSING_REQUIRED_VALUE = "missing_required_value"
    INVALID_ENUM_VALUE = "invalid_enum_value"
    INVALID_IDENTIFIER_FORMAT = "invalid_identifier_format"
    INVALID_TIMESTAMP = "invalid_timestamp"
    NAIVE_TIMESTAMP = "naive_timestamp"
    VALUE_OUT_OF_RANGE = "value_out_of_range"
    NEGATIVE_VALUE = "negative_value"
    TEMPORAL_ORDER = "temporal_order"
    DUPLICATE_IDENTIFIER = "duplicate_identifier"
    DUPLICATE_UNIQUE_VALUE = "duplicate_unique_value"
    MISSING_REFERENCE = "missing_reference"


# Codes that denote a structural (format/shape) problem; everything else is
# semantic (well-formed input carrying domain-invalid data).
_STRUCTURAL_CODES = frozenset(
    {
        ErrorCode.UNSUPPORTED_FORMAT,
        ErrorCode.UNSUPPORTED_DATASET_TYPE,
        ErrorCode.MALFORMED_CSV,
        ErrorCode.MALFORMED_JSON,
        ErrorCode.INVALID_JSON_STRUCTURE,
        ErrorCode.EMPTY_DATASET,
        ErrorCode.MISSING_REQUIRED_COLUMN,
        ErrorCode.UNKNOWN_COLUMN,
        ErrorCode.INVALID_FIELD_TYPE,
        ErrorCode.CONTENT_FORMAT_MISMATCH,
    }
)


def severity_for(code: ErrorCode) -> "ErrorSeverity":
    """Classify an error code as structural or semantic."""
    return ErrorSeverity.STRUCTURAL if code in _STRUCTURAL_CODES else ErrorSeverity.SEMANTIC


@dataclass(frozen=True)
class ValidationIssue:
    """A single structured validation issue.

    ``row`` is the 1-based record index within the dataset (``None`` for
    dataset-level/structural issues). ``field`` is the column name when
    applicable. ``message`` is concise and safe; it never embeds the offending
    value unless that value is a controlled token (e.g. an enum name).
    """

    code: ErrorCode
    severity: ErrorSeverity
    message: str
    row: int | None = None
    field: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "row": self.row,
            "field": self.field,
        }


@dataclass
class ValidationReport:
    """Accumulated validation outcome for one dataset."""

    dataset_type: str
    detected_format: str | None = None
    row_count: int = 0
    accepted_count: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(
        self,
        code: ErrorCode,
        severity: ErrorSeverity,
        message: str,
        *,
        row: int | None = None,
        field: str | None = None,
    ) -> None:
        self.issues.append(ValidationIssue(code, severity, message, row=row, field=field))

    def add_code(
        self,
        code: ErrorCode,
        message: str,
        *,
        row: int | None = None,
        field: str | None = None,
    ) -> None:
        """Append an issue, classifying severity from the code."""
        self.add(code, severity_for(code), message, row=row, field=field)

    @property
    def structural_errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is ErrorSeverity.STRUCTURAL]

    @property
    def semantic_errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is ErrorSeverity.SEMANTIC]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def rejected_count(self) -> int:
        return max(self.row_count - self.accepted_count, 0)
