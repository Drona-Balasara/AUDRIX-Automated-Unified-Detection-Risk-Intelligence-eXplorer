# Development

Local development workflow and engineering conventions for SAT-SA.

## Prerequisites

- Python 3.12
- Node.js 20.19+ / 22.12+ and npm
- Git

## First-time setup

### Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); .venv/bin/activate elsewhere
pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
```

## Running both services

Open two terminals.

```bash
# Terminal 1 — backend
cd backend && uvicorn app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && npm run dev
```

Then open `http://localhost:5173`. The system-status panel should report
"Connected" with the backend's reported service, version, environment, and
database state.

If it reports "Unavailable", the backend is not running or `VITE_API_BASE_URL`
does not match the backend address. If it reports "Error", the backend was
reached but returned something unexpected — check the backend logs.

## Checks before considering work done

```bash
# Backend
cd backend && pytest

# Frontend
cd frontend
npm run type-check
npm run lint
npm run build
```

## Synthetic dataset generation

The Phase 2 generator produces a deterministic, reproducible SOC dataset. Run it
from an activated backend environment:

```bash
cd backend

# Generate with the documented default seed (20240601) into ../data/synthetic
python -m app.datagen

# Explicit seed and/or output directory
python -m app.datagen --seed 20240601
python -m app.datagen --seed 7 --output /tmp/satsa-data

# Fewer/more monthly reporting periods (default is 6)
python -m app.datagen --periods 3
```

Each run is fully determined by the configuration plus the seed, so the same
seed always reproduces an identical dataset. Files are written per table as
typed CSV and JSON (`entities`, `assets`, `alerts`, `investigations`,
`investigation_actions`, `escalations`, `remediations`, `telemetry`,
`performance_metrics`), plus a `manifest.json` of row counts and a separate
`ground_truth` file.

`ground_truth.{csv,json}` is **evaluation data**: it records the planted
scenarios (type, affected IDs, period, expected detection category, and a
machine-readable reason). It must never be exposed through production APIs or
dashboards in later phases.

The generated CSV/JSON under `data/synthetic/` are tracked (they are safe to
publish: fully synthetic, no real people, organizations, credentials, or
addresses). Only generated SQLite `*.db` files under `data/` are git-ignored.

The data-quality invariants, scenario conditions, reproducibility, and ORM
round-trip are all covered by the backend test suite (`pytest`); see the
`tests/test_datagen_*.py` modules. The schema and scenario catalog are
documented in [data-model.md](data-model.md).

## Data ingestion

Phase 3 adds a validation/normalization/import pipeline under
`/api/v1/ingestion`. The dataset type is always named explicitly by the client;
the server never guesses it from file contents. See
[ingestion.md](ingestion.md) for the full reference (formats, canonical
schemas, normalization rules, transaction semantics, and the error format).

Quick local usage against a running backend:

```bash
# Report-only: validate without touching the database.
curl -s -F "file=@../data/synthetic/entities.csv" \
     -F "dataset_type=entities" \
     http://localhost:8000/api/v1/ingestion/validate | python -m json.tool

# Transactional import (all-or-nothing). Load parents before children.
curl -s -F "file=@../data/synthetic/entities.json" \
     -F "dataset_type=entities" -F "mode=replace" \
     http://localhost:8000/api/v1/ingestion/import | python -m json.tool

# List supported dataset types and their columns.
curl -s http://localhost:8000/api/v1/ingestion/dataset-types | python -m json.tool
```

The ingestion test suites run as part of `pytest`:

```bash
cd backend
pytest tests/test_ingestion_validation.py   # format-neutral engine rules
pytest tests/test_ingestion_api.py          # HTTP boundaries + safe errors
pytest tests/test_ingestion_integration.py  # full nine-table synthetic import
```

## Execution-gap detection

Phase 4 adds the first analytic: a deterministic, read-only execution-gap
detector under `app/analytics/execution_gap`. It has no HTTP surface — it is a
service-layer entry point, `run_execution_gap_detection(session)`, that returns
structured, strictly observational findings. See
[execution-gap-detection.md](execution-gap-detection.md) for the rule registry,
configuration, finding schema, and false-positive considerations.

Its test suites run as part of `pytest`:

```bash
cd backend
pytest tests/test_execution_gap.py              # per-rule unit tests
pytest tests/test_execution_gap_integration.py  # synthetic-data detection + oracle
```

## Negative-space detection

Phase 5 adds the second analytic: a deterministic, read-only negative-space
detector under `app/analytics/negative_space`. Like Phase 4 it has no HTTP
surface — it is a service-layer entry point, `run_negative_space_detection(session)`,
that returns structured, strictly observational findings. It reports *absences*
of expected monitoring evidence (critical asset with no telemetry; telemetry
that disappeared mid-window), emitting a finding only once the data establishes
the evidence was expected. See
[negative-space-detection.md](negative-space-detection.md) for the rule
registry, configuration, finding schema, baseline/dataset-boundary handling, and
false-positive considerations.

Its test suites run as part of `pytest`:

```bash
cd backend
pytest tests/test_negative_space.py              # per-rule unit tests
pytest tests/test_negative_space_integration.py  # synthetic-data detection + oracle
```

## Anomaly detection and peer benchmarking

Phase 6 adds two more deterministic, read-only analytics under
`app/analytics/anomaly` and `app/analytics/peer_benchmark`. Like Phases 4–5 they
have no HTTP surface — they are service-layer entry points,
`run_anomaly_detection(session)` and `run_peer_benchmark(session)`, that return
structured, strictly observational results.

- **Anomaly detection** fits a scikit-learn `IsolationForest` (fixed
  `random_state`) over entity-reporting-period feature observations and flags
  observations that are unusual relative to the modelled population. It exposes a
  raw/normalised anomaly signal and a decision — never a risk score — and returns
  an explicit insufficient-sample result when too few observations exist. See
  [anomaly-detection.md](anomaly-detection.md).
- **Peer benchmarking** compares each entity-period's reported KPIs against the
  robust (median/scaled-MAD) baseline of its genuinely comparable, same-period
  peers, with the subject's own value excluded and an explicit insufficient-peers
  status when too few peers exist. See [peer-benchmarking.md](peer-benchmarking.md).

Phase 6 adds exactly one dependency, `scikit-learn` (for `IsolationForest`); peer
benchmarking uses only the standard library. Their test suites run as part of
`pytest`:

```bash
cd backend
pytest tests/test_anomaly.py                     # feature/guard/determinism unit tests
pytest tests/test_peer_benchmark.py              # robust-stat / peer-rule unit tests
pytest tests/test_anomaly_peer_integration.py    # synthetic-data detection + oracle
```

## Metric-risk divergence

Phase 7 adds a third-generation deterministic, read-only analytic under
`app/analytics/metric_risk_divergence`. The service entry point is
`run_metric_risk_divergence(session)`. It identifies reporting periods where
headline SOC performance metrics show an improving trend while underlying
operational-quality indicators materially deteriorate. The finding type is
"Potential Metric-Risk Divergence" — the analytic describes a pattern in the
data and does not assert intent, cause, or fault on any individual or team.
See [metric-risk-divergence.md](metric-risk-divergence.md) for the metric
registry, direction definitions, trend method, thresholds, missing-data
handling, limitations, and the synthetic-dataset evaluation.

No new dependencies are introduced; the analytic uses only the standard library
and existing project code. No schema migration is required. Its test suites run
as part of `pytest`:

```bash
cd backend
pytest tests/test_metric_risk_divergence.py              # unit tests (44)
pytest tests/test_metric_risk_divergence_integration.py  # synthetic-data detection + oracle (16)
```

## Investigation fingerprinting

Phase 8 adds a fourth-generation deterministic, read-only analytic under
`app/analytics/investigation_fingerprinting`. The service entry point is
`run_investigation_fingerprinting(session)`. It represents each investigation
as an ordered action-type fingerprint and runs three detectors: IF-REP-001
(Potential Template-Driven Investigation Pattern), IF-DEV-002 (Potential
Investigation Sequence Deviation), and IF-MEA-003 (Potential Missing
Investigation Action). Sequence similarity uses normalized Levenshtein edit
distance, implemented without any external dependency. No schema migration is
required. See [investigation-fingerprinting.md](investigation-fingerprinting.md)
for the fingerprint definition, detector logic, thresholds, calibration,
missing-data handling, limitations, and synthetic-dataset evaluation.

Its test suites run as part of `pytest`:

```bash
cd backend
pytest tests/test_investigation_fingerprinting.py              # unit tests (73)
pytest tests/test_investigation_fingerprinting_integration.py  # synthetic-data detection + oracle (16)
```

## Evidence and confidence

Phase 9 adds the shared evidence and confidence layer under
`app/analytics/evidence/`. The service entry points are
`annotate_execution_gap(result)`, `annotate_negative_space(result)`,
`annotate_anomaly(result)`, `annotate_peer_benchmark(result)`,
`annotate_metric_risk_divergence(result)`, and
`annotate_fingerprint(result)`. Each returns an `AnnotatedResult` wrapper
carrying the original frozen result plus an `evidence_map` keyed by
`finding_key`. Every finding from Phases 4–8 gets a `FindingEvidence` entry
with structured `EvidenceRef` objects, a categorical `EvidenceConfidence`
(HIGH/MODERATE/LOW), and the `ConfidenceFactor` list that explains why
confidence is limited. Evidence is built from already-loaded context with no
additional database queries. Absent evidence is represented as an `ABSENCE`
source type, never fabricated. See
[evidence-and-confidence.md](evidence-and-confidence.md) for the full schema,
confidence aggregation rules, per-analytic builder logic, and limitations.

Its test suites run as part of `pytest`:

```bash
cd backend
pytest tests/test_evidence.py              # unit tests (77)
pytest tests/test_evidence_integration.py  # cross-phase integration tests (28)
```

## Supervisory Review Queue

Phase 10 adds the supervisory review queue under
`app/analytics/review_queue/`. The service entry point is
`build_review_queue(session, bundle)`. It accepts a `FindingsBundle` of all
Phase 4–8 annotated results (from Phase 9) and upserts one `ReviewQueueItem`
per finding into the `review_queue_item` database table. Priority is separate
from confidence: `QueuePriority` (CRITICAL / HIGH / MEDIUM / LOW) is derived
from finding type, alert severity, evidence confidence, and evidence count
using a transparent, documented, rule-based algorithm — no ML. The queue is
idempotent: re-running over unchanged findings preserves existing review
state. Changed findings reset their item to OPEN. Status lifecycle: OPEN →
IN_REVIEW → REVIEWED / DISMISSED (with reopen). The `ReviewQueueItem` ORM
model is registered on `Base.metadata` via `init_db()` — no separate
migration tool is required. See
[supervisory-review-queue.md](supervisory-review-queue.md) for the full
design, priority logic, idempotency rules, status lifecycle, limitations, and
how future API/UI phases consume the queue.

Its test suites run as part of `pytest`:

```bash
cd backend
pytest tests/test_review_queue.py              # unit tests (79)
pytest tests/test_review_queue_integration.py  # integration tests (38)
```

## API layer (Phase 11)

Phase 11 exposes the existing analytical capabilities through a clean,
thin FastAPI HTTP layer.  Analytical findings remain **read-only** through
the API; only review-queue state transitions may be written.  The API
layer performs request validation, dependency wiring, response serialization,
filtering, pagination, and error translation — all analytics logic stays in
the existing packages.

Start the backend server:

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

The interactive API docs are available at `http://localhost:8000/docs`.

New endpoints (all under `/api/v1`):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/entities` | List all SOC entities (paginated) |
| `GET` | `/entities/{entity_id}` | Get one entity |
| `POST` | `/assessment/run` | Run all analytics, build evidence, refresh queue |
| `GET` | `/findings` | List findings (paginated, filterable) |
| `GET` | `/findings/{finding_key}` | Get one finding with evidence |
| `GET` | `/queue` | List review queue items (paginated, filterable) |
| `GET` | `/queue/summary` | Queue aggregate counts |
| `GET` | `/queue/{queue_id}` | Get one queue item |
| `PATCH` | `/queue/{queue_id}/status` | Transition review status |

See [api.md](api.md) for the full reference including request/response schemas,
filter parameters, pagination, error codes, and limitations.

Its test suite runs as part of `pytest`:

```bash
cd backend
pytest tests/test_api_phase11.py   # 64 API tests
```

## Environment configuration

- Backend variables use the `SATSA_` prefix and are read from the process
  environment or `backend/.env`. Defaults in `app/core/config.py` make the app
  run without any `.env` file.
- Frontend variables use Vite's `VITE_` prefix and are read from
  `frontend/.env.local`.
- `.env.example` (repository root) documents all variables. Copy the relevant
  lines into `backend/.env` and `frontend/.env.local`.

Only `.env.example` files are tracked by Git. Never commit real `.env` files.

## Conventions

### Backend

- Keep route handlers thin; put database and domain logic in `app/services`.
- Use SQLAlchemy 2.x style (`select(...)`, `Mapped`/`mapped_column`), not the
  legacy `Query` API.
- Use type hints everywhere. Prefer explicit Pydantic models over free-form
  dictionaries for request/response payloads.
- Use the logger from `app.core.logging` (`get_logger(__name__)`). Do not use
  `print()`. Never log secrets, tokens, headers, or request bodies.
- API responses must not leak stack traces, file paths, the database URL, or
  other internals.

### Frontend

- All backend requests go through `src/services`. Do not hardcode backend URLs
  in components.
- Represent asynchronous state honestly (loading / connected / unavailable /
  error). Do not display data the backend has not returned.
- Use the design tokens in `src/styles/tokens.css`; avoid ad-hoc colors and
  spacing. Keep interactive elements keyboard-accessible with visible focus.

### General

- Prefer small, focused modules and functions. Avoid speculative abstractions,
  dead code, and placeholder files.
- Comments explain non-obvious decisions, not obvious code.

## Database notes

- The development database is a SQLite file created on startup under `data/`.
- To reset local state, stop the backend and delete the generated `*.db` file;
  it is recreated on the next startup.
- Generated database files are git-ignored.

## Git workflow

The project uses a checkpoint-based workflow: each phase is implemented, tested,
reviewed, and declared complete before its work is committed and pushed. Do not
push incomplete phase work.
