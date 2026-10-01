"""Operational domain models.

Alerts and their investigations, the ordered actions within an investigation,
and the escalation / remediation activity associated with them. Escalation and
remediation reference an alert and/or an investigation; their presence (or
deliberate absence) is what later analytics evaluate.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.common import enum_type
from app.models.enums import (
    ActionOutcome,
    ActionType,
    AlertCategory,
    AlertSeverity,
    AlertStatus,
    DetectionSource,
    EscalationReason,
    EscalationStatus,
    EscalationTarget,
    InvestigationStatus,
    RemediationStatus,
    RemediationType,
)

if TYPE_CHECKING:
    from app.models.organization import Asset, SocEntity

class Alert(Base):
    """An operational security alert raised for an asset within an entity."""

    __tablename__ = "alert"

    alert_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("soc_entity.entity_id"), nullable=False, index=True
    )
    asset_id: Mapped[str] = mapped_column(
        ForeignKey("asset.asset_id"), nullable=False, index=True
    )

    severity: Mapped[AlertSeverity] = mapped_column(enum_type(AlertSeverity), nullable=False)
    category: Mapped[AlertCategory] = mapped_column(enum_type(AlertCategory), nullable=False)
    detection_source: Mapped[DetectionSource] = mapped_column(
        enum_type(DetectionSource), nullable=False
    )
    status: Mapped[AlertStatus] = mapped_column(enum_type(AlertStatus), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Shared, non-sensitive key grouping alerts that recur (same asset + pattern).
    recurrence_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_true_positive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    entity: Mapped["SocEntity"] = relationship(back_populates="alerts")
    asset: Mapped["Asset"] = relationship(back_populates="alerts")
    investigation: Mapped["Investigation | None"] = relationship(
        back_populates="alert", uselist=False
    )
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="alert")
    remediations: Mapped[list["Remediation"]] = relationship(back_populates="alert")


class Investigation(Base):
    """An investigation of a single alert, composed of ordered actions."""

    __tablename__ = "investigation"

    investigation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alert_id: Mapped[str] = mapped_column(
        ForeignKey("alert.alert_id"), nullable=False, unique=True, index=True
    )
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("soc_entity.entity_id"), nullable=False, index=True
    )

    # Synthetic operator identifier (e.g. ANALYST-001); never a real identity.
    analyst_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[InvestigationStatus] = mapped_column(
        enum_type(InvestigationStatus), nullable=False
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Non-sensitive measure of evidence gathered during the investigation.
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    alert: Mapped["Alert"] = relationship(back_populates="investigation")
    entity: Mapped["SocEntity"] = relationship(back_populates="investigations")
    actions: Mapped[list["InvestigationAction"]] = relationship(
        back_populates="investigation", order_by="InvestigationAction.sequence_number"
    )
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="investigation")
    remediations: Mapped[list["Remediation"]] = relationship(back_populates="investigation")

class InvestigationAction(Base):
    """A single ordered action within an investigation.

    Stored individually (not as a workflow string) so later phases can
    reconstruct and compare investigation sequences.
    """

    __tablename__ = "investigation_action"
    __table_args__ = (
        UniqueConstraint("investigation_id", "sequence_number", name="uq_action_sequence"),
    )

    action_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigation.investigation_id"), nullable=False, index=True
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[ActionType] = mapped_column(enum_type(ActionType), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[ActionOutcome | None] = mapped_column(enum_type(ActionOutcome), nullable=True)

    investigation: Mapped["Investigation"] = relationship(back_populates="actions")


class Escalation(Base):
    """Escalation of an alert and/or investigation to a target role."""

    __tablename__ = "escalation"

    escalation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alert_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert.alert_id"), nullable=True, index=True
    )
    investigation_id: Mapped[str | None] = mapped_column(
        ForeignKey("investigation.investigation_id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    target: Mapped[EscalationTarget] = mapped_column(enum_type(EscalationTarget), nullable=False)
    reason: Mapped[EscalationReason] = mapped_column(enum_type(EscalationReason), nullable=False)
    status: Mapped[EscalationStatus] = mapped_column(enum_type(EscalationStatus), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    alert: Mapped["Alert | None"] = relationship(back_populates="escalations")
    investigation: Mapped["Investigation | None"] = relationship(back_populates="escalations")


class Remediation(Base):
    """Operational remediation activity for an alert and/or investigation."""

    __tablename__ = "remediation"

    remediation_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    alert_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert.alert_id"), nullable=True, index=True
    )
    investigation_id: Mapped[str | None] = mapped_column(
        ForeignKey("investigation.investigation_id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remediation_type: Mapped[RemediationType] = mapped_column(
        enum_type(RemediationType), nullable=False
    )
    status: Mapped[RemediationStatus] = mapped_column(enum_type(RemediationStatus), nullable=False)
    successful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    alert: Mapped["Alert | None"] = relationship(back_populates="remediations")
    investigation: Mapped["Investigation | None"] = relationship(back_populates="remediations")
