"""Shared helpers for ORM models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import Enum as SAEnum


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def enum_type(enum_cls: type[Enum]) -> SAEnum:
    """Return a portable SQLAlchemy Enum type storing the member value string.

    ``native_enum=False`` stores values as VARCHAR with a CHECK constraint,
    which is portable across SQLite and other backends and keeps the stored
    representation identical to the CSV/JSON dataset.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )
