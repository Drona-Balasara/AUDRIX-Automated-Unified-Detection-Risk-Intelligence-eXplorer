"""Table schemas for the synthetic dataset.

Each table declares its column order and an explicit pandas dtype token per
column so the writer never relies on type inference. Column names match the
SQLAlchemy models exactly, so the emitted files map one-to-one onto the ORM.

dtype tokens:
  "string"   -> pandas StringDtype (nullable text)
  "int"      -> int64 (non-null integer)
  "Int64"    -> pandas nullable integer
  "float"    -> float64
  "boolean"  -> pandas nullable boolean
  "datetime" -> tz-aware datetime64[ns, UTC]
"""

from __future__ import annotations

# Ordered (column, dtype-token) pairs per table. Keys are the output file stems.
TABLE_SCHEMAS: dict[str, list[tuple[str, str]]] = {
    "entities": [
        ("entity_id", "string"),
        ("name", "string"),
        ("sector", "string"),
        ("peer_group", "string"),
        ("scale", "string"),
        ("asset_count_estimate", "int"),
        ("analyst_headcount", "int"),
        ("created_at", "datetime"),
        ("data_period_start", "datetime"),
        ("data_period_end", "datetime"),
    ],
    "assets": [
        ("asset_id", "string"),
        ("entity_id", "string"),
        ("name", "string"),
        ("category", "string"),
        ("criticality", "string"),
        ("monitoring_expected", "boolean"),
        ("expected_telemetry", "string"),
        ("created_at", "datetime"),
    ],
    "alerts": [
        ("alert_id", "string"),
        ("entity_id", "string"),
        ("asset_id", "string"),
        ("severity", "string"),
        ("category", "string"),
        ("detection_source", "string"),
        ("status", "string"),
        ("created_at", "datetime"),
        ("acknowledged_at", "datetime"),
        ("closed_at", "datetime"),
        ("recurrence_key", "string"),
        ("is_true_positive", "boolean"),
    ],
    "investigations": [
        ("investigation_id", "string"),
        ("alert_id", "string"),
        ("entity_id", "string"),
        ("analyst_id", "string"),
        ("status", "string"),
        ("started_at", "datetime"),
        ("ended_at", "datetime"),
        ("duration_seconds", "Int64"),
        ("evidence_count", "int"),
    ],
    "investigation_actions": [
        ("action_id", "string"),
        ("investigation_id", "string"),
        ("sequence_number", "int"),
        ("action_type", "string"),
        ("occurred_at", "datetime"),
        ("duration_seconds", "Int64"),
        ("outcome", "string"),
    ],
    "escalations": [
        ("escalation_id", "string"),
        ("alert_id", "string"),
        ("investigation_id", "string"),
        ("created_at", "datetime"),
        ("target", "string"),
        ("reason", "string"),
        ("status", "string"),
        ("resolved_at", "datetime"),
    ],
    "remediations": [
        ("remediation_id", "string"),
        ("alert_id", "string"),
        ("investigation_id", "string"),
        ("created_at", "datetime"),
        ("completed_at", "datetime"),
        ("remediation_type", "string"),
        ("status", "string"),
        ("successful", "boolean"),
    ],
    "telemetry": [
        ("telemetry_id", "string"),
        ("entity_id", "string"),
        ("asset_id", "string"),
        ("period_start", "datetime"),
        ("period_end", "datetime"),
        ("category", "string"),
        ("event_count", "int"),
        ("activity_level", "float"),
        ("source_status", "string"),
        ("expected", "boolean"),
    ],
    "performance_metrics": [
        ("metric_id", "string"),
        ("entity_id", "string"),
        ("period_start", "datetime"),
        ("period_end", "datetime"),
        ("mttr_hours", "float"),
        ("closure_rate", "float"),
        ("sla_compliance", "float"),
        ("escalation_rate", "float"),
        ("investigation_completeness", "float"),
        ("recurrence_rate", "float"),
        ("remediation_rate", "float"),
        ("evidence_completeness", "float"),
    ],
}

# Ground truth is evaluation data (kept separate from the operational tables and
# never surfaced through production APIs/dashboards in later phases).
GROUND_TRUTH_SCHEMA: list[tuple[str, str]] = [
    ("scenario_id", "string"),
    ("scenario_type", "string"),
    ("entity_id", "string"),
    ("asset_id", "string"),
    ("alert_id", "string"),
    ("investigation_id", "string"),
    ("recurrence_key", "string"),
    ("period_start", "datetime"),
    ("period_end", "datetime"),
    ("expected_detection_category", "string"),
    ("reason", "string"),
]

# File stems that make up the operational dataset (ground truth excluded).
TABLE_NAMES: tuple[str, ...] = tuple(TABLE_SCHEMAS.keys())
