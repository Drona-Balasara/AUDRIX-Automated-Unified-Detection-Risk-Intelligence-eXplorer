"""Minimal system metadata table.

This single table is the only domain table in Phase 1. It exists for a concrete
foundation benefit: it proves that schema creation and a database round-trip
work end to end, records the schema version the database was initialized at
(useful when later phases introduce migrations), and gives the health check a
real table to query. It is not a placeholder for future SOC entities.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SystemMetadata(Base):
    """A single-row record describing the initialized database foundation."""

    __tablename__ = "system_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    initialized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SystemMetadata(id={self.id!r}, schema_version={self.schema_version!r})"
