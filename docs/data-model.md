# Data Model

This document describes the SAT-SA domain data model (Phase 2) and the
reproducible synthetic dataset generated against it.

## Scope and intent

SAT-SA assesses **SOC operational evidence**. The model is deliberately smaller
than a SIEM: it captures the operational lifecycle of security work — alerts,
investigations and their ordered actions, escalations, remediations, telemetry
presence, and reported performance — at a granularity sufficient to detect
execution gaps, missing expected evidence, unusual patterns, and divergence
between reported performance and operational reality. It does **not** store raw
packets, payloads, credentials, malware samples, or real personal data.

### Normalization influence (not an OCSF implementation)

Field and enumeration design is conceptually influenced by vendor-neutral event
normalization such as the Open Cybersecurity Schema Framework (OCSF): controlled
vocabularies, severity/criticality scales, and category taxonomies. SAT-SA is
**not** an OCSF implementation and makes **no** OCSF compatibility or
certification claim. The schema is purpose-built for supervisory analytics.

## Conventions

- **Identifiers** are synthetic, stable, and deterministic for a given seed:
  prefixed, zero-padded strings allocated in generation order — `ENT-01`,
  `AST-00001`, `ALR-000001`, `INV-000001`, `ACT-0000001`, `ESC-00001`,
  `REM-00001`, `TLM-000001`, `PMET-0001`, and `SCN-0001` (ground truth).
- **Timestamps** are timezone-aware UTC everywhere (`DateTime(timezone=True)` in
  the ORM; ISO-8601 with offset in the files). There are no naive datetimes.
- **Enumerations** are controlled vocabularies defined in
  `backend/app/models/enums.py`. They are stored as their string values
  (portable `VARCHAR` + `CHECK`, `native_enum=False`) so the data is identical
  across SQLite and other backends and serializes cleanly to CSV/JSON.
- **Reporting periods** are consecutive monthly `[start, end)` windows. The
  default dataset spans 6 periods beginning 2024-01-01 UTC.

## Entities and relationships

```
SocEntity 1───* Asset
SocEntity 1───* Alert            Asset 1───* Alert
Alert     1───1 Investigation    Investigation 1───* InvestigationAction
Alert/Investigation 1───* Escalation
Alert/Investigation 1───* Remediation
SocEntity 1───* TelemetryRecord  Asset 1───* TelemetryRecord
SocEntity 1───* PerformanceMetric
```

<!-- DATAMODEL-ENTITIES -->

### SocEntity (`soc_entity`)

A comparable operational unit (a SOC) being assessed and benchmarked.

| Field | Type | Notes |
| --- | --- | --- |
| `entity_id` | PK, str | `ENT-NN`. |
| `name` | str | Synthetic label. |
| `sector` | enum `Sector` | FINANCE, HEALTHCARE, TECHNOLOGY, … |
| `peer_group` | str, indexed | Benchmarking group; here the sector, so multiple entities are legitimate peers. |
| `scale` | enum `EntityScale` | SMALL / MEDIUM / LARGE; drives volume. |
| `asset_count_estimate` | int | Approximate asset count. |
| `analyst_headcount` | int | Size of the synthetic analyst pool. |
| `created_at`, `data_period_start`, `data_period_end` | datetime | History bounds for the entity. |

**Rationale.** Multiple entities share a `peer_group` so later peer benchmarking
has valid comparisons; `scale` produces correlated-but-not-exaggerated volume
differences rather than being the only difference.

### Asset (`asset`)

A monitored system or logical asset owned by an entity.

| Field | Type | Notes |
| --- | --- | --- |
| `asset_id` | PK, str | `AST-NNNNN`. |
| `entity_id` | FK → `soc_entity` | Owner. |
| `name` | str | Synthetic label. |
| `category` | enum `AssetCategory` | SERVER, WORKSTATION, DATABASE, … |
| `criticality` | enum `Criticality` | LOW / MEDIUM / HIGH / CRITICAL. |
| `monitoring_expected` | bool | Whether telemetry is expected — central to missing-monitoring analysis. |
| `expected_telemetry` | enum `TelemetryCategory`, nullable | Primary expected category when monitored. |
| `created_at` | datetime | |

**Rationale.** HIGH/CRITICAL assets are always expected to be monitored; the gap
between "expected" and "actually present" telemetry is what later negative-space
analysis evaluates.

### Alert (`alert`)

An operational security alert raised for an asset.

| Field | Type | Notes |
| --- | --- | --- |
| `alert_id` | PK, str | `ALR-NNNNNN`. |
| `entity_id`, `asset_id` | FK | Owner and subject. |
| `severity` | enum `AlertSeverity` | Influences the whole downstream lifecycle. |
| `category` | enum `AlertCategory` | MALWARE, PHISHING, … |
| `detection_source` | enum `DetectionSource` | Detection family, not a product name. |
| `status` | enum `AlertStatus` | NEW, ACKNOWLEDGED, IN_INVESTIGATION, CLOSED, SUPPRESSED. |
| `created_at` | datetime | |
| `acknowledged_at`, `closed_at` | datetime, nullable | Lifecycle timestamps. |
| `recurrence_key` | str, nullable, indexed | Shared key grouping recurring alerts (same asset + pattern). |
| `is_true_positive` | bool, nullable | Known once an investigation closes. |

### Investigation (`investigation`) and InvestigationAction (`investigation_action`)

An investigation of a single alert (one-to-one), composed of ordered actions.

Investigation fields: `investigation_id` (PK), `alert_id` (FK, unique),
`entity_id` (FK), `analyst_id` (synthetic, e.g. `ANALYST-001`), `status`
(`InvestigationStatus`: OPEN, IN_PROGRESS, CLOSED, ABANDONED), `started_at`,
`ended_at` (nullable), `duration_seconds` (nullable), `evidence_count`.

Action fields: `action_id` (PK), `investigation_id` (FK), `sequence_number`,
`action_type` (`ActionType`: OPEN … CLOSE), `occurred_at`, `duration_seconds`
(nullable), `outcome` (`ActionOutcome`, nullable). A unique constraint on
`(investigation_id, sequence_number)` guarantees a well-ordered, contiguous
sequence; actions are stored individually (not as one workflow string) so later
phases can reconstruct and compare investigation sequences.

**Rationale.** Duration and evidence scale with severity; a short, evidence-free
investigation of a critical alert is therefore detectable as anomalous.

<!-- DATAMODEL-ENTITIES-2 -->

### Escalation (`escalation`) and Remediation (`remediation`)

Both reference an alert and/or an investigation; their **presence or deliberate
absence** is what later analytics evaluate.

Escalation: `escalation_id` (PK), nullable `alert_id`/`investigation_id` FKs,
`created_at`, `target` (`EscalationTarget`), `reason` (`EscalationReason`),
`status` (`EscalationStatus`), `resolved_at` (nullable).

Remediation: `remediation_id` (PK), nullable `alert_id`/`investigation_id` FKs,
`created_at`, `completed_at` (nullable), `remediation_type` (`RemediationType`),
`status` (`RemediationStatus`), `successful` (bool, nullable). Remediation is an
operational response (isolation, credential reset, patch, …), never a payload.

### TelemetryRecord (`telemetry_record`)

Aggregate evidence that an asset produced expected telemetry over a period —
counts/activity, not raw events.

Fields: `telemetry_id` (PK), `entity_id`/`asset_id` FKs, `period_start`,
`period_end`, `category` (`TelemetryCategory`), `event_count`, `activity_level`
(normalized, ~1.0 at baseline), `source_status` (`TelemetrySourceStatus`:
HEALTHY/DEGRADED/SILENT), `expected` (bool). Missing records for a monitored
asset/period are themselves the signal.

### PerformanceMetric (`performance_metric`)

Reported SOC performance per entity per period, as **explicit typed fields** (not
an opaque JSON blob) so later phases can compare reported figures against
operational evidence.

- **Headline KPIs** (the figures a SOC reports upward): `mttr_hours`,
  `closure_rate`, `sla_compliance`, `escalation_rate`.
- **Operational-quality indicators** (how sound the work actually was):
  `investigation_completeness`, `recurrence_rate`, `remediation_rate`,
  `evidence_completeness`.

Metrics are **derived** from the underlying records for each entity/period, so
they are internally consistent with the operational data — except where a
scenario deliberately makes reported KPIs diverge from reality (see below).

## Generated files

Written to `data/synthetic/` (CSV and JSON per table, with explicit pandas
dtypes — never type inference):

`entities`, `assets`, `alerts`, `investigations`, `investigation_actions`,
`escalations`, `remediations`, `telemetry`, `performance_metrics`, plus
`manifest.json` (row counts) and the separate `ground_truth` file.

<!-- DATAMODEL-SCENARIOS -->

## Ground truth (evaluation data)

Ground truth is written separately as `ground_truth.{csv,json}`. It is **not** an
operational table and must never be exposed through production APIs or dashboards
in later phases; it exists only to evaluate detections.

Each row has: `scenario_id`, `scenario_type`, the affected
`entity_id`/`asset_id`/`alert_id`/`investigation_id`/`recurrence_key` (as
applicable), `period_start`/`period_end`, `expected_detection_category`, and a
concise machine-readable `reason`.

Every scenario is backed by **real record relationships**, not just a label. A
large normal baseline substantially outnumbers the planted scenarios.

| Scenario type | Expected detection | How it is represented in the data |
| --- | --- | --- |
| `NORMAL_BASELINE` | `NONE` | Negative controls: confirmed critical/high alerts, fully investigated, escalated, closed with complete evidence. Must **not** be flagged. |
| `CRITICAL_ALERT_WITHOUT_ESCALATION` | `ESCALATION_GAP` | A confirmed CRITICAL alert, fully investigated and closed, with **no** escalation record (baseline criticals usually escalate). |
| `ACKNOWLEDGED_WITHOUT_INVESTIGATION` | `INVESTIGATION_GAP` | A HIGH/CRITICAL alert acknowledged within SLA with **no** investigation opened (baseline high/critical almost always investigate). |
| `SUSPICIOUSLY_FAST_INVESTIGATION` | `INVESTIGATION_QUALITY` | A critical/high alert "investigated" in under two minutes with only OPEN/CLOSE actions and ~no evidence. |
| `RECURRING_ALERTS_WITHOUT_REMEDIATION` | `REMEDIATION_GAP` | Five confirmed recurring alerts on one asset (shared `recurrence_key`) across periods with **no** completed remediation. |
| `MISSING_TELEMETRY_CRITICAL_ASSET` | `MONITORING_GAP` | A CRITICAL asset with `monitoring_expected=True` that produces **no** telemetry in any period. |
| `TELEMETRY_DISAPPEARANCE` | `TELEMETRY_DISAPPEARANCE` | An asset with normal telemetry for the first periods that then goes silent for the remainder. |
| `REPETITIVE_INVESTIGATION_WORKFLOW` | `WORKFLOW_REPETITION` | One analyst closing multiple alerts with an identical, mechanical action sequence. |
| `METRIC_RISK_DIVERGENCE` | `METRIC_RISK_DIVERGENCE` | For one entity, reported headline KPIs improve across periods while evidence/remediation quality fall and recurrence rises; underlying later-period records are genuinely degraded. |

## Reproducibility

A run is fully determined by `GenerationConfig` plus the seed. The documented
default seed is **20240601**. Re-running with the same seed produces an identical
dataset (verified in the test suite). Generate with:

```bash
cd backend && python -m app.datagen --seed 20240601
```

## Mapping to the ORM and database initialization

File column names match the SQLAlchemy models one-to-one, so a dataset row maps
directly onto its ORM class. Importing `app.models` registers every model on
`Base.metadata`; `init_db()` runs `create_all`, which is additive and idempotent
— re-initializing an existing (Phase 1) database adds the new tables without
dropping data or duplicating system metadata. No destructive resets are
performed, and no migration tool is introduced yet; the structure leaves room to
add one later.



