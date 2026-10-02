# Execution-Gap Detection

Phase 4 introduces SAT-SA's first analytic: **execution-gap detection**. It
reads the normalized SOC operational record and reports *observable* deviations
in how alerts were handled — for example, a confirmed critical alert that was
investigated and closed but never escalated. It is deterministic, read-only, and
deliberately conservative.

## Purpose

A security operations centre produces a trail of operational records: alerts,
investigations and their ordered actions, escalations, and remediations.
Execution-gap detection inspects that trail for places where an expected
operational step appears to be missing, given what the records themselves show.
Each result is a **finding** — a structured, strictly factual statement of an
observed condition that a human supervisor can review.

## Detection philosophy

- **Observational, not accusatory.** A finding describes what the records show
  ("confirmed critical alert closed with no recorded escalation"), never intent,
  negligence, misconduct, or performance judgement. The wording distinguishes
  the *expected* condition from the *observed* one.
- **Deterministic and explainable.** Identical database state plus identical
  configuration always produce identical findings. There is no randomness, no
  machine learning, no language model, and no external service. Every finding
  carries a stable key and a machine-readable reason code.
- **Conservative.** A rule fires only when its required source data is actually
  present and the observed condition is unambiguous in the schema. Missing
  *unrelated* data is never treated as evidence of a gap.
- **No scoring.** This phase does not assign risk or confidence numbers and does
  not rank findings. Evidence resolution, confidence, prioritization, and
  supervisory review are explicitly later phases; the finding schema is shaped
  to feed them without duplicating them.
- **Strategy separated from analytics.** Rule definitions and their
  configuration are separate from input access, rule evaluation, and the
  structured result. (This separation is an architectural choice; SAT-SA is not
  an ATT&CK implementation and carries no threat-intel identifiers.)

## Assessment scope

A run operates over one or more **SOC entities**. Temporal reasoning is anchored
to each entity's observed data horizon (`data_period_end`), never to wall-clock
time, so a run over a fixed dataset is reproducible regardless of when it runs.
By default every entity in the database is in scope; a caller may restrict the
run to specific entity ids.

## Rule registry

All rules live in an explicit allowlist (`REGISTRY` in
`app/analytics/execution_gap/rules.py`) so every enabled rule is discoverable and
testable. Each rule has a stable identifier, a human-readable name and
description, an `enabled` flag, and a pure `evaluate(context, config)` method.
Rule ownership is disjoint, so one underlying problem is reported by exactly one
rule.

| Rule   | Name                                        | Gap type           | Reason code |
|--------|---------------------------------------------|--------------------|-------------|
| EG-001 | Critical alert without escalation           | `ESCALATION_GAP`   | `CRITICAL_ALERT_NO_ESCALATION` |
| EG-002 | Acknowledged alert without investigation    | `INVESTIGATION_GAP`| `ACKNOWLEDGED_ALERT_NO_INVESTIGATION` |
| EG-003 | Recurring confirmed alerts without remediation | `REMEDIATION_GAP` | `RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION` |

### EG-001 — Critical alert without escalation

**Fires when** an alert is all of: severity at least `escalation_min_severity`
(default `CRITICAL`); status `CLOSED`; confirmed true-positive (when
`escalation_require_true_positive` is set, the default); has a recorded
investigation; and has **no** escalation referencing either the alert or its
investigation.

**Why the investigation is required.** A closed, confirmed critical alert with no
investigation record at all is a *different* condition; EG-001 deliberately does
not claim it, to keep rule ownership clean and avoid double-reporting.

**Data dependencies.** `alert` (severity, status, `is_true_positive`),
`investigation` (alert → investigation link), `escalation` (by `alert_id` and by
`investigation_id`).

**False-positive handling.** Disproven alerts (`is_true_positive` false/unknown)
are excluded by default, as are non-critical and non-closed alerts. An escalation
linked through the investigation (not directly to the alert) still counts as an
escalation, so legitimately escalated work is never flagged.

### EG-002 — Acknowledged alert without investigation

**Fires when** an alert is all of: it has an `acknowledged_at` timestamp; its
status is still `ACKNOWLEDGED`; severity is at least
`investigation_min_severity` (default `HIGH`); it has **no** linked
investigation; and at least `investigation_expected_within_hours` (default 24h)
have elapsed between acknowledgement and the entity's `data_period_end`.

**Why the elapsed-time gate.** An alert acknowledged very close to the end of the
observed window may simply be pending investigation rather than missing one. The
gate is measured against the deterministic data horizon, not wall-clock time, so
the boundary is reproducible. Elapsed time **equal to** the threshold fires.

**Data dependencies.** `alert` (status, severity, `acknowledged_at`),
`investigation` (absence of an alert → investigation link), `soc_entity`
(`data_period_end`).

**False-positive handling.** Only alerts still in `ACKNOWLEDGED` status qualify;
an alert that progressed (e.g. `IN_INVESTIGATION`, `CLOSED`) or was never
acknowledged is excluded. Severity below the threshold is excluded.

### EG-003 — Recurring confirmed alerts without remediation

**Fires when**, for one entity and one `recurrence_key`, there are at least
`remediation_min_recurring_true_positives` (default 3) confirmed true-positive
alerts and **none** of them has a `COMPLETED` remediation (linked by alert or by
investigation).

**Data dependencies.** `alert` (`recurrence_key`, `is_true_positive`),
`investigation` (alert → investigation link), `remediation` (status `COMPLETED`,
by `alert_id` and by `investigation_id`).

**False-positive handling.** Only confirmed true positives count toward the
threshold, so a cluster of disproven or unconfirmed alerts never triggers it. A
remediation that is `REQUESTED`, `IN_PROGRESS`, `FAILED`, or `CANCELLED` is **not**
treated as completed — the recurring problem is only considered addressed by a
`COMPLETED` remediation.

## Configuration

Thresholds and windows live in the immutable `ExecutionGapConfig` dataclass
(`app/analytics/execution_gap/config.py`) — no magic numbers appear in rule
bodies. The documented production defaults are:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `escalation_min_severity` | `CRITICAL` | Minimum severity for EG-001. |
| `escalation_require_true_positive` | `True` | EG-001 only considers confirmed alerts. |
| `investigation_min_severity` | `HIGH` | Minimum severity for EG-002. |
| `investigation_expected_within_hours` | `24.0` | Elapsed hours (ack → data horizon) before EG-002 treats an investigation as missing. |
| `remediation_min_recurring_true_positives` | `3` | Confirmed recurring alerts required before EG-003 fires. |

A caller may pass a customized instance; invalid values (negative hours, a
minimum count below 1) raise at construction.

## Structured finding schema

Each finding is a frozen Pydantic model (`ExecutionGapFinding`) carrying:

- `finding_key` — deterministic identity (e.g. `EG-001:ALR-000776`,
  `EG-003:ENT-02:RK-...`); no random UUIDs.
- `rule_id`, `gap_type`, `reason_code` — stable identifiers for grouping.
- `entity_id`, and where applicable `asset_id`, `alert_id`, `investigation_id`,
  `related_alert_ids`, `recurrence_key` — stable references for later evidence
  resolution.
- `observed_at` / `window_start` / `window_end` — timezone-aware UTC observation
  time or window, sourced from the domain records.
- `summary`, `expected_condition`, `observed_condition` — human-readable,
  strictly observational wording.
- `alert_severity` — non-scoring observed context.

The engine returns an `ExecutionGapResult` with the entities and rule ids that
ran and the ordered, deduplicated findings. The result carries no wall-clock
timestamp, so two runs over identical data compare equal.

## Determinism and deduplication

- Input records are loaded once per run and sorted by stable id; rules iterate
  entities in sorted order. Output does not depend on database row order.
- Findings are deduplicated by `finding_key` and ordered by
  `(rule_id, finding_key)`, so overlapping or repeated evaluation cannot emit
  the same condition twice and the ordering is stable.
- All timestamps are timezone-aware UTC. Values read back from SQLite (which does
  not persist a timezone) are interpreted as UTC per the domain invariant.

## Expected behavior on the synthetic dataset

Run against the default-seed synthetic dataset (801 alerts across 6 entities),
the detector reports a small number of findings (well under 10% of alerts). It
detects every planted execution-gap scenario — critical-without-escalation,
acknowledged-without-investigation, and recurring-without-remediation — and does
**not** flag the labelled normal-baseline controls. The planted ground-truth
manifest is used only by the test suite as an evaluation oracle; it is never an
input to detection.

Because the synthetic baseline is realistic, a handful of baseline alerts
genuinely exhibit the same observable conditions (e.g. a confirmed critical alert
that happened not to be escalated). These are correct, conservative findings, not
false positives against the schema — they describe real observable conditions in
the records.

## Known limitations

- Detection reflects only what the records contain. If the source data omits an
  escalation that actually occurred, the detector cannot know that.
- The rules intentionally cover only three well-determined conditions. Other
  Phase 2 scenario families (investigation quality, monitoring/telemetry gaps,
  workflow repetition, metric–risk divergence) are **out of scope** for Phase 4
  and are left to later phases.
- Thresholds are deliberately conservative defaults, not tuned policy; they are
  configurable per run.

## Security and logging

Detection is strictly **read-only**: it never writes, flushes, or mutates ORM
state, and this is verified by tests that compare row counts before and after.
Logging emits only safe operational metadata — rule ids, entity-scope size,
per-rule finding counts, and run duration. It never logs alert, investigation,
or any record contents, and there is no verbose debug logging by default.

## Programmatic usage

The detector has no HTTP surface in Phase 4; it is a service-layer entry point,
independent of FastAPI:

```python
from app.analytics.execution_gap import run_execution_gap_detection

result = run_execution_gap_detection(session)          # all entities, defaults
for finding in result.findings:
    print(finding.rule_id, finding.summary)

print(result.counts_by_rule())
```

A custom configuration or entity scope may be passed:

```python
from app.analytics.execution_gap import ExecutionGapConfig
from app.models.enums import AlertSeverity

result = run_execution_gap_detection(
    session,
    config=ExecutionGapConfig(investigation_expected_within_hours=48.0),
    entity_ids=["ENT-01"],
)
```


