# SAT-SA API Reference

Phase 11 exposes the SAT-SA analytical capabilities through a versioned
FastAPI HTTP layer.  All endpoints are under the `/api/v1` prefix.

Analytical findings are **read-only** through the API — they are derived on
demand from the ingested domain records and never stored.  Only supervisory
review queue state may be written (status transitions and review notes).

No authentication is implemented in V1; the API is designed for local
deployment only.

---

## Common conventions

### Pagination

List endpoints accept `limit` (default 50, max varies) and `offset` (default 0)
query parameters.  Responses include `total`, `limit`, `offset`, and `items`.

```
GET /api/v1/findings?limit=20&offset=40
```

### Stable ordering

List results are deterministically ordered so pagination is predictable:
- Findings: by `finding_key` (alphabetic).
- Queue items: by priority rank (CRITICAL first), then `created_at` descending.
- Entities: by `entity_id`.

### Error responses

All errors return a JSON body with a `detail` field.

| HTTP status | Meaning |
|---|---|
| 400 | Invalid filter/enum value |
| 404 | Resource not found |
| 409 | Invalid status transition |
| 422 | Request validation error (Pydantic) |
| 500 | Unexpected internal error (safe generic message; no stack trace) |

### Security

- No stack traces, SQL queries, filesystem paths, or secrets are ever
  returned to clients.
- The global exception handler in `app/main.py` catches all unhandled errors
  and returns `{"detail": "An internal error occurred."}`.
- CORS is pre-configured for `http://localhost:5173` (Vite dev server).

---

## Endpoints

### System

#### `GET /api/v1/health`

Returns service identity, version, environment, and database connectivity.
Preserved from Phase 1 — no changes.

**Response:** `200 OK`
```json
{
  "service": "sat-sa-api",
  "status": "ok",
  "version": "0.1.0",
  "environment": "development",
  "database": "ok"
}
```

---

### Ingestion

Preserved from Phase 3.  See `docs/ingestion.md` for full reference.

- `GET /api/v1/ingestion/dataset-types`
- `POST /api/v1/ingestion/validate`
- `POST /api/v1/ingestion/import`
- `GET /api/v1/ingestion/imports/{import_id}`

---

### Entities

#### `GET /api/v1/entities`

List all SOC entities ordered by `entity_id`.

**Query parameters:**
- `limit` (int, 1–200, default 50)
- `offset` (int ≥ 0, default 0)

**Response:** `200 OK`
```json
{
  "total": 6,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "entity_id": "ENT-01",
      "name": "SOC Entity 01",
      "sector": "FINANCE",
      "peer_group": "FINANCE",
      "scale": "LARGE",
      "asset_count_estimate": 54,
      "analyst_headcount": 24,
      "data_period_start": "2024-01-01T00:00:00Z",
      "data_period_end": "2024-07-01T00:00:00Z"
    }
  ]
}
```

#### `GET /api/v1/entities/{entity_id}`

Get one entity. Returns `404` if not found.

---

### Assessment

#### `POST /api/v1/assessment/run`

Runs all Phase 4–8 analytics against the current ingested records, builds
Phase 9 evidence/confidence, and refreshes the Phase 10 supervisory review
queue (idempotent upsert).

Analytical findings are **not** persisted — they are recomputed on each run.
Only the review queue is written.

**Request:** No body required.

**Response:** `200 OK`
```json
{
  "entity_count": 6,
  "total_findings": 245,
  "analytics": [
    {"analytic_id": "EG-001", "finding_count": 37, "status": "ok"},
    {"analytic_id": "NS-001", "finding_count": 5,  "status": "ok"},
    {"analytic_id": "AN-001", "finding_count": 4,  "status": "OK"},
    {"analytic_id": "PB-001", "finding_count": 28, "status": "ok"},
    {"analytic_id": "MRD-001","finding_count": 1,  "status": "ok"},
    {"analytic_id": "IF",     "finding_count": 170,"status": "ok"}
  ],
  "queue_items_total": 245,
  "queue_inserted": 0,
  "queue_unchanged": 245,
  "duration_ms": 520.3
}
```

---

### Findings

#### `GET /api/v1/findings`

List findings from all Phase 4–8 analytics, ordered by `finding_key`.

**Query parameters:**
- `entity_id` (string, optional) — filter by entity
- `analytic_id` (string, optional) — filter by analytic (e.g. `EG-001`, `AN-001`)
- `category` (string, optional) — filter by broad category
  (`EXECUTION_GAP`, `MONITORING_GAP`, `ANOMALY`, `PEER_DEVIATION`,
  `METRIC_RISK_DIVERGENCE`, `WORKFLOW_PATTERN`)
- `period_label` (string `YYYY-MM`, optional) — filter by reporting period
- `include_evidence` (bool, default `false`) — include evidence refs and confidence
- `limit` (int, 1–500, default 50)
- `offset` (int ≥ 0, default 0)

**Response:** `200 OK`
```json
{
  "total": 245,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "finding_key": "AN-001:ENT-02:2024-03",
      "analytic_id": "AN-001",
      "finding_type": "ANOMALOUS",
      "category": "ANOMALY",
      "entity_id": "ENT-02",
      "period_label": "2024-03",
      "summary": "Entity ENT-02 in reporting period 2024-03 is unusual...",
      "confidence": "MODERATE",
      "evidence_count": 3,
      "details": {"decision": "ANOMALOUS", "normalized_anomaly_score": 0.72}
    }
  ]
}
```

When `include_evidence=true`, each item additionally carries `confidence`,
`evidence_count` populated from the Phase 9 evidence layer.

#### `GET /api/v1/findings/{finding_key}`

Get one finding with full evidence and confidence.  The `finding_key` may
contain colons and is matched after URL-decoding.  Always includes evidence.

**Response:** `200 OK`
```json
{
  "finding": { "...all finding fields..." },
  "evidence": {
    "finding_key": "...",
    "analytic_id": "EG-001",
    "confidence": "MODERATE",
    "confidence_note": "Adequate evidence...",
    "confidence_factors": ["ABSENCE_BASED", "SINGLE_RECORD"],
    "evidence_count": 3,
    "evidence_refs": [
      {
        "source_type": "ALERT",
        "source_id": "ALR-000001",
        "role": "triggering_alert",
        "period_label": "2024-01",
        "reason": "Alert ALR-000001 (severity CRITICAL) is the direct trigger..."
      }
    ]
  }
}
```

Returns `404` when the finding key does not exist.

---

### Review Queue

#### `GET /api/v1/queue`

List review queue items ordered by priority (CRITICAL first) then
`created_at` descending.

**Query parameters:**
- `status` (string, optional) — `OPEN`, `IN_REVIEW`, `REVIEWED`, `DISMISSED`
- `priority` (string, optional) — `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`
- `entity_id` (string, optional)
- `category` (string, optional)
- `limit` (int, 1–200, default 50)
- `offset` (int ≥ 0, default 0)

**Response:** `200 OK`
```json
{
  "total": 245,
  "limit": 50,
  "offset": 0,
  "items": [
    {
      "queue_id": "3a7f9c12b4e8",
      "finding_key": "EG-001:ENT-01:ALR-000001",
      "analytic_id": "EG-001",
      "finding_type": "CRITICAL_ALERT_NO_ESCALATION",
      "entity_id": "ENT-01",
      "period_label": "2024-02",
      "title": "Potential Escalation Gap — ENT-01",
      "category": "EXECUTION_GAP",
      "priority": "CRITICAL",
      "confidence": "MODERATE",
      "evidence_count": 3,
      "status": "OPEN",
      "created_at": "2024-10-03T17:00:00Z",
      "updated_at": "2024-10-03T17:00:00Z",
      "reviewed_at": null,
      "reviewer_ref": null,
      "review_note": null
    }
  ]
}
```

#### `GET /api/v1/queue/summary`

Return aggregate counts by status, priority, and category.

**Response:** `200 OK`
```json
{
  "total": 245,
  "by_status":   {"OPEN": 245, "IN_REVIEW": 0, "REVIEWED": 0, "DISMISSED": 0},
  "by_priority": {"CRITICAL": 34, "HIGH": 11, "MEDIUM": 8, "LOW": 192},
  "by_category": {"EXECUTION_GAP": 37, "MONITORING_GAP": 5, ...}
}
```

#### `GET /api/v1/queue/{queue_id}`

Get one queue item. Returns `404` when not found.

#### `PATCH /api/v1/queue/{queue_id}/status`

Transition a queue item to a new review status.  Validates the transition
against the defined lifecycle.  Analytical findings and evidence are never
mutated.

**Request body:**
```json
{
  "new_status": "IN_REVIEW",
  "reviewer_ref": "supervisor-team-a",
  "review_note": "Reviewing with additional context."
}
```

`new_status` is required; `reviewer_ref` and `review_note` are optional.

**Status transition rules:**

| From | Allowed targets |
|---|---|
| `OPEN` | `IN_REVIEW`, `REVIEWED`, `DISMISSED` |
| `IN_REVIEW` | `REVIEWED`, `OPEN` |
| `REVIEWED` | `OPEN` |
| `DISMISSED` | `OPEN` |

**Responses:**
- `200 OK` — transition successful; returns updated queue item
- `400 Bad Request` — invalid `new_status` value
- `404 Not Found` — queue_id does not exist
- `409 Conflict` — transition not permitted from current status

---

## Limitations

- **No authentication.** The API is for local V1 use only.
- **Assessment is synchronous.** Running `/assessment/run` on a large real
  dataset could take several seconds.  Future phases may add async execution.
- **Findings are not cached.** `/findings` re-runs all analytics on each
  request.  For the local synthetic dataset this takes ~100–600 ms.
- **No bulk status transitions.** Queue items must be updated one at a time.
- **No WebSocket / push.** The React dashboard must poll for updates.
- **CORS is pre-configured** for `localhost:5173`.  Adjust `SATSA_CORS_ORIGINS`
  for other frontends.
