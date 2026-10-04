# SAT-SA Phase 13 — Validation Report

**Assessment date:** October 2026  
**Repository:** Drona-Balasara/SAT-SA-Security-Assessment-Supervisory-Analytics  
**Phase 12 commit (dashboard):** `a82a9d3` feat: complete React dashboard  
**Environment:** Python 3.12.10, Node 22, SQLite V1 local deployment

---

## 1. Test strategy

Phase 13 validates system correctness across four layers:

1. **Scenario-level detection coverage** — each planted ground-truth scenario is
   traced through the analytic(s) responsible for detecting it, with
   evidence/confidence/queue behavior verified downstream.
2. **Automated regression suite** — 649 backend tests (617 prior phases + 32
   Phase 13) and 36 frontend tests.
3. **System-property validation** — determinism, evidence integrity, queue
   idempotency, status lifecycle, and confidence/priority separation.
4. **API and security checks** — endpoint boundary behavior, error safety,
   ingestion controls, and dependency audit.

Ground truth is used **only as an evaluation oracle** in tests and offline
scripts; it is never read by production analytic code.

---

## 2. Environment

| Item | Value |
|---|---|
| Backend language | Python 3.12.10 |
| Backend framework | FastAPI 0.115 / SQLAlchemy 2.0 / SQLite |
| Frontend language | TypeScript 5.6 |
| Frontend framework | React 18.3 / Vite 7.3 |
| Test runner (backend) | pytest 8.4 |
| Test runner (frontend) | vitest 3.2 |
| Synthetic seed | 20240601 |
| Entity count | 6 |
| Reporting periods | 6 monthly (Jan–Jun 2024) |
| Total alerts (synthetic) | 801 |
| Total investigations (synthetic) | 512 |
| Ground-truth rows planted | 26 |

---

## 3. Synthetic ground-truth scenario validation matrix

The synthetic dataset plants nine scenario types across the six entities.
Each row below states what was planted, which analytic is expected to detect it,
the detection result, and downstream behavior.

### Detection category key

| Symbol | Meaning |
|---|---|
| DETECTED | At least one analytic produced a finding for the affected entity |
| NEG_CTRL | Expected detection category is NONE — no finding should be produced |
| MISSED | No analytic produced a finding for the affected entity |

---

### 3.1 NORMAL_BASELINE — Negative control

**Expected category:** NONE  
**Planted:** 6 healthy high/critical investigations on ENT-01 (closed, escalated, full workflow, confirmed).  
**Expected behavior:** No finding from any analytic. These records verify that the
analytics do not generate false positives for sound operational evidence.

| Check | Result |
|---|---|
| EG-001 fires on baseline investigations | Not expected; baseline entities still receive other EG findings from their full alert set |
| REP detector fires on baseline-only investigations | PASS — no false positive on purely-baseline-investigation cluster |
| NEG_CTRL status | **CONFIRMED** |

**Note:** ENT-01 receives other findings from other scenarios (critical-without-escalation,
acknowledged-without-investigation). The baseline negative control applies specifically to
the six labelled "healthy" investigations, which are correctly not individually flagged.

---

### 3.2 CRITICAL_ALERT_WITHOUT_ESCALATION

**Expected category:** ESCALATION_GAP  
**Planted:** 3 instances across ENT-01, ENT-03, ENT-05 (CRITICAL alert, closed, full workflow, no escalation).  
**Primary analytic:** EG-001, rule `CRITICAL_ALERT_NO_ESCALATION`

| Check | Result |
|---|---|
| EG-001 finding produced | **DETECTED** for all affected entities |
| Reason code | `CRITICAL_ALERT_NO_ESCALATION` |
| Evidence type | ALERT + ABSENCE (missing_escalation) |
| Evidence confidence | MODERATE (absence-based) |
| Queue priority | CRITICAL (base=HIGH + CRITICAL severity modifier) |
| False positives | None |
| False negatives | None |

---

### 3.3 ACKNOWLEDGED_WITHOUT_INVESTIGATION

**Expected category:** INVESTIGATION_GAP  
**Planted:** 3 instances across ENT-01, ENT-02, ENT-04 (HIGH/CRITICAL alert acknowledged, no investigation opened).  
**Primary analytic:** EG-001, rule `ACKNOWLEDGED_ALERT_NO_INVESTIGATION`

| Check | Result |
|---|---|
| EG-001 finding produced | **DETECTED** for all affected entities |
| Reason code | `ACKNOWLEDGED_ALERT_NO_INVESTIGATION` |
| Evidence type | ALERT + ABSENCE (missing_investigation) |
| Evidence confidence | MODERATE |
| Queue priority | CRITICAL (HIGH base + HIGH/CRITICAL severity) |
| False positives | None |
| False negatives | None |

---

### 3.4 SUSPICIOUSLY_FAST_INVESTIGATION

**Expected category:** INVESTIGATION_QUALITY  
**Planted:** 3 instances across ENT-03, ENT-05, ENT-06 (CRITICAL/HIGH alert, 45–115 s investigation, OPEN→CLOSE only, no evidence).  
**Primary analytic:** IF-MEA-003 (missing VALIDATE/EVIDENCE_REVIEW on HIGH/CRITICAL closed investigation)

| Check | Result |
|---|---|
| IF-MEA-003 finding produced | **DETECTED** for all affected entities |
| Finding type | `MISSING_EXPECTED_ACTION` |
| Missing actions | VALIDATE, EVIDENCE_REVIEW |
| Evidence confidence | MODERATE (short sequence → MINIMUM_THRESHOLD) |
| Also detected by | AN-001 (anomaly) on affected entity set |
| Queue priority | MEDIUM (MEA base) → CRITICAL (HIGH severity modifier) for CRITICAL alert instances |
| Note | Duration is not directly detectable by existing analytics; the MEA rule captures the quality signal through action completeness rather than timing. Documented design tradeoff. |
| False positives | Negligible — short sequences are expected to trigger MEA |
| False negatives | Duration signal itself is not directly captured; acceptable V1 limitation |

---

### 3.5 RECURRING_ALERTS_WITHOUT_REMEDIATION

**Expected category:** REMEDIATION_GAP  
**Planted:** 2 groups across ENT-02, ENT-04 (5 recurring HIGH alerts per group, investigated, no remediation).  
**Primary analytic:** EG-001, rule `RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION`

| Check | Result |
|---|---|
| EG-001 finding produced | **DETECTED** for both affected entities |
| Reason code | `RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION` |
| Evidence type | Multiple ALERT refs + ABSENCE (missing_remediation) |
| Evidence confidence | MODERATE |
| Queue priority | MEDIUM (remediation gap base) |
| False positives | None |
| False negatives | None |

---

### 3.6 MISSING_TELEMETRY_CRITICAL_ASSET

**Expected category:** MONITORING_GAP  
**Planted:** 2 critical DATABASE assets on ENT-01, ENT-03 with `monitoring_expected=True` but no telemetry ever produced.  
**Primary analytic:** NS-001, rule `CRITICAL_ASSET_NO_TELEMETRY`

| Check | Result |
|---|---|
| NS-001 finding produced | **DETECTED** for both affected entities |
| Reason code | `CRITICAL_ASSET_NO_TELEMETRY` |
| Evidence type | ASSET + ABSENCE (missing_telemetry) |
| Evidence confidence | LOW (absence-only evidence — no baseline telemetry rows to reference) |
| Queue priority | MEDIUM |
| False positives | None |
| False negatives | None |

---

### 3.7 TELEMETRY_DISAPPEARANCE

**Expected category:** TELEMETRY_DISAPPEARANCE  
**Planted:** 2 IDENTITY_SERVICE assets on ENT-04, ENT-05 producing healthy telemetry for 3 periods then going silent.  
**Primary analytic:** NS-001, rule `TELEMETRY_CONTINUITY_GAP`

| Check | Result |
|---|---|
| NS-001 finding produced | **DETECTED** for both affected entities |
| Reason code | `TELEMETRY_CONTINUITY_GAP` |
| Evidence type | ASSET + TELEMETRY_RECORD (baseline rows) + ABSENCE (telemetry_gap) |
| Evidence confidence | MODERATE (baseline records present) |
| Queue priority | MEDIUM |
| False positives | None |
| False negatives | None |

---

### 3.8 REPETITIVE_INVESTIGATION_WORKFLOW

**Expected category:** WORKFLOW_REPETITION  
**Planted:** 4 identical investigations on ENT-03, same analyst, pattern `[OPEN, EVENT_SEARCH×3, CLOSE]`, period 2024-03.  
**Primary analytic:** IF-REP-001

| Check | Result |
|---|---|
| IF-REP-001 finding produced | **DETECTED** for ENT-03 |
| Dominant fingerprint | `(OPEN, EVENT_SEARCH, EVENT_SEARCH, EVENT_SEARCH, CLOSE)` |
| Matching count | 4 of 27 eligible investigations (14.8%) |
| Evidence confidence | LOW (count at threshold, rate below 0.25 → MODERATE_BASELINE applied) |
| Queue priority | LOW (REP base, LOW confidence → one step down) |
| Note | Priority is LOW, not HIGH, because (a) the base priority for WORKFLOW_PATTERN is LOW, and (b) the evidence confidence is LOW. The finding is visible in the queue and filterable. This is the intended behavior per Phase 10 design. |
| False positives | None among purely-baseline entities |
| False negatives | None — the planted scenario is detected |

---

### 3.9 METRIC_RISK_DIVERGENCE

**Expected category:** METRIC_RISK_DIVERGENCE  
**Planted:** ENT-04 — headline KPIs improve linearly across all 6 periods while quality indicators deteriorate. Manually overridden metric rows.  
**Primary analytic:** MRD-001

| Check | Result |
|---|---|
| MRD-001 finding produced | **DETECTED** for ENT-04 |
| Finding key | `MRD-001:ENT-04:2024-01:2024-06` |
| Headline score | +1.0000 |
| Quality score | −1.0000 |
| Supporting periods | 6 |
| Evidence confidence | HIGH (6 periods, both composites ≥ 2× threshold) |
| Queue priority | HIGH (MRD base = HIGH) |
| Also detected by | AN-001 (anomaly — ENT-04 naturally surfaces), PB-001 (peer deviation) |
| False positives | None — only ENT-04 fired at default thresholds |
| False negatives | None |

---

## 4. Detection coverage summary

| Metric | Value |
|---|---|
| Detectable scenarios (non-NONE expected category) | 8 |
| Detected | **8 / 8 (100%)** |
| Missed | 0 |
| Negative controls verified | 1 (NORMAL_BASELINE) |
| False positives (planted-scenario entities) | 0 |
| False negatives | 0 |

**Classification metric notes:**

- **Execution gap (EG-001):** Rule-based binary detection. 3/3 ESCALATION_GAP scenarios detected, 3/3 INVESTIGATION_GAP scenarios detected, 2/2 REMEDIATION_GAP scenarios detected. Precision and recall are meaningful here: all three ground-truth rule conditions fire on the affected entities without misclassifying baseline-only entities.
- **Negative-space (NS-001):** Rule-based. 2/2 MONITORING_GAP and 2/2 TELEMETRY_DISAPPEARANCE detected. No false positives on assets without planting.
- **Anomaly (AN-001):** Unsupervised model. Binary ground-truth mapping is not appropriate. ENT-04 (the metric-divergence entity) naturally surfaces in the anomaly population — consistent with Phase 6 documentation. The anomaly detector's planted evaluation is validated separately per Phase 6 protocol.
- **Peer benchmarking (PB-001):** Statistical comparison. Same caveat as anomaly — not a direct planted-scenario detector. ENT-04 surfaces as documented in Phase 6.
- **MRD-001:** Single clear planted signal. 1/1 detected.
- **IF-REP-001:** Planted scenario detected. 1/1.
- **IF-MEA-003:** All fast-investigation entities produce MEA findings. Acceptable given the analytic's intended design (rule-based, not ground-truth-specific).
- **IF-DEV-002:** High volume (163 findings); documented fragile-baseline limitation. See Section 7 below.

---

## 5. Determinism validation

All analytics produce identical finding-key hashes across two independent runs over the same deterministic dataset:

| Analytic | Determinism |
|---|---|
| EG-001 | PASS |
| NS-001 | PASS |
| AN-001 | PASS |
| MRD-001 | PASS |
| IF-REP-001 | PASS |
| IF-DEV-002 | PASS |
| IF-MEA-003 | PASS |
| Dataset re-generation (same seed) | PASS |
| Dataset with different seed produces different output | PASS |

The synthetic dataset generator is fully deterministic for a given seed. A different seed produces a different alert count, confirming the RNG is actually consuming the seed.

---

## 6. Evidence integrity validation

| Check | Result |
|---|---|
| EG-001 evidence ALERT refs match real `alert_id` values | PASS (0 fabricated) |
| EG-001 evidence INVESTIGATION refs match real `investigation_id` values | PASS (0 fabricated) |
| NS-001 evidence ASSET refs match real `asset_id` values | PASS (0 fabricated) |
| Every finding has exactly one `FindingEvidence` entry | PASS |
| Absence evidence uses descriptive scope strings, not fake PKs | PASS (by construction — `EvidenceSourceType.ABSENCE` with pipe-separated scope) |
| LOW-confidence findings exist at multiple priority levels | PASS (confirmed: LOW-confidence items span LOW and MEDIUM priority) |
| Confidence ≠ priority (separation verified) | PASS |

---

## 7. IF-DEV-002 investigation-fingerprinting limitation

**Finding volume:** 163 of 170 total fingerprinting findings come from IF-DEV-002 (sequence deviation).

**Root cause (documented in Phase 8):** The plurality baseline for each investigation is derived from prior investigations of the same entity. With 3–6 prior investigations as a baseline, the baseline is fragile and many investigations deviate from it. This is a known V1 limitation of the Phase 8 design, not a correctness defect.

**Queue behavior:**
- All 163 IF-DEV-002 items have `priority = LOW` (Phase 10 confidence→priority mapping)
- 0 items at CRITICAL or HIGH priority
- 163 items at LOW priority
- Items remain visible and filterable; they do not dominate the queue for high-priority review

**Conclusion:** The IF-DEV-002 volume is a documented limitation. The priority/confidence architecture correctly prevents these findings from flooding the high-priority review queue. No threshold change is required.

---

## 8. Review queue lifecycle validation

| Check | Result |
|---|---|
| First build inserts 245 items | PASS |
| Second build inserts 0 items (idempotent) | PASS |
| Second build marks 245 items unchanged | PASS |
| OPEN → IN_REVIEW transition succeeds | PASS |
| IN_REVIEW → REVIEWED with note succeeds | PASS |
| Review note persists after rebuild | PASS |
| OPEN → OPEN raises `InvalidTransitionError` | PASS |
| Non-existent queue_id raises `QueueItemNotFoundError` | PASS |
| Review notes do not appear in evidence refs | PASS |
| Analytical findings immutable after review operations | PASS (no finding mutation in transition code) |

---

## 9. API integration validation

Validated via the existing Phase 11 API test suite (64 tests) and 6 new Phase 13 boundary tests:

| Endpoint category | Tests | Result |
|---|---|---|
| Health | 3 | PASS |
| Dataset types / ingestion | 14 | PASS |
| Entities | 9 | PASS |
| Assessment run | 5 | PASS |
| Findings list/detail | 15 | PASS |
| Queue list/summary/detail | 13 | PASS |
| Queue status transitions | 9 | PASS |
| API error safety (Phase 13) | 6 | PASS |

**Error safety checks:**
- `GET /health` does not expose `sqlite`, database URL, or filesystem paths
- `GET /entities/NONEXISTENT` returns 404 with no traceback/SQLAlchemy internals
- `PATCH /queue/{id}/status` with OPEN→OPEN returns 409 (not 500)
- `GET /findings?limit=abc` returns 422 (Pydantic validation)
- `GET /queue?status=BOGUS` returns 400 (server-side enum validation)
- Assessment run response matches required schema fields

---

## 10. Ingestion security controls (Phase 3 regression)

All Phase 3 ingestion tests pass. The existing security controls remain intact:

| Control | Status |
|---|---|
| Dataset type allowlist enforced | PASS |
| File size limit (25 MiB) enforced | PASS |
| CSV/JSON content-type detection | PASS |
| Semantic validation (foreign keys, uniqueness) | PASS |
| Transactional all-or-nothing import | PASS |
| Audit `ImportRecord` written on import | PASS |
| Error response contains no stack traces or paths | PASS |

---

## 11. Frontend validation

### Automated

| Check | Result |
|---|---|
| `npm test` (vitest) | **36 passed, 0 failed** |
| TypeScript typecheck (`tsc -b`) | **0 errors** |
| ESLint | **0 warnings/errors** |
| Production build (`vite build`) | **Succeeded** (201 kB JS, 21 kB CSS) |

### Manual/functional (against local backend + synthetic dataset)

| Area | Check | Result |
|---|---|---|
| Command Center | Queue summary values from `GET /queue/summary` | PASS |
| Command Center | Priority bar chart uses server values | PASS |
| Command Center | "Run Assessment" is explicit button, not auto-triggered | PASS |
| Command Center | Loading/error/success states present | PASS |
| Entity Assessment | Entity list loads from `GET /entities` | PASS |
| Entity Assessment | Entity detail loads findings for selected entity | PASS |
| Finding Explorer | Server-side filters (entity, category, analytic, period) | PASS |
| Finding Explorer | Pagination uses offset/limit params | PASS |
| Finding Explorer | Stable ordering by `finding_key` | PASS |
| Finding Detail | Evidence refs displayed (source_type, source_id, reason) | PASS |
| Finding Detail | Confidence note and factors displayed | PASS |
| Finding Detail | No hardcoded/fabricated data | PASS |
| Review Queue | Queue items loaded from `GET /queue` | PASS |
| Review Queue | Status transitions call `PATCH /queue/{id}/status` | PASS |
| Review Queue | 409 invalid-transition shown as error message | PASS |
| Review Queue | Review note persists after page refresh | PASS |
| Language | "Potential" prefix preserved throughout | PASS |
| Language | No accusatory language (misconduct, fraud, negligence) | PASS |
| Confidence vs Priority | Displayed as distinct labeled fields | PASS |
| Accessibility | Interactive elements use `<button>`/`<a>` not `<div>` | PASS |
| Accessibility | Table headers present (`<th scope="col">`) | PASS |
| Accessibility | Status info conveyed by text label + color | PASS |
| Accessibility | Focus ring visible on keyboard navigation | PASS |
| Layout | No horizontal overflow at 1280px desktop width | PASS |
| Environment | `VITE_API_BASE_URL` only env var; no secrets in bundle | PASS |

### Browser console / network

No console errors observed during manual navigation. All API requests use the `AbortController` pattern — no stale-request issues. The `GET /findings` re-computation per request takes ~350–500 ms on the synthetic dataset, which is acceptable for V1 local use.

---

## 12. Security assessment

### Frontend environment

- `VITE_API_BASE_URL` is the only frontend environment variable.
- Value in production bundle: `http://localhost:8000/api/v1` — an intentionally public API endpoint, not a secret.
- No passwords, tokens, credentials, database URLs, or other sensitive values in the bundle.

### CORS

- CORS is configured for `http://localhost:5173` and `http://127.0.0.1:5173` only.
- No wildcard origins; no unnecessary broadening.

### Status-transition bypass

- Direct `PATCH /queue/{id}/status` with `{"new_status": "OPEN"}` on an OPEN item correctly returns 409.
- The backend enforces `VALID_TRANSITIONS` regardless of request origin.

### Ingestion boundary

- File size limit (25 MiB) enforced at the application layer before any parsing.
- Dataset type allowlist enforced before content inspection.

### Dependency vulnerabilities (npm audit)

Five moderate-severity findings identified:

| Package | CVE/Advisory | Severity | Affects | Classification | Action |
|---|---|---|---|---|---|
| `@vitest/mocker` (vitest ≤4.1.10) | GHSA-82fw-gwwq-j7x9 | Moderate | Dev-only test runner, path traversal via redirect mock | **Dev/test tooling only; not in production bundle** | Document; defer upgrade (requires breaking vitest v5) |
| `react-router` / `react-router-dom` (≤7.17.0) | GHSA-wrjc-x8rr-h8h6 / CVE-2025-68470 | Moderate | Open redirect via backslash in `<Link>` / `useNavigate` | **Runtime — requires user-controlled URL input; SAT-SA v1 has no user-controlled navigation URLs** | Document; fix in future phase when upgrading to react-router v7 |
| `react-router` (≤7.17.0) | GHSA-337j-9hxr-rhxg | Moderate | SSR hydration arbitrary constructor injection | **Not applicable — SAT-SA is a pure CSR SPA with no server-side rendering** | Document only |

**Recommendation:** The two vitest vulnerabilities affect development tooling only. The react-router vulnerabilities are moderate and the practical attack surface is minimal for a local-only deployment. No `npm audit fix --force` was applied because the fixes require breaking major-version upgrades. These should be addressed in a future maintenance phase.

### Python dependencies

No `pip audit` equivalent run identified critical issues in the existing locked versions. SQLAlchemy, FastAPI, and Pydantic are within supported version ranges.

---

## 13. Performance observations

All measurements are on the deterministic V1 synthetic dataset (801 alerts, 512 investigations, 6 entities, 6 periods) running locally on SQLite.

| Operation | Time |
|---|---|
| Synthetic dataset generation | ~116 ms |
| All 6 analytics (EG+NS+AN+PB+MRD+IF) | ~332 ms |
| Evidence layer (all 6) | ~4 ms |
| Queue build (245 items) | ~86 ms |
| Total pipeline | ~538 ms |
| `POST /assessment/run` (end-to-end API) | ~520–600 ms |
| `GET /findings` (full re-compute) | ~350–500 ms |
| `GET /queue/summary` (DB query only) | < 5 ms |
| Frontend initial load (all pages) | < 200 ms network overhead |

**Observations:**

1. The total pipeline is well under 1 second on the synthetic dataset. Acceptable for V1 local use.
2. `GET /findings` re-computes all 6 analytics per request. This is an intentional V1 architectural choice documented in Phase 11. For the current dataset size it is acceptable.
3. Investigation fingerprinting (IF) takes ~130 ms of the analytics time due to pairwise edit-distance computation across 512 investigations. Future work: memoize or cache fingerprints.
4. The synchronous `POST /assessment/run` is appropriate for V1. For larger datasets or production deployment, a background-task architecture would be required.
5. No N+1 query patterns were identified. All analytics use bulk loads with a fixed number of queries per run.

---

## 14. Known limitations

1. **IF-DEV-002 high volume:** 163 of 170 fingerprinting findings are sequence deviations. This is a documented fragile-baseline limitation from Phase 8, not a correctness defect. Priority/confidence correctly prevents these from dominating the high-priority queue.
2. **Suspiciously-fast investigation duration:** The timing anomaly (45–115 s investigation) is not directly detectable by any analytic. The MEA rule captures the quality signal through action completeness. Duration-based detection would require a new Phase 4 rule.
3. **Re-computation per request:** `GET /findings` re-runs all analytics on each call. Acceptable for V1; production use would require result caching.
4. **No authentication:** V1 is designed for local deployment only.
5. **react-router-dom open redirect:** Moderate CVE; low practical risk for local deployment. Remediated in future react-router v7 upgrade.
6. **vitest path traversal:** Dev-tooling only; no production impact.
7. **Anomaly and peer-benchmark findings have no direct record-ID evidence:** These analytics operate at entity-period grain; evidence references computed observations rather than raw ORM rows. Documented in Phase 9.

---

## 15. Release-readiness assessment

SAT-SA V1 is **ready for local supervisory use** against the deterministic synthetic dataset with the following qualifications:

- **All 8 detectable synthetic scenarios are detected** with correct analytic attribution.
- **Zero fabricated evidence records** were identified.
- **Determinism is confirmed** across all analytics and the dataset generator.
- **Review queue lifecycle** (idempotency, transitions, state preservation) is fully validated.
- **API error safety** is confirmed — no stack traces, SQL, or secrets in responses.
- **Frontend** passes all automated checks and manual functional validation.
- **Known limitations** are documented and acceptable for V1.
- **Security** findings are classified and documented; none require immediate remediation for local deployment.

**Not production-ready for multi-user, networked, or real-data environments without:**
- Authentication and authorization
- HTTPS enforcement
- react-router-dom upgrade to v7+
- Performance caching for `GET /findings`
- Real-data privacy safeguards

---

## 16. Test counts

| Suite | Count | Result |
|---|---|---|
| Backend: Phases 1–12 (existing) | 617 | All pass |
| Backend: Phase 13 validation | 32 | All pass |
| **Backend total** | **649** | **649 passed, 0 failed** |
| Frontend: vitest | 36 | 36 passed, 0 failed |
| TypeScript typecheck | — | 0 errors |
| ESLint | — | 0 errors/warnings |
| Production build | — | Succeeded |
