"""Ingestion audit model.

A compact, non-sensitive record of each ingestion operation so supervisory
review can later trace what was imported, when, and with what outcome — without
becoming a full audit-log system. Raw uploaded content is never stored here:
only counts, status, and a short safe summary string.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.common import enum_type, utcnow
from app.models.enums import ImportMode, ImportStatus


class ImportRecord(Base):
    """Audit row describing a single validation or import operation."""

    __tablename__ = "import_record"

    # Server-generated identifier (clients never supply this). Format IMP-<hex>.
    import_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    mode: Mapped[ImportMode] = mapped_column(enum_type(ImportMode), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(enum_type(ImportStatus), nullable=False)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Row accounting. ``row_count`` is the number of input records parsed;
    # accepted + rejected == row_count. ``error_count`` counts individual
    # validation errors (there can be several per row).
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Short, human-readable, non-sensitive summary. Never contains row contents,
    # filesystem paths, or stack traces.
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
