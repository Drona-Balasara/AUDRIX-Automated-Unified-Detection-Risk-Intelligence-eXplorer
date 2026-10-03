# SAT-SA — Security Assessment & Supervisory Analytics

SAT-SA is an evidence-driven security operations supervisory analytics platform.
Its purpose is to assess SOC operational evidence, identify execution gaps and
missing expected evidence, detect unusual operational patterns, compare
comparable entities, and surface evidence-backed findings that require human
supervisory review.

This repository contains the full application: a Python/FastAPI backend and a
React/TypeScript frontend, organized as a single monorepo.

## Current status

**Phase 6 — Anomaly Detection & Peer Benchmarking.**

Phase 1 established the architecture, tooling, and a working end-to-end baseline.
Phase 2 built the SAT-SA domain data model and a realistic, reproducible
synthetic SOC dataset. Phase 3 added the trusted ingestion boundary that accepts
externally produced datasets, validates and normalizes them against canonical
per-type schemas, and persists them transactionally. Phase 4 added the first
analytic: a deterministic, read-only execution-gap detector that reports
observable deviations in how recorded work was handled. Phase 5 added the second
analytic: a deterministic, read-only negative-space detector that reports
observable *absences of expected monitoring evidence* — but only where existing
data establishes that the evidence was reasonably expected. Phase 6 adds two
more deterministic, read-only analytics: an unsupervised **anomaly detector**
(scikit-learn IsolationForest, fixed seed) that flags entity-reporting-period
observations unusual relative to the modelled population, and a **peer
benchmark** that compares an entity-period's reported KPIs against the robust
baseline of its genuinely comparable, same-period peers. Both expose
observational signals only — never a SAT-SA risk score or performance verdict.
The remaining analytical capabilities described above are still **not
implemented**; they are introduced in later phases (see [Roadmap](#roadmap)).
What exists today:

- A FastAPI application with a versioned API and a `GET /api/v1/health` endpoint.
- Typed configuration, centralized logging, and a SQLAlchemy 2.x database layer
  with SQLite foreign-key enforcement enabled on every connection.
- The full domain model as SQLAlchemy 2.x models: SOC entities and assets;
  alerts, investigations and ordered investigation actions; escalations and
  remediations; telemetry records; and periodic performance metrics — plus the
  Phase 1 system-metadata table. Database initialization remains additive and
  idempotent (no destructive resets).
- A deterministic, seed-driven synthetic dataset generator
  (`python -m app.datagen`) that emits a coherent multi-entity dataset to
  `data/synthetic/` as typed CSV and JSON, together with a separate ground-truth
  file of planted scenarios used only for evaluation.
- An ingestion pipeline under `/api/v1/ingestion` (validate / import /
  dataset-types / audit-record) that accepts CSV and JSON only, requires an
  explicit allowlisted dataset type, applies one shared validation engine to
  both formats, normalizes to canonical typed values (timezone-aware UTC
  timestamps, authoritative enums), and imports all-or-nothing with a safe audit
  record. See [docs/ingestion.md](docs/ingestion.md).
- An execution-gap detector under `app/analytics/execution_gap` (service layer,
  no HTTP surface) that evaluates a small registry of deterministic, read-only
  rules against the normalized domain records and returns structured, strictly
  observational findings for later evidence/confidence phases. See
  [docs/execution-gap-detection.md](docs/execution-gap-detection.md).
- A negative-space detector under `app/analytics/negative_space` (service layer,
  no HTTP surface) that reports observable absences of expected monitoring
  evidence — a critical monitored asset with no telemetry, or a monitored asset
  whose telemetry disappeared mid-window — emitting a finding only once an
  expectation of that evidence has been established from the data. See
  [docs/negative-space-detection.md](docs/negative-space-detection.md).
- An anomaly detector under `app/analytics/anomaly` (service layer, no HTTP
  surface) that fits a scikit-learn IsolationForest (fixed `random_state`) over
  a small, leakage-free set of entity-reporting-period features and flags
  observations unusual relative to the modelled population, returning an
  explicit insufficient-sample result when too few observations exist and a
  raw/normalised anomaly signal rather than a risk score. See
  [docs/anomaly-detection.md](docs/anomaly-detection.md).
- A peer benchmark under `app/analytics/peer_benchmark` (service layer, no HTTP
  surface) that compares each entity-period's reported KPIs against the robust
  (median/scaled-MAD) baseline of its genuinely comparable, same-period peers —
  excluding the subject's own value, never pooling incompatible groups, and
  emitting an explicit insufficient-peers status rather than a misleading
  deviation. See [docs/peer-benchmarking.md](docs/peer-benchmarking.md).
- A React frontend shell that reports live backend connectivity and nothing it
  cannot verify.
- A backend test suite (foundation, domain model, data-quality invariants,
  scenario verification, reproducibility, and the full ingestion/validation
  surface) and a working frontend build.

The domain model is conceptually influenced by vendor-neutral event
normalization (e.g. OCSF) but is **not** an OCSF implementation and makes no
compatibility or certification claim. See
[docs/data-model.md](docs/data-model.md).

## Architecture

```
sat-sa/
├── backend/          FastAPI service (Python)
│   └── app/
│       ├── core/       configuration, logging
│       ├── api/v1/     versioned HTTP routes
│       ├── db/         engine, session, declarative base
│       ├── models/     ORM models (SOC domain model)
│       ├── datagen/    synthetic dataset generator (seed-driven)
│       ├── schemas/    Pydantic v2 response models
│       ├── services/   application logic called by routes
│       ├── analytics/  supervisory analytics (execution-gap, negative-space, anomaly, peer-benchmark)
│       └── utils/      small helpers
├── frontend/         React + TypeScript client (Vite)
│   └── src/
│       ├── components/ UI building blocks
│       ├── pages/      composed views
│       ├── services/   API access layer
│       ├── hooks/      React hooks
│       ├── types/      shared TypeScript types
│       └── styles/     design tokens and global styles
├── data/
│   ├── synthetic/    generated synthetic dataset (CSV + JSON, tracked)
│   └── *.db          local SQLite database location (generated; git-ignored)
└── docs/             architecture and development documentation
```

The backend follows a one-way dependency direction: API routes call services,
and services own database and domain interactions. See
[docs/architecture.md](docs/architecture.md) for detail.

## Technology stack

| Area        | Choice                                                     |
| ----------- | ---------------------------------------------------------- |
| Backend     | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x, SQLite  |
| Analytics   | scikit-learn (IsolationForest); NumPy / pandas (datagen)   |
| Backend dev | Uvicorn, pytest, HTTPX / FastAPI TestClient                |
| Frontend    | React 18, TypeScript, Vite 7                               |
| Styling     | Modern CSS with design tokens (CSS custom properties)      |

<!-- APPEND-MARKER -->

## Prerequisites

- Python 3.12 (verified with 3.12.10)
- Node.js 20.19+ or 22.12+ (verified with 24.x) and npm

## Local development

### Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); use .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

# Optional: copy environment template (defaults work without it)
# cp ../.env.example .env   # then keep only the SATSA_* lines

uvicorn app.main:app --reload --port 8000
```

The API is then available at `http://localhost:8000`; health at
`http://localhost:8000/api/v1/health`, interactive docs at
`http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env.local          # sets VITE_API_BASE_URL
npm run dev
```

The app runs at `http://localhost:5173` and reads the backend URL from
`VITE_API_BASE_URL`.

## Testing and checks

```bash
# Backend tests
cd backend && pytest

# Frontend type-check, lint, and production build
cd frontend
npm run type-check
npm run lint
npm run build
```

## Synthetic dataset

Phase 2 ships a deterministic generator for a realistic multi-entity SOC
dataset. From an activated backend environment:

```bash
cd backend
python -m app.datagen                 # default seed, writes to ../data/synthetic
python -m app.datagen --seed 20240601 # explicit seed (this is the default)
```

The same seed always reproduces an identical dataset. Output is written as typed
CSV and JSON per table (entities, assets, alerts, investigations,
investigation_actions, escalations, remediations, telemetry,
performance_metrics) plus a separate `ground_truth` file. Ground truth is
**evaluation data**: it describes planted scenarios and must not be exposed
through production APIs or dashboards in later phases. See
[docs/data-model.md](docs/data-model.md) for the schema and scenario catalog.

## Configuration

All environment-sensitive values are read from the environment with safe local
defaults. Backend variables use the `SATSA_` prefix; the frontend uses Vite's
`VITE_` prefix. See [.env.example](.env.example) and
[docs/development.md](docs/development.md).

Secrets are never committed: `.env` files, generated databases, `node_modules`,
and build output are all git-ignored.

## Roadmap

Later phases build the analytics pipeline on top of this foundation. At a high
level:

- **Done (Phase 2):** the SOC domain data model and a reproducible synthetic
  dataset with planted ground-truth scenarios.
- **Done (Phase 3):** ingestion of SOC operational evidence — CSV/JSON upload,
  schema and semantic validation, canonical normalization, and all-or-nothing
  transactional import with an audit record.
- **Done (Phase 4):** execution-gap detection — a deterministic, read-only rule
  engine that reports observable execution deviations (confirmed critical alert
  closed without escalation; acknowledged high/critical alert never
  investigated; recurring confirmed alerts never remediated) as structured
  findings. It assigns no risk or confidence scores — those are later phases.
- **Done (Phase 5):** negative-space detection — a deterministic, read-only rule
  engine that reports observable absences of expected monitoring evidence
  (critical monitored asset with no telemetry; monitored asset whose telemetry
  disappeared mid-window) as structured findings, emitted only once the data
  establishes the evidence was expected. It, too, assigns no risk or confidence
  scores.
- **Done (Phase 6):** unsupervised anomaly detection (scikit-learn
  IsolationForest, fixed seed, explicit insufficient-sample handling) over
  leakage-free entity-reporting-period features, and peer benchmarking (robust
  median/scaled-MAD comparison against genuinely comparable, same-period peers,
  with a documented zero-MAD fallback and explicit insufficient-peers status).
  Both emit observational signals only — no risk score or performance verdict.

Still **not yet implemented**:

- Metric–risk divergence
- Investigation fingerprinting and evidence-backed findings
- A supervisory review queue and the operational dashboard

Each capability is added deliberately, phase by phase. This README is updated as
functionality actually lands — it does not describe features before they exist.

## License

Released under the [MIT License](LICENSE).

