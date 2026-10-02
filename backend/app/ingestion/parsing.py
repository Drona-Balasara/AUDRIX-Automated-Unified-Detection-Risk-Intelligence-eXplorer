"""Parsing of raw upload bytes into a uniform list of records.

Only CSV and JSON are supported. Parsing performs light content inspection to
confirm the bytes actually match the declared format (never trusting the
filename or Content-Type), decodes strictly as UTF-8, and refuses to let pandas
infer types — CSV cells are read as plain strings and converted later under the
canonical schema. The output of both formats is the same shape: an ordered list
of ``{column: value}`` records plus the set of columns seen.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pandas as pd

from app.ingestion.errors import ErrorCode

CSV = "csv"
JSON = "json"
SUPPORTED_FORMATS = (CSV, JSON)


class ParseError(Exception):
    """Fatal structural parsing failure."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedDataset:
    """Uniform parse result for CSV and JSON input."""

    fmt: str
    rows: list[dict[str, object]]
    columns: list[str]


def _decode_utf8(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        raise ParseError(
            ErrorCode.CONTENT_FORMAT_MISMATCH,
            "file is not valid UTF-8 text",
        ) from None


def parse(content: bytes, fmt: str) -> ParsedDataset:
    """Parse ``content`` as the declared format into a :class:`ParsedDataset`."""
    if fmt == CSV:
        return _parse_csv(content)
    if fmt == JSON:
        return _parse_json(content)
    raise ParseError(ErrorCode.UNSUPPORTED_FORMAT, f"unsupported format: {fmt}")


def _parse_csv(content: bytes) -> ParsedDataset:
    text = _decode_utf8(content)
    stripped = text.lstrip()
    # A JSON document uploaded as .csv is a content/format mismatch.
    if stripped[:1] in ("{", "["):
        raise ParseError(
            ErrorCode.CONTENT_FORMAT_MISMATCH,
            "content looks like JSON, not CSV",
        )
    try:
        frame = pd.read_csv(
            io.StringIO(text),
            dtype=str,
            keep_default_na=False,
            na_filter=False,
            skip_blank_lines=True,
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        raise ParseError(ErrorCode.MALFORMED_CSV, "file is not valid CSV") from exc

    columns = [str(c) for c in frame.columns]
    if not columns:
        raise ParseError(ErrorCode.MALFORMED_CSV, "CSV has no header row")

    rows = frame.to_dict(orient="records")
    return ParsedDataset(fmt=CSV, rows=rows, columns=columns)


def _parse_json(content: bytes) -> ParsedDataset:
    text = _decode_utf8(content)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        raise ParseError(ErrorCode.MALFORMED_JSON, "file is not valid JSON") from None

    # Accept either a bare array of records or a wrapped {"records": [...]}.
    if isinstance(payload, dict) and "records" in payload:
        records = payload["records"]
    else:
        records = payload

    if not isinstance(records, list):
        raise ParseError(
            ErrorCode.INVALID_JSON_STRUCTURE,
            "expected a JSON array of records or an object with a 'records' array",
        )

    rows: list[dict[str, object]] = []
    seen: list[str] = []
    seen_set: set[str] = set()
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise ParseError(
                ErrorCode.INVALID_JSON_STRUCTURE,
                f"record {index} is not a JSON object",
            )
        for key in record:
            if key not in seen_set:
                seen_set.add(key)
                seen.append(key)
        rows.append(record)

    return ParsedDataset(fmt=JSON, rows=rows, columns=seen)
