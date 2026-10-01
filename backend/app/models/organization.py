"""Organizational domain models: SOC entities and their assets."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import enum_type
from app.models.enums import AssetCategory, Criticality, EntityScale, Sector, TelemetryCategory

if TYPE_CHECKING:
    from app.models.monitoring import PerformanceMetric, TelemetryRecord
    from app.models.operations import Alert, Investigation


class SocEntity(Base):
    """A comparable operational unit (a SOC) being assessed and benchmarked."""

    __tablename__ = "soc_entity"

    entity_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sector: Mapped[Sector] = mapped_column(enum_type(Sector), nullable=False)
    # Peer group used for benchmarking; typically sector + scale (e.g. FINANCE-LARGE).
    peer_group: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scale: Mapped[EntityScale] = mapped_column(enum_type(EntityScale), nullable=False)
    asset_count_estimate: Mapped[int] = mapped_column(Integer, nullable=False)
    analyst_headcount: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Inclusive/exclusive bounds of the dataset history for this entity.
    data_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    assets: Mapped[list["Asset"]] = relationship(back_populates="entity")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="entity")
    investigations: Mapped[list["Investigation"]] = relationship(back_populates="entity")
    telemetry: Mapped[list["TelemetryRecord"]] = relationship(back_populates="entity")
    performance_metrics: Mapped[list["PerformanceMetric"]] = relationship(back_populates="entity")


class Asset(Base):
    """A monitored system or logical asset owned by a SOC entity."""

    __tablename__ = "asset"

    asset_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("soc_entity.entity_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[AssetCategory] = mapped_column(enum_type(AssetCategory), nullable=False)
    criticality: Mapped[Criticality] = mapped_column(enum_type(Criticality), nullable=False)

    # Whether operational telemetry is expected for this asset. Central to later
    # negative-space (missing-monitoring) analysis.
    monitoring_expected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Primary telemetry category expected when monitoring is expected.
    expected_telemetry: Mapped[TelemetryCategory | None] = mapped_column(
        enum_type(TelemetryCategory), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entity: Mapped["SocEntity"] = relationship(back_populates="assets")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="asset")
    telemetry: Mapped[list["TelemetryRecord"]] = relationship(back_populates="asset")
