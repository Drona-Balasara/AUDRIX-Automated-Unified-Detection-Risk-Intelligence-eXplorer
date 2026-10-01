"""Monitoring-evidence domain models: telemetry records and performance metrics."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import enum_type
from app.models.enums import TelemetryCategory, TelemetrySourceStatus

if TYPE_CHECKING:
    from app.models.organization import Asset, SocEntity


class TelemetryRecord(Base):
    """Evidence that an asset produced expected telemetry over a reporting period.

    This is deliberately an aggregate signal (counts/activity per period), not
    raw packets or payloads. It is sufficient to detect missing monitoring and
    telemetry disappearance without SAT-SA acting as a SIEM.
    """

    __tablename__ = "telemetry_record"

    telemetry_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("soc_entity.entity_id"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset.asset_id"), nullable=False, index=True
    )

    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[TelemetryCategory] = mapped_column(enum_type(TelemetryCategory), nullable=False)

    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # Normalized activity level (0..1+) relative to the asset's typical baseline.
    activity_level: Mapped[float] = mapped_column(Float, nullable=False)
    source_status: Mapped[TelemetrySourceStatus] = mapped_column(
        enum_type(TelemetrySourceStatus), nullable=False
    )
    # Whether telemetry was expected for this asset/category in this period.
    expected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    entity: Mapped["SocEntity"] = relationship(back_populates="telemetry")
    asset: Mapped["Asset"] = relationship(back_populates="telemetry")


class PerformanceMetric(Base):
    """Periodic reported SOC performance for an entity.

    Headline KPIs and operational-quality indicators are stored as explicit
    typed fields (not an opaque JSON blob) so later phases can compare them and
    detect divergence between reported performance and operational evidence.
    """

    __tablename__ = "performance_metric"

    metric_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("soc_entity.entity_id"), nullable=False, index=True
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Headline KPIs (the figures a SOC typically reports upward).
    mttr_hours: Mapped[float] = mapped_column(Float, nullable=False)
    closure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    sla_compliance: Mapped[float] = mapped_column(Float, nullable=False)
    escalation_rate: Mapped[float] = mapped_column(Float, nullable=False)

    # Operational-quality indicators (how sound the work actually was).
    investigation_completeness: Mapped[float] = mapped_column(Float, nullable=False)
    recurrence_rate: Mapped[float] = mapped_column(Float, nullable=False)
    remediation_rate: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_completeness: Mapped[float] = mapped_column(Float, nullable=False)

    entity: Mapped["SocEntity"] = relationship(back_populates="performance_metrics")
