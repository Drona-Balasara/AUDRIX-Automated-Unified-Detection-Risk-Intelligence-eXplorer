# Investigation Fingerprinting

Phase 8 introduces **investigation fingerprinting**: a deterministic, read-only
analytic that represents SOC investigations as ordered action-type sequences and
identifies potentially unusual, repetitive, incomplete, or template-driven
investigation workflows using transparent sequence analysis.

## What this analytic does — and does not — claim

Findings describe *what was observed* in the action-sequence data. The system
never accuses an analyst, team, or organisation of negligence, misconduct,
fraud, or deliberate gaming. Finding types use neutral titles: **"Potential
Template-Driven Investigation Pattern"**, **"Potential Investigation Sequence
Deviation"**, and **"Potential Missing Investigation Action"**. Analyst
identifiers (opaque synthetic labels such as `ANALYST-001`) appear only as
scope context, never as blame targets. Interpretation requires human supervisory
judgement.

## Analytical framework

### Fingerprint definition

An investigation *fingerprint* is the **ordered tuple of `ActionType` string
values** extracted from an investigation's `InvestigationAction` rows:

```
("OPEN", "ASSET_LOOKUP", "EVENT_SEARCH", "CORRELATION", "EVIDENCE_REVIEW", "VALIDATE", "CLOSE")
```

Sort order: primary key `sequence_number`, tiebreaker `occurred_at` (ascending;
`None` timestamps sort last). Duplicate `sequence_number` values are handled
safely — the tiebreaker keeps output deterministic and they are flagged in the
context record for reference.

The fingerprint is immutable, computed once during context loading, and never
inferred or imputed. Empty fingerprints (no action records) are excluded from
all sequence-comparison detectors rather than silently treated as a valid
sequence.

### `ActionType` vocabulary

11 defined values: `OPEN`, `ASSET_LOOKUP`, `EVENT_SEARCH`, `CORRELATION`,
`CONTEXT_REVIEW`, `EVIDENCE_REVIEW`, `ESCALATE`, `CONTAINMENT_REQUEST`,
`REMEDIATION_REQUEST`, `VALIDATE`, `CLOSE`.

The domain's full-workflow constant (`_FULL_WORKFLOW`) and NIST SP 800-61
Rev. 3 (April 2025) guidance on structured incident detection, analysis, and
validation together justify expecting at least one of `VALIDATE` or
`EVIDENCE_REVIEW` in a complete closed investigation for high-severity alerts.

### Sequence similarity: normalized Levenshtein edit distance

Sequence comparison uses the **normalized Levenshtein edit distance**
([Wagner–Fischer 1974](https://dl.acm.org/doi/10.1145/321796.321811)):

```
distance(a, b) = edit_distance(a, b) / max(len(a), len(b), 1)
```

Result is in `[0.0, 1.0]`; 0.0 = identical, 1.0 = completely dissimilar.
The implementation is a dependency-free 30-line stdlib module
(`app/analytics/investigation_fingerprinting/sequence.py`) — no external
library is added. Process-mining literature confirms edit distance as a
natural, interpretable trace-similarity metric for event logs (van der Aalst
et al., process conformance checking; NIST SP 800-61 Rev. 3 motivates
structured, repeatable IR workflows).

---

## Three detectors

### IF-REP-001 — Potential Template-Driven Investigation Pattern

**What it detects:** Within each (entity, reporting-period) window, counts how
many eligible investigations share the exact same normalized fingerprint. If the
dominant fingerprint appears at least `repetition_threshold` times, a finding
is emitted.

**Why count-only (not rate-gated):** The planted synthetic scenario plants
exactly 4 identical investigations among 27 total in one period (rate ≈ 14 %).
A rate threshold would suppress this legitimate signal. Absolute count is the
more honest detection criterion: 4 or more investigations with an identical
mechanical sequence is notable regardless of the entity's total workload.

**Finding fields:** dominant fingerprint, matching count, window total,
repetition rate (informational), all matching investigation IDs, confidence,
summary.

**Finding key format:** `IF-REP-001:<entity_id>:<YYYY-MM>:<8-char-fp-hash>`

### IF-DEV-002 — Potential Investigation Sequence Deviation

**What it detects:** For each investigation, compares its fingerprint against
the entity's *plurality-baseline* fingerprint derived from prior investigations
(same or earlier reporting period). If the normalized Levenshtein distance
exceeds `deviation_threshold`, a finding is emitted.

**Future-leakage prevention:** The baseline for an investigation in period P
uses only investigations with `period_start ≤ P`. Investigations in P+1 or
later are never included.

**Baseline derivation:** The most common fingerprint among ≥
`min_baseline_investigations` prior eligible investigations. If fewer than
`min_baseline_investigations` exist, the investigation is skipped (no baseline
available, no false finding).

**Finding key format:** `IF-DEV-002:<investigation_id>`

### IF-MEA-003 — Potential Missing Investigation Action

**What it detects:** For `CLOSED` investigations on alerts at or above
`missing_action_min_severity` (default `HIGH`), checks whether at least one of
`VALIDATE` or `EVIDENCE_REVIEW` is present in the fingerprint. If *neither*
appears, a finding is emitted.

**Justification:** The domain's `_FULL_WORKFLOW` constant includes both
`VALIDATE` and `EVIDENCE_REVIEW`. NIST SP 800-61 Rev. 3 (April 2025,
restructured around CSF 2.0) emphasises structured detection and analysis
activities as essential to effective incident response. This rule applies
only to `CLOSED` investigations (incomplete/in-progress investigations are
excluded) and only to high/critical severity alerts.

**Finding key format:** `IF-MEA-003:<investigation_id>`

---

## Configuration

All thresholds live in `app/analytics/investigation_fingerprinting/config.py`.

| Parameter | Default | Purpose |
|---|---|---|
| `min_sequence_length` | 2 | Exclude single-action fingerprints from comparison |
| `min_comparable_investigations` | 3 | Minimum window size before REP/DEV engage |
| `repetition_threshold` | 3 | Minimum identical fingerprints to flag REP |
| `repetition_rate_threshold` | 0.5 | Informational rate stored in finding (not a gate) |
| `deviation_threshold` | 0.6 | Normalized distance ≥ this → DEV finding |
| `min_baseline_investigations` | 3 | Minimum prior investigations for a reliable baseline |
| `missing_action_min_severity` | `"HIGH"` | Minimum alert severity for MEA rule |
| `expected_actions_for_closed_high_crit` | `{"VALIDATE", "EVIDENCE_REVIEW"}` | At least one must be present |

### Default calibration

Defaults are calibrated for the six-entity, six-period synthetic dataset.
Production deployments must recalibrate all thresholds to their entity
population size, investigation volume, and acceptable false-positive rate.

---

## Missing data and edge cases

| Situation | Behaviour |
|---|---|
| Investigation has no action records | Excluded from REP/DEV; assessed by MEA if status/severity qualify |
| `sequence_number` duplicates | Tiebroken by `occurred_at`; flagged in context record |
| `occurred_at` is `None` | Sorts last within same `sequence_number` group |
| Investigation not within any reporting period | Excluded from all detectors |
| Fewer than `min_baseline_investigations` prior investigations | DEV detector skips — no baseline, no false finding |
| Fewer than `min_comparable_investigations` eligible investigations in window | REP detector skips window |
| `OPEN`/`IN_PROGRESS` investigations | MEA rule only applies to `CLOSED` status |
| Non-HIGH/CRITICAL alerts | MEA rule skips (below `missing_action_min_severity`) |

---

## Finding output

Each finding is a frozen Pydantic model. The three types share no common
base class (per existing analytics convention) but follow the same language
discipline and key format.

### `RepetitiveWorkflowFinding`

Key fields: `finding_key`, `analytic_id=IF-REP-001`, `entity_id`,
`window_start`/`window_end`, `period_label`, `dominant_fingerprint`,
`matching_investigation_count`, `window_investigation_count`,
`repetition_rate`, `matching_investigation_ids`, `confidence`, `summary`.

### `SequenceDeviationFinding`

Key fields: `finding_key`, `analytic_id=IF-DEV-002`, `entity_id`,
`investigation_id`, `period_start`/`period_end`, `observed_fingerprint`,
`baseline_fingerprint`, `normalized_distance`, `baseline_investigation_count`,
`confidence`, `summary`.

### `MissingExpectedActionFinding`

Key fields: `finding_key`, `analytic_id=IF-MEA-003`, `entity_id`,
`investigation_id`, `alert_id`, `period_start`/`period_end`,
`observed_fingerprint`, `missing_action_types`, `alert_severity`,
`confidence`, `summary`.

### Confidence levels

`HIGH`, `MODERATE`, `LOW` — data-quality/observational-sufficiency indicators,
not risk scores. Risk/severity assessment is reserved for later phases.

---

## Limitations

- **Small sample.** With 6 periods per entity the plurality baseline is
  derived from a small number of investigations. The baseline is informative
  but not statistically robust; findings are signals for review, not verdicts.
- **Sequence representation.** The fingerprint captures action *types* in
  order but not duration, outcome, or evidence quality. Two investigations with
  the same fingerprint may differ significantly in depth.
- **MEA rule scope.** `VALIDATE` and `EVIDENCE_REVIEW` are the only expected
  actions checked. Other quality dimensions (escalation, evidence count) are
  covered by other analytics (execution-gap, anomaly).
- **No analyst-level grouping.** The repetitive detector groups by
  (entity, period) not (entity, period, analyst). Analyst-level concentration
  is visible in the finding's `matching_investigation_ids` but not a hard gate.
- **No schema migration.** The analytic reads existing tables with no new
  columns or tables required.
- **No new dependencies.** Edit-distance is a stdlib implementation (~30 lines).

---

## Synthetic dataset evaluation

The planted `REPETITIVE_INVESTIGATION_WORKFLOW` scenario (entity ENT-03,
period 2024-03, entity_ctx index 2):

| Property | Value |
|---|---|
| Entity | ENT-03 |
| Period | 2024-03 (March 2024) |
| Planted investigations | 4, all identical |
| Planted fingerprint | `(OPEN, EVENT_SEARCH, EVENT_SEARCH, EVENT_SEARCH, CLOSE)` |
| Analyst | `analyst_pool[0]` of ENT-03 |
| Total investigations in period | 27 |
| Repetition rate | 4/27 ≈ 14.8 % |

**Result:** ENT-03 is detected by IF-REP-001 with `matching_investigation_count=4`,
dominant fingerprint `(OPEN, EVENT_SEARCH, EVENT_SEARCH, EVENT_SEARCH, CLOSE)`,
confidence `MODERATE`. The rate threshold is not used as a gate; the count of
4 ≥ `repetition_threshold=3` is sufficient.

IF-DEV-002 and IF-MEA-003 produce additional findings across the full synthetic
dataset (163 deviation and 131 missing-action findings respectively), reflecting
the varied investigation quality and short sequences throughout the stochastic
baseline.

---

## Code organisation

```
backend/app/analytics/investigation_fingerprinting/
    __init__.py      Public API and module docstring
    sequence.py      build_fingerprint(), edit_distance(), normalized_distance()
    config.py        FingerprintConfig (frozen dataclass, documented defaults)
    context.py       load_context() — bulk-query loader, no N+1
    engine.py        run_fingerprint_detection() — three pure detectors
    findings.py      RepetitiveWorkflowFinding, SequenceDeviationFinding,
                     MissingExpectedActionFinding, FingerprintConfidence
    service.py       run_investigation_fingerprinting() — public service entry point

backend/tests/
    test_investigation_fingerprinting.py              Unit tests (73)
    test_investigation_fingerprinting_integration.py  Integration tests (16)
```
