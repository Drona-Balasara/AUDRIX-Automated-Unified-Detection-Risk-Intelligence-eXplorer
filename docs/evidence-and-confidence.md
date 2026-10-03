# Evidence and Confidence

Phase 9 introduces a shared, reusable evidence and confidence layer that
explains *which source records* support each analytical finding and *how
sufficient* the supporting data is.

## Purpose

Prior phases (4–8) already produce deterministic, neutral findings. Phase 9
answers the supervisor's next question: **why did this fire, which records
support it, and how much should I trust the data quality?**

This layer is the foundation for Phase 10's Supervisory Review Queue, which
will use evidence and confidence to prioritise findings for human review.

## Design references

**NIST SP 800-61 Rev. 3 (April 2025)** emphasises recording and preserving the
provenance of incident-response actions and observations so findings can be
traced to specific source records.  SAT-SA's evidence layer is the analytical
equivalent: every finding is traceable to the database rows that triggered it.

**NIST SP 800-55 Vol. 1 & 2 (December 2024)** acknowledge that measurement
results carry inherent data-quality limitations and uncertainty.  Rather than
fabricating precise confidence percentages, SAT-SA uses a three-level
categorical confidence model (HIGH / MODERATE / LOW) with explicit factor
labels so a reviewer understands *why* confidence is limited, not just *that*
it is.

## Non-fabrication rule

Evidence references MUST correspond to records that actually exist in the
source database.  When a finding depends on the *absence* of an expected
record (e.g. missing telemetry, missing escalation), the absence is
represented as an `EvidenceRef` with `source_type=ABSENCE` and a descriptive
`source_id` scope string.  No fake primary-key IDs are ever created.

---

## Evidence model

### `EvidenceRef` — one source record pointer

| Field | Type | Description |
|---|---|---|
| `source_type` | `EvidenceSourceType` | Vocabulary of source record types |
| `source_id` | `str` | Stable primary key or composite key string |
| `role` | `str` | Record's role relative to the finding |
| `period_label` | `str \| None` | `"YYYY-MM"` period anchor, or `None` |
| `reason` | `str` | Concise explanation (≤ ~200 chars) |

`EvidenceRef` is a frozen Pydantic model: immutable after construction.

### `EvidenceSourceType` vocabulary

| Value | Maps to |
|---|---|
| `ALERT` | `Alert.alert_id` |
| `INVESTIGATION` | `Investigation.investigation_id` |
| `INVESTIGATION_ACTION` | Individual action within an investigation |
| `ESCALATION` | `Escalation` row |
| `REMEDIATION` | `Remediation` row |
| `TELEMETRY_RECORD` | `TelemetryRecord.telemetry_id` |
| `PERFORMANCE_METRIC` | `PerformanceMetric` identified by `{entity_id}:{period_label}:{metric_name}` |
| `ENTITY` | `SocEntity.entity_id` |
| `ASSET` | `Asset.asset_id` |
| `ENTITY_PERIOD_OBSERVATION` | Computed entity-period feature observation (anomaly detection) |
| `PEER_BASELINE` | Computed peer-group baseline (peer benchmarking, sequence deviation) |
| `ABSENCE` | Expected record that was not found; scope described in `source_id` |

### `FindingEvidence` — evidence container for one finding

| Field | Type | Description |
|---|---|---|
| `finding_key` | `str` | Matches the finding's `finding_key` exactly |
| `analytic_id` | `str` | Analytic identifier (e.g. `"EG-001"`, `"AN-001"`) |
| `evidence_refs` | `tuple[EvidenceRef, ...]` | Ordered, deduplicated refs (sorted by `(source_type, source_id)`) |
| `confidence` | `EvidenceConfidence` | Data-quality confidence level |
| `confidence_factors` | `tuple[ConfidenceFactor, ...]` | Factors that led to this level |
| `confidence_note` | `str` | One-line human-readable explanation |

---

## Confidence model

### `EvidenceConfidence` levels

| Level | Meaning |
|---|---|
| `HIGH` | Multiple direct source records; complete observations; no significant data-quality concerns |
| `MODERATE` | Adequate evidence, but at least one limiting factor (small population, some dropped records, absence-based) |
| `LOW` | Thin or fragile evidence base; baseline from very few prior cases; finding at exact minimum threshold |

Confidence is **data sufficiency**, not risk or severity.  A LOW-confidence
finding is still valid — it warrants review but should be treated as a
preliminary signal.

### `ConfidenceFactor` labels

| Factor | Category | Meaning |
|---|---|---|
| `MULTIPLE_DIRECT_RECORDS` | Positive | ≥ 3 direct source records cited |
| `COMPLETE_OBSERVATIONS` | Positive | No dropped or missing observations |
| `LARGE_COMPARISON_POP` | Positive | ≥ 6 comparable peers or observations |
| `ADEQUATE_RECORD_COUNT` | Neutral | 1–2 direct records (sufficient but minimal) |
| `MODERATE_BASELINE` | Neutral/moderate | 3–5 baseline observations |
| `PARTIAL_OBSERVATIONS` | Moderate | Some observations incomplete |
| `SINGLE_RECORD` | Moderate limiter | Exactly 1 direct source record |
| `DROPPED_OBSERVATIONS` | Moderate limiter | Some observations were dropped |
| `SMALL_BASELINE` | LOW limiter | Baseline from < min adequate observations |
| `FRAGILE_BASELINE` | LOW limiter | Baseline explicitly documented as fragile |
| `INSUFFICIENT_PERIODS` | LOW limiter | Fewer than recommended observation periods |
| `ABSENCE_BASED` | Context | Finding depends on absence of evidence |
| `MINIMUM_THRESHOLD` | LOW limiter | Finding is at the exact minimum detection threshold |

### Aggregation rule (weakest-factor wins)

1. No refs → `LOW`
2. Only `ABSENCE` refs → `LOW`
3. Any `LOW` limiter factor present → `LOW`
4. `ABSENCE_BASED` with direct records present → `MODERATE`
5. Any `MODERATE` limiter factor present → `MODERATE`
6. Otherwise → `HIGH`

---

## Per-analytic evidence and confidence

### Phase 4 — Execution Gap (`EG-001`)

**Evidence refs per finding:**
- `ALERT` — the triggering alert (direct source)
- `ALERT` × N — related recurring alerts (for remediation-gap findings)
- `INVESTIGATION` — the linked investigation, if one exists
- `ASSET` — the affected monitored asset
- `ABSENCE` — the expected-but-missing escalation, investigation, or remediation

**Confidence:** Typically `MODERATE` because all execution-gap findings
involve an absence.  Recurring-remediation findings with ≥ 3 related alerts
may reach `HIGH`.

### Phase 5 — Negative Space (`NS-001`)

**Evidence refs per finding:**
- `ASSET` — the monitored asset subject to the monitoring gap
- `TELEMETRY_RECORD` × N — the last-observed telemetry rows (for
  TELEMETRY_DISAPPEARANCE findings; empty for total MONITORING_GAP findings)
- `ABSENCE` — the missing expected telemetry signal

**Confidence:** MONITORING_GAP findings (zero telemetry ever) are typically
`LOW` (absence-only evidence).  TELEMETRY_DISAPPEARANCE findings with observed
baseline rows are `MODERATE`.

### Phase 6 — Anomaly Detection (`AN-001`)

**Evidence refs per finding:**
- `ENTITY_PERIOD_OBSERVATION` — the scored entity-period observation
- `ENTITY_PERIOD_OBSERVATION` × 2 — notable feature deviations (descriptive context)

**Confidence:** Derived from population size and anomaly signal magnitude.
Findings with normalised score < 0.55 (close to threshold) get
`MINIMUM_THRESHOLD` → `LOW`.  Small populations (<15 complete observations)
get `SMALL_BASELINE` → `LOW`.

### Phase 6 — Peer Benchmarking (`PB-001`)

**Evidence refs per finding:**
- `PERFORMANCE_METRIC` — the subject entity-period KPI observation
- `PEER_BASELINE` — the computed peer-median baseline (peer group + period)

**Confidence:** Derived from `peer_population_count`.
Zero-MAD (identical peers, `ABSOLUTE_ZERO_MAD` basis) → `FRAGILE_BASELINE` → `LOW`.
2 peers → `SMALL_BASELINE` → `LOW`.
3–5 peers → `MODERATE_BASELINE` → `MODERATE`.
≥ 6 peers → `LARGE_COMPARISON_POP` → `HIGH`.

### Phase 7 — Metric-Risk Divergence (`MRD-001`)

**Evidence refs per finding:**
- `PERFORMANCE_METRIC` × (N_periods × N_metrics) — one ref per (period, metric)
  in both headline and quality groups

**Confidence:** Mapped directly from Phase 7's own `ConfidenceLevel` field;
no recalculation.  `ConfidenceLevel.HIGH → EvidenceConfidence.HIGH`, etc.
Phase 7's `confidence_note` is reused as-is.

### Phase 8 — Investigation Fingerprinting

**IF-REP-001 (Repetitive Workflow):**
- `INVESTIGATION` × N — all investigations sharing the dominant fingerprint

Confidence: `count == 3 (threshold)` → `MINIMUM_THRESHOLD` → `LOW`.
`count ≥ 6` and `rate ≥ 0.80` → `HIGH`.

**IF-DEV-002 (Sequence Deviation):**
- `INVESTIGATION` — the deviating investigation
- `PEER_BASELINE` — the entity plurality-baseline fingerprint

Confidence: always at most `MODERATE` due to `FRAGILE_BASELINE` (baseline
< 5 prior investigations) or `MODERATE_BASELINE` (5–7 prior investigations).
This explicitly documents the Phase 8 documented limitation that IF-DEV-002
produces many findings from the synthetic dataset because the baseline is
fragile.

**IF-MEA-003 (Missing Expected Action):**
- `INVESTIGATION` — the closed incomplete investigation
- `ALERT` — the associated high/critical severity alert
- `ABSENCE` × N — one ref per missing expected action type

Confidence: typically `MODERATE` (2 direct records + absence).  Very short
sequences (≤ 2 actions) get `MINIMUM_THRESHOLD` → `LOW`.

---

## Integration pattern

Phase 9 does **not** modify the frozen Phase 4–8 finding models.  Evidence
and confidence are delivered as a parallel mapping:

```python
from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.evidence import annotate_execution_gap

result = run_execution_gap_detection(session)          # existing, unchanged
annotated = annotate_execution_gap(result)             # Phase 9 wrapper

for finding in result.findings:
    ev = annotated.evidence_for(finding.finding_key)   # FindingEvidence | None
    print(ev.confidence, ev.confidence_note)
    for ref in ev.evidence_refs:
        print(ref.source_type, ref.source_id, ref.reason)
```

The original `result` is preserved unchanged inside `annotated.result`.

---

## Limitations

- **No new DB queries.** Evidence is built from the already-loaded context
  passed in through the existing detection pipeline.  If a builder needs
  information not available in the current context object, it is documented
  as a limitation rather than silently omitted or fabricated.
- **Anomaly and peer-benchmark findings have no direct record IDs.** These
  analytics operate at entity-period grain and the existing finding models
  carry only `entity_id`.  Evidence references are `ENTITY_PERIOD_OBSERVATION`
  and `PEER_BASELINE` computed observations, not raw ORM row IDs.
- **IF-DEV-002 confidence is at most MODERATE.** The Phase 8 documented
  limitation (fragile baseline from few prior investigations) is explicitly
  reflected in the evidence layer.  All 163 IF-DEV-002 findings on the
  synthetic dataset carry `LOW` or `MODERATE` confidence, not `HIGH`.
- **`INVESTIGATION_ACTION`, `ESCALATION`, `REMEDIATION` source types** are
  defined in the vocabulary but not currently used by any builder.  They are
  available for future phases or future refinements of existing builders.
- **Confidence is not a risk score.** A `LOW`-confidence finding is not a
  weak finding — it may be correct but rests on limited data.  Phase 10
  (Supervisory Review Queue) will decide how confidence interacts with
  prioritisation.

---

## Code organisation

```
backend/app/analytics/evidence/
    __init__.py      Public API and module docstring
    model.py         EvidenceRef, FindingEvidence, EvidenceSourceType,
                     EvidenceConfidence, ConfidenceFactor, make_finding_evidence
    confidence.py    aggregate_confidence(), factors_from_counts(),
                     note_from_confidence()
    builders.py      Six evidence builders, one per analytic
    annotated.py     Six AnnotatedResult wrappers (one per analytic)
    service.py       Six annotate_*() service functions

backend/tests/
    test_evidence.py              Unit tests (77)
    test_evidence_integration.py  Cross-phase integration tests (28)
```
