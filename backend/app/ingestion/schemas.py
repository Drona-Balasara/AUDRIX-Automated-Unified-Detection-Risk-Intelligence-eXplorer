"""The ingestion schema registry.

An explicit, auditable allowlist mapping each supported dataset type to its
canonical schema. The Phase 2 enumerations (``app.models.enums``) are the single
source of allowed values; no second vocabulary is defined here. Both CSV and
JSON ingestion consult this same registry, so semantic rules are format-neutral.

Dataset types correspond one-to-one to the Phase 2 domain tables. Client input
selects a type by its stable key; physical table and model names are never taken
from the client.
"""

from __future__ import annotations

from app.ingestion.types import (
    ColumnType,
    DatasetSchema,
    FieldSpec,
    ForeignKeySpec,
    TemporalRule,
)
from app.models.enums import (
    ActionOutcome,
    ActionType,
    AlertCategory,
    AlertSeverity,
    AlertStatus,
    AssetCategory,
    Criticality,
    DetectionSource,
    EntityScale,
    EscalationReason,
    EscalationStatus,
    EscalationTarget,
    InvestigationStatus,
    RemediationStatus,
    RemediationType,
    Sector,
    TelemetryCategory,
    TelemetrySourceStatus,
)

# Identifier format patterns (prefix + digits). Deliberately lenient on padding
# width so externally produced datasets are accepted as long as the family and
# shape are correct.
_ID = {
    "entity": r"^ENT-\d+$",
    "asset": r"^AST-\d+$",
    "alert": r"^ALR-\d+$",
    "investigation": r"^INV-\d+$",
    "action": r"^ACT-\d+$",
    "escalation": r"^ESC-\d+$",
    "remediation": r"^REM-\d+$",
    "telemetry": r"^TLM-\d+$",
    "metric": r"^PMET-\d+$",
    "analyst": r"^ANALYST-\d+$",
}

# Placeholder populated in the chunks below.
DATASET_SCHEMAS: dict[str, DatasetSchema] = {}


_ENTITIES = DatasetSchema(
    dataset_type="entities",
    table_name="soc_entity",
    model_name="SocEntity",
    primary_key="entity_id",
    fields=(
        FieldSpec("entity_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["entity"]),
        FieldSpec("name", ColumnType.STRING),
        FieldSpec("sector", ColumnType.ENUM, enum=Sector),
        FieldSpec("peer_group", ColumnType.STRING),
        FieldSpec("scale", ColumnType.ENUM, enum=EntityScale),
        FieldSpec("asset_count_estimate", ColumnType.INT, min_value=0),
        FieldSpec("analyst_headcount", ColumnType.INT, min_value=0),
        FieldSpec("created_at", ColumnType.DATETIME),
        FieldSpec("data_period_start", ColumnType.DATETIME),
        FieldSpec("data_period_end", ColumnType.DATETIME),
    ),
    temporal_rules=(TemporalRule("data_period_start", "data_period_end"),),
)

_ASSETS = DatasetSchema(
    dataset_type="assets",
    table_name="asset",
    model_name="Asset",
    primary_key="asset_id",
    fields=(
        FieldSpec("asset_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["asset"]),
        FieldSpec("entity_id", ColumnType.STRING, id_format=_ID["entity"]),
        FieldSpec("name", ColumnType.STRING),
        FieldSpec("category", ColumnType.ENUM, enum=AssetCategory),
        FieldSpec("criticality", ColumnType.ENUM, enum=Criticality),
        FieldSpec("monitoring_expected", ColumnType.BOOLEAN),
        FieldSpec(
            "expected_telemetry",
            ColumnType.ENUM,
            enum=TelemetryCategory,
            required=False,
            nullable=True,
        ),
        FieldSpec("created_at", ColumnType.DATETIME),
    ),
    foreign_keys=(ForeignKeySpec("entity_id", "entities"),),
)

_ALERTS = DatasetSchema(
    dataset_type="alerts",
    table_name="alert",
    model_name="Alert",
    primary_key="alert_id",
    fields=(
        FieldSpec("alert_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["alert"]),
        FieldSpec("entity_id", ColumnType.STRING, id_format=_ID["entity"]),
        FieldSpec("asset_id", ColumnType.STRING, id_format=_ID["asset"]),
        FieldSpec("severity", ColumnType.ENUM, enum=AlertSeverity),
        FieldSpec("category", ColumnType.ENUM, enum=AlertCategory),
        FieldSpec("detection_source", ColumnType.ENUM, enum=DetectionSource),
        FieldSpec("status", ColumnType.ENUM, enum=AlertStatus),
        FieldSpec("created_at", ColumnType.DATETIME),
        FieldSpec("acknowledged_at", ColumnType.DATETIME, required=False, nullable=True),
        FieldSpec("closed_at", ColumnType.DATETIME, required=False, nullable=True),
        FieldSpec("recurrence_key", ColumnType.STRING, required=False, nullable=True),
        FieldSpec("is_true_positive", ColumnType.BOOLEAN, required=False, nullable=True),
    ),
    foreign_keys=(
        ForeignKeySpec("entity_id", "entities"),
        ForeignKeySpec("asset_id", "assets"),
    ),
    temporal_rules=(
        TemporalRule("created_at", "acknowledged_at"),
        TemporalRule("created_at", "closed_at"),
    ),
)

_INVESTIGATIONS = DatasetSchema(
    dataset_type="investigations",
    table_name="investigation",
    model_name="Investigation",
    primary_key="investigation_id",
    fields=(
        FieldSpec(
            "investigation_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["investigation"]
        ),
        FieldSpec("alert_id", ColumnType.STRING, id_format=_ID["alert"]),
        FieldSpec("entity_id", ColumnType.STRING, id_format=_ID["entity"]),
        FieldSpec("analyst_id", ColumnType.STRING, id_format=_ID["analyst"]),
        FieldSpec("status", ColumnType.ENUM, enum=InvestigationStatus),
        FieldSpec("started_at", ColumnType.DATETIME),
        FieldSpec("ended_at", ColumnType.DATETIME, required=False, nullable=True),
        FieldSpec("duration_seconds", ColumnType.INT, required=False, nullable=True, min_value=0),
        FieldSpec("evidence_count", ColumnType.INT, min_value=0),
    ),
    foreign_keys=(
        ForeignKeySpec("alert_id", "alerts"),
        ForeignKeySpec("entity_id", "entities"),
    ),
    temporal_rules=(TemporalRule("started_at", "ended_at"),),
    unique_columns=("alert_id",),
)

_INVESTIGATION_ACTIONS = DatasetSchema(
    dataset_type="investigation_actions",
    table_name="investigation_action",
    model_name="InvestigationAction",
    primary_key="action_id",
    fields=(
        FieldSpec("action_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["action"]),
        FieldSpec("investigation_id", ColumnType.STRING, id_format=_ID["investigation"]),
        FieldSpec("sequence_number", ColumnType.INT, min_value=0),
        FieldSpec("action_type", ColumnType.ENUM, enum=ActionType),
        FieldSpec("occurred_at", ColumnType.DATETIME),
        FieldSpec("duration_seconds", ColumnType.INT, required=False, nullable=True, min_value=0),
        FieldSpec("outcome", ColumnType.ENUM, enum=ActionOutcome, required=False, nullable=True),
    ),
    foreign_keys=(ForeignKeySpec("investigation_id", "investigations"),),
)

_ESCALATIONS = DatasetSchema(
    dataset_type="escalations",
    table_name="escalation",
    model_name="Escalation",
    primary_key="escalation_id",
    fields=(
        FieldSpec("escalation_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["escalation"]),
        FieldSpec("alert_id", ColumnType.STRING, required=False, nullable=True, id_format=_ID["alert"]),
        FieldSpec(
            "investigation_id",
            ColumnType.STRING,
            required=False,
            nullable=True,
            id_format=_ID["investigation"],
        ),
        FieldSpec("created_at", ColumnType.DATETIME),
        FieldSpec("target", ColumnType.ENUM, enum=EscalationTarget),
        FieldSpec("reason", ColumnType.ENUM, enum=EscalationReason),
        FieldSpec("status", ColumnType.ENUM, enum=EscalationStatus),
        FieldSpec("resolved_at", ColumnType.DATETIME, required=False, nullable=True),
    ),
    foreign_keys=(
        ForeignKeySpec("alert_id", "alerts", required=False),
        ForeignKeySpec("investigation_id", "investigations", required=False),
    ),
    temporal_rules=(TemporalRule("created_at", "resolved_at"),),
)

_REMEDIATIONS = DatasetSchema(
    dataset_type="remediations",
    table_name="remediation",
    model_name="Remediation",
    primary_key="remediation_id",
    fields=(
        FieldSpec("remediation_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["remediation"]),
        FieldSpec("alert_id", ColumnType.STRING, required=False, nullable=True, id_format=_ID["alert"]),
        FieldSpec(
            "investigation_id",
            ColumnType.STRING,
            required=False,
            nullable=True,
            id_format=_ID["investigation"],
        ),
        FieldSpec("created_at", ColumnType.DATETIME),
        FieldSpec("completed_at", ColumnType.DATETIME, required=False, nullable=True),
        FieldSpec("remediation_type", ColumnType.ENUM, enum=RemediationType),
        FieldSpec("status", ColumnType.ENUM, enum=RemediationStatus),
        FieldSpec("successful", ColumnType.BOOLEAN, required=False, nullable=True),
    ),
    foreign_keys=(
        ForeignKeySpec("alert_id", "alerts", required=False),
        ForeignKeySpec("investigation_id", "investigations", required=False),
    ),
    temporal_rules=(TemporalRule("created_at", "completed_at"),),
)

_TELEMETRY = DatasetSchema(
    dataset_type="telemetry",
    table_name="telemetry_record",
    model_name="TelemetryRecord",
    primary_key="telemetry_id",
    fields=(
        FieldSpec("telemetry_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["telemetry"]),
        FieldSpec("entity_id", ColumnType.STRING, id_format=_ID["entity"]),
        FieldSpec("asset_id", ColumnType.STRING, id_format=_ID["asset"]),
        FieldSpec("period_start", ColumnType.DATETIME),
        FieldSpec("period_end", ColumnType.DATETIME),
        FieldSpec("category", ColumnType.ENUM, enum=TelemetryCategory),
        FieldSpec("event_count", ColumnType.INT, min_value=0),
        FieldSpec("activity_level", ColumnType.FLOAT, min_value=0),
        FieldSpec("source_status", ColumnType.ENUM, enum=TelemetrySourceStatus),
        FieldSpec("expected", ColumnType.BOOLEAN),
    ),
    foreign_keys=(
        ForeignKeySpec("entity_id", "entities"),
        ForeignKeySpec("asset_id", "assets"),
    ),
    temporal_rules=(TemporalRule("period_start", "period_end"),),
)

# Ratio fields are normalized ratios in [0, 1] (the Phase 2 model's canonical
# representation); mttr_hours is a non-negative duration in hours.
_RATIO = dict(min_value=0.0, max_value=1.0)

_PERFORMANCE_METRICS = DatasetSchema(
    dataset_type="performance_metrics",
    table_name="performance_metric",
    model_name="PerformanceMetric",
    primary_key="metric_id",
    fields=(
        FieldSpec("metric_id", ColumnType.STRING, is_primary_key=True, id_format=_ID["metric"]),
        FieldSpec("entity_id", ColumnType.STRING, id_format=_ID["entity"]),
        FieldSpec("period_start", ColumnType.DATETIME),
        FieldSpec("period_end", ColumnType.DATETIME),
        FieldSpec("mttr_hours", ColumnType.FLOAT, min_value=0),
        FieldSpec("closure_rate", ColumnType.FLOAT, **_RATIO),
        FieldSpec("sla_compliance", ColumnType.FLOAT, **_RATIO),
        FieldSpec("escalation_rate", ColumnType.FLOAT, **_RATIO),
        FieldSpec("investigation_completeness", ColumnType.FLOAT, **_RATIO),
        FieldSpec("recurrence_rate", ColumnType.FLOAT, **_RATIO),
        FieldSpec("remediation_rate", ColumnType.FLOAT, **_RATIO),
        FieldSpec("evidence_completeness", ColumnType.FLOAT, **_RATIO),
    ),
    foreign_keys=(ForeignKeySpec("entity_id", "entities"),),
    temporal_rules=(TemporalRule("period_start", "period_end"),),
)

# Registered in dependency order (parents before children). The import flow for
# a multi-table dataset should follow this order so relationship checks resolve.
for _schema in (
    _ENTITIES,
    _ASSETS,
    _ALERTS,
    _INVESTIGATIONS,
    _INVESTIGATION_ACTIONS,
    _ESCALATIONS,
    _REMEDIATIONS,
    _TELEMETRY,
    _PERFORMANCE_METRICS,
):
    DATASET_SCHEMAS[_schema.dataset_type] = _schema

# Public, ordered allowlist of supported dataset types.
SUPPORTED_DATASET_TYPES: tuple[str, ...] = tuple(DATASET_SCHEMAS.keys())


def get_schema(dataset_type: str) -> DatasetSchema | None:
    """Return the schema for a dataset type, or ``None`` if unsupported."""
    return DATASET_SCHEMAS.get(dataset_type)
