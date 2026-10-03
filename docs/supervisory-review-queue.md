# Supervisory Review Queue

Phase 10 introduces the **Supervisory Review Queue**: a persistent,
deterministic, human-focused workflow layer that converts the completed
analytical findings (Phases 4–8) and evidence/confidence information
(Phase 9) into a structured list of items awaiting supervisory attention.

## Purpose

The queue answers the question a supervisor faces after analytics have run:
*What do I need to look at, in what order, and what has already been reviewed?*

It is not an autonomous decision engine. Every finding is a signal for human
review. The queue organises those signals, provides context (finding type,
entity, period, evidence availability, confidence), and tracks what was done —
without drawing conclusions about intent, fault, or guilt.

## Design references

**NIST SP 800-61 Rev. 3 (April 2025)** integrates incident response into
cybersecurity risk management and emphasises structured prioritisation of
detection, response, and recovery activities across the CSF 2.0 functions.
The review queue operationalises this by surfacing analytical signals in
priority order for supervisory decision-making.

**NIST SP 800-55 Vol. 1 (December 2024)** provides guidance on selecting and
prioritising information-security measures based on impact/likelihood
considerations and acknowledges the importance of data quality and uncertainty
in measurement results.  The queue's priority derivation applies these
principles; the evidence confidence from Phase 9 is visible but separate from
priority.

---

## Architecture

The queue is implemented in `backend/app/analytics/review_queue/` with the
following modules:

| Module | Purpose |
|---|---|
| `enums.py` | `ReviewStatus`, `QueuePriority`, `FindingCategory`, transition table |
| `priority.py` | Deterministic rule-based priority derivation |
| `models.py` | `ReviewQueueItem` SQLAlchemy ORM model |
| `queue.py` | `build_queue_item`, `upsert_queue_item`, `transition_status` |
| `service.py` | `FindingsBundle`, `QueueSummary`, `build_review_queue`, `fetch_queue_items` |
| `__init__.py` | Public API |

### Persistence

`ReviewQueueItem` is a SQLAlchemy model registered on `Base.metadata` via
`init_db()` in `app/db/session.py`.  The `review_queue_item` table is created
by the existing `Base.metadata.create_all()` call at startup — no separate
migration tool is required and no schema migration is introduced.  SQLite
compatibility is preserved.

### Usage

```python
from app.analytics.review_queue import build_review_queue, FindingsBundle, fetch_queue_items

bundle = FindingsBundle(
    execution_gap=annotate_execution_gap(run_execution_gap_detection(session)),
    negative_space=annotate_negative_space(run_negative_space_detection(session)),
    anomaly=annotate_anomaly(run_anomaly_detection(session)),
    peer_benchmark=annotate_peer_benchmark(run_peer_benchmark(session)),
    metric_risk_div=annotate_metric_risk_divergence(run_metric_risk_divergence(session)),
    fingerprint=annotate_fingerprint(run_investigation_fingerprinting(session)),
)

summary = build_review_queue(session, bundle)
session.commit()

# Retrieve OPEN items, highest priority first.
items = fetch_queue_items(session, status="OPEN")
```

---

## Priority logic

Priority is separate from confidence. A LOW-confidence finding may still
deserve HIGH priority (e.g. a potential escalation gap on a CRITICAL alert,
where the evidence is thin but the operational signal warrants attention).

### Step-by-step derivation

**Step 1: Base priority from finding type.**

| Finding type | Analytic | Base priority |
|---|---|---|
| Critical alert without escalation | EG-001 | HIGH |
| Acknowledged alert without investigation | EG-001 | HIGH |
| Recurring alerts without remediation | EG-001 | MEDIUM |
| Critical asset no telemetry | NS-001 | MEDIUM |
| Telemetry continuity gap | NS-001 | MEDIUM |
| Operational anomaly | AN-001 | HIGH |
| Peer benchmark deviation | PB-001 | MEDIUM |
| Metric-risk divergence | MRD-001 | HIGH |
| Repetitive workflow pattern | IF-REP-001 | LOW |
| Sequence deviation | IF-DEV-002 | LOW |
| Missing expected action | IF-MEA-003 | MEDIUM |

**Step 2: Severity modifier (upward).**  
If `alert_severity` ∈ {HIGH, CRITICAL}: `priority_up` by one step (CRITICAL
is the ceiling).

**Step 3: Confidence modifier (downward).**  
If `evidence_confidence == LOW`: `priority_down` by one step (LOW is the
floor).  MODERATE and HIGH confidence apply no modifier.

**Step 4: Evidence-absence modifier (downward).**  
If `evidence_count == 0`: `priority_down` by one step.

Modifiers are applied in order, non-cumulatively per step.  The logic is
deterministic, auditable, and contains no ML.  Ground truth is never used.

---

## Status lifecycle

```
OPEN ──► IN_REVIEW ──► REVIEWED
  │                        │
  ├──────────────────────► │  (shortcut: OPEN → REVIEWED)
  │                        │
  ▼                        ▼
DISMISSED ◄──────── (reopen from any terminal)
```

| Transition | Meaning |
|---|---|
| OPEN → IN_REVIEW | Supervisor begins review |
| OPEN → REVIEWED | Direct close (no explicit in-review step) |
| OPEN → DISMISSED | Supervisor dismisses without full review |
| IN_REVIEW → REVIEWED | Supervisor completes review |
| IN_REVIEW → OPEN | Hand back for later |
| REVIEWED → OPEN | Reopen (new evidence warrants re-examination) |
| DISMISSED → OPEN | Undismiss |

Invalid transitions raise `InvalidTransitionError`.  The analytical finding
and evidence are never touched by transition operations.

---

## ReviewQueueItem fields

| Field | Type | Description |
|---|---|---|
| `queue_id` | `str(12)` | Deterministic: SHA-256(finding_key)[:12] |
| `finding_key` | `str(256)` | The finding's stable key (from Phases 4–8) |
| `analytic_id` | `str(32)` | e.g. `"EG-001"`, `"AN-001"` |
| `finding_type` | `str(64)` | Reason code / finding-type string |
| `entity_id` | `str(32)` | Subject entity |
| `period_label` | `str(16)` | `"YYYY-MM"` primary reporting period |
| `title` | `str(256)` | Neutral concise title (always contains "Potential") |
| `category` | `FindingCategory` | Broad analytical category |
| `priority` | `QueuePriority` | CRITICAL / HIGH / MEDIUM / LOW |
| `confidence` | `str(16)` | Evidence confidence snapshot (HIGH/MODERATE/LOW/UNKNOWN) |
| `evidence_count` | `int` | Number of EvidenceRef entries at generation time |
| `finding_version` | `str(8)` | SHA-256(key+analytic+entity+period)[:8] for change detection |
| `status` | `ReviewStatus` | OPEN / IN_REVIEW / REVIEWED / DISMISSED |
| `created_at` | `datetime` | When the item was first created |
| `updated_at` | `datetime` | When the item was last modified |
| `reviewed_at` | `datetime \| None` | When REVIEWED or DISMISSED |
| `reviewer_ref` | `str \| None` | Optional opaque reviewer label (not an account ID) |
| `review_note` | `str \| None` | Free-form review note |

---

## Idempotency and versioning

`build_review_queue()` is safe to call repeatedly.

| Condition | Behaviour |
|---|---|
| `queue_id` not in DB | Insert new item, status = OPEN |
| `queue_id` exists, `finding_version` unchanged | Touch `updated_at` only; preserve all review state |
| `queue_id` exists, `finding_version` changed | Update all analytical fields; reset status to OPEN conservatively |

This means:
- Reviewed items stay reviewed across re-runs as long as the underlying
  finding is unchanged.
- If a finding changes materially between assessment runs, its review item
  is reset to OPEN so the supervisor re-examines it.
- Duplicate items are never created.

---

## Synthetic evaluation results

Running against the default synthetic dataset (6 entities, 6 reporting
periods) with all Phase 4–8 analytics:

| Analytic | Findings → Queue items |
|---|---|
| EG-001 Execution Gap | 37 |
| NS-001 Negative Space | 5 |
| AN-001 Anomaly | 4 |
| PB-001 Peer Benchmark | 28 |
| MRD-001 Metric-Risk Divergence | 1 |
| IF Investigation Fingerprinting | 170 |
| **Total** | **245** |

Priority distribution (approximate, varies slightly with synthetic data
run): CRITICAL > 0, HIGH > 0, MEDIUM > 0, LOW > 0 across all categories.

All items start OPEN.  All titles contain "Potential" and contain no
accusatory language.

---

## Limitations

- **No authentication.** `reviewer_ref` is an optional opaque string label,
  not an account ID.  V1 does not implement authentication; Phase 11 API
  will need to decide how to populate this field.
- **No deduplication across analytics.** An execution gap, an anomaly, and a
  peer deviation that all concern the same entity in the same period produce
  three separate queue items.  They share the same `entity_id` and
  `period_label` so a future UI can group them, but the queue itself does not
  merge them (analytically distinct signals deserve separate supervisory
  consideration).
- **No Phase 11 API routes yet.** The queue is a domain/analytics layer only.
  FastAPI endpoints will be added in Phase 11.
- **IF-DEV-002 volume.** The 163 sequence-deviation findings on the synthetic
  dataset all enter the queue.  Given the documented fragile-baseline
  limitation (Phase 8), they are mostly LOW confidence → priority stepped down
  to LOW.  Supervisors can filter by priority; the low-priority items are
  visible but not at the top of the queue.
- **`finding_version` resets.** If an analytical finding changes between runs
  (e.g. a different period_label is assigned), the queue item resets to OPEN.
  This is conservative but correct: stale human decisions should not silently
  persist when the underlying finding changed.

---

## Code organisation

```
backend/app/analytics/review_queue/
    __init__.py      Public API
    enums.py         ReviewStatus, QueuePriority, FindingCategory, transition table
    priority.py      derive_priority(), finding_category() — rule-based, no ML
    models.py        ReviewQueueItem (SQLAlchemy ORM, registered via init_db)
    queue.py         build_queue_item(), upsert_queue_item(), transition_status()
    service.py       FindingsBundle, QueueSummary, build_review_queue(), fetch_queue_items()

backend/tests/
    test_review_queue.py              Unit tests (79)
    test_review_queue_integration.py  Integration tests (38)
```
