# Negative-Space Detection

Phase 5 introduces SAT-SA's second analytic: **negative-space detection**. Where
execution-gap detection (Phase 4) inspects how recorded work was handled,
negative-space detection inspects what the record *fails to contain* — but only
where existing data establishes that something was reasonably expected. It is
deterministic, read-only, and deliberately conservative.

## What "negative space" means

Negative space is the **absence of expected evidence**. A security operations
centre is expected to produce a continuous monitoring trail for the assets it
watches; gaps in that trail can indicate a visibility problem. The central
discipline of this phase, drawn from established monitoring guidance (NIST on
tracking the logging status of sources, CISA on establishing a baseline of
normal behaviour, MITRE on declaring expected telemetry sources rather than
treating every missing record as suspicious), is simple:

> **Absence is not automatically evidence of failure.** A finding is emitted
> only when existing data provides sufficient evidence that a particular
> operational signal was *expected* during a defined observation window.

"No rows exist" is never equated with "negative space" without an explicit
expectation condition. This is the difference between an asset that was never
meant to be monitored (silence is normal) and a critical asset that is flagged
monitoring-expected yet produced nothing (silence is a potential gap).

## Detection philosophy

- **Expectation before absence.** Each rule first establishes that evidence was
  expected — using real asset/domain fields (`monitoring_expected`,
  `criticality`, `expected_telemetry`) or the asset's own observed telemetry
  history — and only then reports the absence.
- **Observational, never accusatory.** Findings describe a *potential*
  monitoring coverage or telemetry continuity gap. They never assert that
  telemetry was intentionally disabled, tampered with, or maliciously
  suppressed; the source data does not contain such evidence and this phase does
  not invent it.
- **Deterministic and explainable.** Identical database state plus identical
  configuration always produce identical findings. No randomness, no machine
  learning, no language model, no external service. Every finding carries a
  stable key and a machine-readable reason code.
- **Conservative.** A rule fires only when its required source data is present
  and the observed condition is unambiguous. Thresholds are explicit and
  configurable; missing *unrelated* data is never treated as evidence of a gap.
- **No scoring.** This phase assigns no risk or confidence numbers and does not
  rank findings. Evidence resolution, confidence, prioritization, and
  supervisory review remain later phases.

<!-- NS-APPEND -->

## Assessment scope and time

A run operates over one or more **SOC entities**. Temporal reasoning is anchored
to each entity's observed data horizon (`data_period_start` / `data_period_end`)
and to each asset's own observed telemetry — **never** to wall-clock time — so a
run over a fixed dataset is reproducible regardless of when it runs. All
timestamp comparisons are timezone-aware UTC; values read back from SQLite
(which does not persist a timezone) are interpreted as UTC per the domain
invariant.

## Rule registry

All rules live in an explicit allowlist (`REGISTRY` in
`app/analytics/negative_space/rules.py`) so every enabled rule is discoverable
and testable. Each rule has a stable identifier, a human-readable name and
description, an `enabled` flag, and a pure `evaluate(context, config)` method.
Rule ownership is disjoint, so one underlying condition is reported by exactly
one rule.

| Rule   | Name                             | Gap type                  | Reason code |
|--------|----------------------------------|---------------------------|-------------|
| NS-001 | Critical asset without telemetry | `MONITORING_GAP`          | `CRITICAL_ASSET_NO_TELEMETRY` |
| NS-002 | Telemetry disappearance          | `TELEMETRY_DISAPPEARANCE` | `TELEMETRY_CONTINUITY_GAP` |

### NS-001 — Critical asset without telemetry

**Expectation established by:** the asset's own fields. The asset must be flagged
`monitoring_expected` (configurable via `monitoring_require_expected_flag`) and
be at least `monitoring_min_criticality` (default `CRITICAL`). The entity's
observed window must also be at least `monitoring_min_observation_hours`
(default 24h) long, so a trivially short dataset cannot produce the finding.

**Fires when** such an asset has **no telemetry record of any category** across
the entire observed window.

**Why criticality is required.** The schema does not record a universal "this
asset must be monitored" policy, so the detector does not invent one. It uses
the strongest expectation the data actually supports: an asset the inventory
marks both monitoring-expected and critical. Lower-criticality assets that
happen to lack telemetry are not flagged by default (the floor is configurable).

**Data dependencies.** `asset` (`monitoring_expected`, `criticality`,
`expected_telemetry`), `soc_entity` (`data_period_start`, `data_period_end`),
`telemetry` (absence of any row for the asset).

**False-positive handling.** Assets without an established monitoring
expectation (not monitoring-expected, or below the criticality floor) are never
flagged. An asset with *any* telemetry is out of scope for NS-001 — a partial
signal is a continuity question owned by NS-002, not a total coverage gap.

### NS-002 — Telemetry disappearance

**Expectation established by:** the asset's **own historical telemetry**, not a
fixed heartbeat applied to all assets. The asset must have at least
`disappearance_min_baseline_periods` (default 2) telemetry records, establishing
a meaningful prior observation pattern.

**Fires when** the trailing silence — from the asset's last observed telemetry
`period_end` to the entity's `data_period_end` — reaches at least
`telemetry_gap_threshold_hours` (default 720h ≈ 30 days). Silence **equal to**
the threshold fires.

**Why a baseline is required.** An asset that was never (or barely) observed
cannot be said to have "disappeared"; that is a NS-001 coverage question, not a
continuity one. Requiring a prior baseline is what distinguishes *"no telemetry
ever observed"* from *"telemetry previously observed, then absent."*

**Dataset-boundary safety.** Silence is measured only at the **trailing** edge of
the window, from the asset's genuine last-seen record. The detector never infers
telemetry that would have existed *before* the dataset began, so a dataset that
starts immediately after an asset's real history is not misread as a gap. An
asset still reporting through `data_period_end` has zero trailing silence and is
never flagged.

**Data dependencies.** `asset` (`monitoring_expected`), `telemetry`
(`period_start`, `period_end` history for the asset), `soc_entity`
(`data_period_end`).

**False-positive handling.** Assets with fewer than the minimum baseline records
are excluded; trailing gaps below the threshold are excluded; assets reporting
to the window end are excluded.

## Rule ownership and non-duplication

- **Within Phase 5:** NS-001 requires *zero* telemetry; NS-002 requires a
  baseline of *at least two* records. The conditions are disjoint by
  construction, so no asset is reported by both rules.
- **Against Phase 4:** execution-gap rules reason about alert handling
  (alerts, investigations, escalations, remediations). Negative-space rules
  reason about monitoring telemetry. The two analytics share no observable
  condition; e.g. "acknowledged alert without investigation" is strictly an
  execution-gap concern and "missing telemetry" is strictly a negative-space
  concern. Both detectors run read-only over the same database without altering
  each other's results.

## Configuration

Thresholds live in the immutable `NegativeSpaceConfig` dataclass
(`app/analytics/negative_space/config.py`) — no magic numbers appear in rule
bodies. The documented defaults are:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `monitoring_require_expected_flag` | `True` | Only consider assets flagged `monitoring_expected`. |
| `monitoring_min_criticality` | `CRITICAL` | Minimum asset criticality for NS-001. |
| `monitoring_min_observation_hours` | `24.0` | Minimum observed window before a total absence is meaningful. |
| `telemetry_gap_threshold_hours` | `720.0` | Trailing silence (~30 days) before NS-002 treats a source as disappeared. |
| `disappearance_min_baseline_periods` | `2` | Prior telemetry records required to establish a NS-002 baseline. |

**Why these defaults.** They are calibrated against the deterministic synthetic
dataset, whose monitored assets report on a ~30-day cadence across a six-month
window. A monitored asset that stops reporting leaves a multi-month trailing
silence, so a ~30-day threshold distinguishes a genuine disappearance from an
asset that merely missed its most recent report, while a two-record baseline
excludes assets that were never meaningfully observed. The defaults were chosen
from the data's semantics, not tuned to make a test pass — the integration
tests assert that every planted scenario is a subset of what is detected and
that findings stay a small fraction of all assets, not an exact count. **These
values are reasonable for the synthetic/local assessment context only;
production deployments must calibrate them to their own inventory and cadence.**

A caller may pass a customized instance; invalid values (negative hours, a
baseline minimum below 1) raise at construction.

## Structured finding schema

Each finding is a frozen Pydantic model (`NegativeSpaceFinding`) carrying:

- `finding_key` — deterministic identity (`NS-001:AST-00205`,
  `NS-002:AST-00207`); no random UUIDs.
- `rule_id`, `gap_type`, `reason_code` — stable identifiers for grouping.
- `entity_id`, `asset_id` — stable references for later evidence resolution.
- `related_telemetry_ids` — the source telemetry rows establishing a NS-002
  baseline (empty for NS-001, where the expectation is schema-derived).
- `observation_start` / `observation_end` — timezone-aware UTC window, anchored
  to the entity data horizon (NS-001) or the asset's last-seen boundary and the
  horizon (NS-002).
- `last_evidence_at` — the last observed telemetry boundary (NS-002).
- `summary`, `expected_evidence`, `observed_evidence` — human-readable, strictly
  observational wording.
- `asset_criticality`, `expected_telemetry`, `baseline_observation_count`,
  `silence_hours` — non-scoring observed context (facts, not scores).

The engine returns a `NegativeSpaceResult` with the entities and rule ids that
ran and the ordered, deduplicated findings. The result carries no wall-clock
timestamp, so two runs over identical data compare equal.

## Determinism and deduplication

- Input records are loaded once per run and sorted by stable id / period; rules
  iterate entities and assets in sorted order. Output does not depend on
  database row order.
- Findings are deduplicated by `finding_key` and ordered by
  `(rule_id, finding_key)`, so overlapping evaluation cannot emit the same
  condition twice and ordering is stable.
- No wall-clock time is read anywhere in detection, so results cannot shift with
  the system clock.

## Expected behaviour on the synthetic dataset

Run against the default-seed synthetic dataset (210 assets, 978 telemetry
records across 6 entities), the detector reports **5 findings** — well under 10%
of assets:

- **NS-001 (3):** `AST-00205` and `AST-00206` are the two planted
  missing-telemetry critical assets. `AST-00202` is a third critical,
  monitoring-expected asset that genuinely has no telemetry: it was created by
  an execution-gap scenario (it carries an alert) and, because the generator
  emits telemetry before planting scenarios, never received any. It is **not** a
  planted missing-telemetry scenario, but by the observable-evidence definition
  it is a correct, conservative finding — a critical monitored asset with no
  telemetry — not a schema false positive. This mirrors the Phase 4 observation
  that realistic baselines contain a few genuine extras.
- **NS-002 (2):** `AST-00207` and `AST-00208`, the two planted disappearance
  assets, each with three telemetry periods ending 2024-04-01 and then silence
  through the 2024-07-01 horizon (≈2184h).

The detector does **not** flag the labelled normal-baseline controls, does not
flag the seven monitoring-expected zero-telemetry assets below the criticality
floor as coverage gaps, and does not classify any zero-telemetry asset as a
disappearance. The ground-truth manifest is used only by the test suite as an
evaluation oracle; it is never an input to detection.

## Known limitations

- Detection reflects only what the records contain. If the source omits
  telemetry that actually existed, the detector cannot know that; conversely a
  telemetry gap is a *potential visibility issue* and does not by itself prove
  tampering, malicious activity, or intentional disabling.
- The schema records a per-asset `monitoring_expected` flag and criticality but
  no richer monitoring policy (e.g. expected cadence per source), so NS-001
  anchors its expectation on criticality. This is a deliberate, documented
  limitation rather than an invented universal policy.
- The rules intentionally cover only the two well-determined negative-space
  conditions present in the Phase 2 scenario catalogue. Other families (anomaly
  detection, peer benchmarking, metric–risk divergence, investigation
  fingerprinting) are out of scope for Phase 5.
- Thresholds are conservative defaults for the synthetic context, not tuned
  production policy; they are configurable per run.

## Security and logging

Detection is strictly **read-only**: it never writes, flushes, or mutates ORM
state, verified by tests that compare row counts before and after. Logging emits
only safe operational metadata — rule ids, entity-scope size, per-rule finding
counts, and run duration. It never logs telemetry rows, asset or alert contents,
credentials, or uploaded file contents, and there is no verbose debug logging by
default.

## Programmatic usage

The detector has no HTTP surface in Phase 5; it is a service-layer entry point,
independent of FastAPI:

```python
from app.analytics.negative_space import run_negative_space_detection

result = run_negative_space_detection(session)          # all entities, defaults
for finding in result.findings:
    print(finding.rule_id, finding.summary)

print(result.counts_by_rule())
```

A custom configuration or entity scope may be passed:

```python
from app.analytics.negative_space import NegativeSpaceConfig
from app.models.enums import Criticality

result = run_negative_space_detection(
    session,
    config=NegativeSpaceConfig(
        monitoring_min_criticality=Criticality.HIGH,
        telemetry_gap_threshold_hours=168.0,
    ),
    entity_ids=["ENT-01"],
)
```

