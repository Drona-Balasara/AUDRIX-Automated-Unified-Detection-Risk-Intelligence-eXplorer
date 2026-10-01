# SAT-SA — Security Assessment & Supervisory Analytics

SAT-SA is an evidence-driven security operations supervisory analytics platform.
Its purpose is to assess SOC operational evidence, identify execution gaps and
missing expected evidence, detect unusual operational patterns, compare
comparable entities, and surface evidence-backed findings that require human
supervisory review.

This repository contains the full application: a Python/FastAPI backend and a
React/TypeScript frontend, organized as a single monorepo.

## Current status

**Phase 1 — Project Foundation.**

Phase 1 establishes the architecture, tooling, and a working end-to-end
baseline. The analytical capabilities described above are **not implemented
yet**; they are introduced in later phases (see [Roadmap](#roadmap)). What
exists today:

- A FastAPI application with a versioned API and a `GET /api/v1/health` endpoint.
- Typed configuration, centralized logging, and a SQLAlchemy 2.x database layer
  with a minimal system-metadata table.
- A React frontend shell that reports live backend connectivity and nothing it
  cannot verify.
- A backend test suite and a working frontend production build.

## Architecture

```
sat-sa/
├── backend/          FastAPI service (Python)
│   └── app/
│       ├── core/       configuration, logging
│       ├── api/v1/     versioned HTTP routes
│       ├── db/         engine, session, declarative base
│       ├── models/     ORM models (minimal in Phase 1)
│       ├── schemas/    Pydantic v2 response models
│       ├── services/   application logic called by routes
│       ├── analytics/  reserved for the later analytics pipeline
│       └── utils/      small helpers
├── frontend/         React + TypeScript client (Vite)
│   └── src/
│       ├── components/ UI building blocks
│       ├── pages/      composed views
│       ├── services/   API access layer
│       ├── hooks/      React hooks
│       ├── types/      shared TypeScript types
│       └── styles/     design tokens and global styles
├── data/             local SQLite database location (generated; git-ignored)
└── docs/             architecture and development documentation
```

The backend follows a one-way dependency direction: API routes call services,
and services own database and domain interactions. See
[docs/architecture.md](docs/architecture.md) for detail.

## Technology stack

| Area        | Choice                                                     |
| ----------- | ---------------------------------------------------------- |
| Backend     | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x, SQLite  |
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

## Configuration

All environment-sensitive values are read from the environment with safe local
defaults. Backend variables use the `SATSA_` prefix; the frontend uses Vite's
`VITE_` prefix. See [.env.example](.env.example) and
[docs/development.md](docs/development.md).

Secrets are never committed: `.env` files, generated databases, `node_modules`,
and build output are all git-ignored.

## Roadmap

Later phases build the analytics pipeline on top of this foundation. At a high
level, and **not yet implemented**:

- Ingestion and modeling of SOC operational evidence
- Execution-gap and missing-evidence (negative-space) detection
- Unusual-pattern / anomaly detection
- Peer comparison and metric–risk divergence
- Investigation fingerprinting and evidence-backed findings
- A supervisory review queue and the operational dashboard

Each capability is added deliberately, phase by phase. This README is updated as
functionality actually lands — it does not describe features before they exist.

## License

Released under the [MIT License](LICENSE).

