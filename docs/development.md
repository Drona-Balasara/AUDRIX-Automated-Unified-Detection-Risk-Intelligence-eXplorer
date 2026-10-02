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
