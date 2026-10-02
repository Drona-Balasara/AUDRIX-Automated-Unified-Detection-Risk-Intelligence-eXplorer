"""Canonical normalization of raw input values into internal Python types.

Normalization is deliberately conservative: it trims surrounding whitespace,
maps a small explicit allowlist of boolean spellings, matches enum values
against the Phase 2 vocabularies (case-insensitively, a controlled and
documented policy), parses timestamps into timezone-aware UTC, and converts
numbers strictly. It never guesses, never coerces an invalid value into a
plausible one, and never changes semantic meaning. Invalid values raise
:class:`ValueConversionError`, which the validation layer turns into a
structured, safe issue.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.ingestion.errors import ErrorCode
from app.ingestion.types import ColumnType, FieldSpec

# Explicit boolean spellings. Anything else is an error (no silent coercion).
_TRUE = {"true", "1", "yes", "y", "t"}
_FALSE = {"false", "0", "no", "n", "f"}


class ValueConversionError(Exception):
    """Raised when a single value cannot be normalized to its canonical type."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def normalize_value(spec: FieldSpec, raw: object, *, assume_naive_utc: bool) -> object | None:
    """Return the canonical value for ``raw`` under ``spec``.

    Returns ``None`` for an absent value. Raises :class:`ValueConversionError`
    for anything that is present but invalid.
    """
    if _is_blank(raw):
        return None

    if spec.type is ColumnType.STRING:
        return raw.strip() if isinstance(raw, str) else str(raw)

    if spec.type is ColumnType.ENUM:
        return _normalize_enum(spec, raw)

    if spec.type is ColumnType.BOOLEAN:
        return _normalize_bool(raw)

    if spec.type is ColumnType.INT:
        return _normalize_int(raw)

    if spec.type is ColumnType.FLOAT:
        return _normalize_float(raw)

    if spec.type is ColumnType.DATETIME:
        return _normalize_datetime(raw, assume_naive_utc=assume_naive_utc)

    raise ValueConversionError(  # pragma: no cover - exhaustive guard
        ErrorCode.INVALID_FIELD_TYPE, "unsupported column type"
    )


def _normalize_enum(spec: FieldSpec, raw: object) -> str:
    assert spec.enum is not None
    text = raw.strip() if isinstance(raw, str) else str(raw)
    allowed = {member.value: member.value for member in spec.enum}
    # Case-insensitive match against the controlled vocabulary.
    upper = {member.value.upper(): member.value for member in spec.enum}
    if text in allowed:
        return allowed[text]
    if text.upper() in upper:
        return upper[text.upper()]
    raise ValueConversionError(
        ErrorCode.INVALID_ENUM_VALUE,
        f"value is not one of the allowed {spec.name} values",
    )


def _normalize_bool(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueConversionError(
        ErrorCode.INVALID_FIELD_TYPE, "value is not a recognized boolean"
    )


def _normalize_int(raw: object) -> int:
    if isinstance(raw, bool):  # bool is a subclass of int; reject explicitly
        raise ValueConversionError(ErrorCode.INVALID_FIELD_TYPE, "expected an integer, got boolean")
    if isinstance(raw, int):
        return raw
    text = str(raw).strip()
    try:
        # Accept integral floats (e.g. "5.0") but reject genuine fractions.
        if isinstance(raw, float) or ("." in text) or ("e" in text.lower()):
            as_float = float(text)
            if as_float.is_integer():
                return int(as_float)
            raise ValueError
        return int(text)
    except (ValueError, OverflowError):
        raise ValueConversionError(
            ErrorCode.INVALID_FIELD_TYPE, "value is not a valid integer"
        ) from None


def _normalize_float(raw: object) -> float:
    if isinstance(raw, bool):
        raise ValueConversionError(ErrorCode.INVALID_FIELD_TYPE, "expected a number, got boolean")
    try:
        value = float(raw)
    except (ValueError, TypeError, OverflowError):
        raise ValueConversionError(
            ErrorCode.INVALID_FIELD_TYPE, "value is not a valid number"
        ) from None
    if value != value or value in (float("inf"), float("-inf")):  # NaN/inf guard
        raise ValueConversionError(ErrorCode.INVALID_FIELD_TYPE, "value is not a finite number")
    return value


def _normalize_datetime(raw: object, *, assume_naive_utc: bool) -> datetime:
    if isinstance(raw, datetime):
        parsed = raw
    else:
        text = str(raw).strip()
        # Tolerate a trailing 'Z' (UTC designator) which fromisoformat on older
        # 3.x did not accept; 3.11+ does, but normalize defensively.
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueConversionError(
                ErrorCode.INVALID_TIMESTAMP, "value is not an ISO-8601 timestamp"
            ) from None

    if parsed.tzinfo is None:
        if not assume_naive_utc:
            raise ValueConversionError(
                ErrorCode.NAIVE_TIMESTAMP,
                "timestamp lacks timezone information; supply an offset or set assume_naive_utc",
            )
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
