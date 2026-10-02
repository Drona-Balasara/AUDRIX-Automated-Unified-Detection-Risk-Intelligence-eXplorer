"""ORM models.

Phase 1 defined only :class:`SystemMetadata`. Phase 2 adds the SAT-SA domain
model: SOC entities and assets, alerts and investigations with ordered actions,
escalations and remediations, telemetry records, and periodic performance
metrics. Importing this package registers every model on ``Base.metadata`` so
``create_all`` (in :func:`app.db.session.init_db`) creates all tables.

No ingestion or analytics behavior lives here; these are data definitions only.
"""

from __future__ import annotations

from app.models.ingestion import ImportRecord
from app.models.monitoring import PerformanceMetric, TelemetryRecord
from app.models.operations import (
    Alert,
    Escalation,
    Investigation,
    InvestigationAction,
    Remediation,
)
from app.models.organization import Asset, SocEntity
from app.models.system_metadata import SystemMetadata

__all__ = [
    "SystemMetadata",
    "SocEntity",
    "Asset",
    "Alert",
    "Investigation",
    "InvestigationAction",
    "Escalation",
    "Remediation",
    "TelemetryRecord",
    "PerformanceMetric",
    "ImportRecord",
]
