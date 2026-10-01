# Architecture

This document describes the SAT-SA architecture as it exists in Phase 1 and the
intended direction of later phases. It describes current structure; it does not
claim unimplemented functionality exists.

## Overview

SAT-SA is a monorepo with two deployable parts:

- **backend** — a FastAPI (Python) service exposing a versioned HTTP API.
- **frontend** — a React + TypeScript single-page application built with Vite.

In Phase 1 the frontend talks to the backend solely through the health endpoint
to establish and display connectivity.

```
┌────────────┐      HTTP / JSON       ┌─────────────────────────┐
│  Frontend  │ ─────────────────────▶ │  Backend (FastAPI)      │
│  (React)   │   /api/v1/health       │                         │
└────────────┘                        │  API ─▶ services ─▶ DB  │
                                      └───────────┬─────────────┘
                                                  │ SQLAlchemy 2.x
                                                  ▼
                                            SQLite (data/)
```

## Backend layering

The backend enforces a one-way dependency direction:

```
api (routes)  ──▶  services  ──▶  db / models
      │                               ▲
      └────────── schemas ────────────┘
```

- **api** (`app/api/v1`) — route handlers. They validate input, call a service,
  and return a Pydantic schema. Handlers contain no database logic.
- **services** (`app/services`) — application logic. Services own database
  sessions passed to them and perform the actual queries and domain operations.
- **db** (`app/db`) — the SQLAlchemy engine, session factory (`SessionLocal`),
  the `get_db` request-scoped dependency, declarative `Base`, and `init_db`.
- **models** (`app/models`) — ORM models registered on `Base.metadata`.
- **schemas** (`app/schemas`) — Pydantic v2 models defining the API contract.
- **core** (`app/core`) — typed configuration and logging setup.

### Configuration

`app/core/config.py` defines a typed `Settings` model (via `pydantic-settings`).
Values come from the process environment or a local `.env` file, with safe
defaults. Settings are cached (`get_settings`) so the environment is parsed
once per process. The database URL and CORS origins are configurable so other
environments can override them without code changes.

### Database and sessions

A single `Engine` is created from the configured URL. Sessions are produced per
request by `get_db`, which yields a session and closes it afterwards — sessions
are never shared across requests or threads. For SQLite,
`check_same_thread=False` is set so FastAPI's threadpool workers can use the
connection safely, with each request isolated to its own session.

`init_db` runs on application startup (via the FastAPI lifespan handler): it
creates tables and ensures a single `system_metadata` row exists. This record
captures the schema version and initialization time, giving later phases a
foundation for migrations.

### Error handling and logging

A catch-all exception handler logs full error detail server-side and returns a
generic `{"detail": "An internal error occurred."}` with HTTP 500. Stack traces,
the database URL, and other internals are never sent to clients. Logging is
configured centrally through the standard library; log statements must never
include credentials, tokens, authorization headers, or request bodies.

## Frontend structure

- **services** (`src/services/api.ts`) — the single place that performs network
  requests. The backend base URL comes from `VITE_API_BASE_URL`.
- **hooks** (`src/hooks/useHealth.ts`) — exposes the backend connection as an
  honest state machine: `loading`, `connected`, `unavailable`, `error`.
- **components / pages** — presentational building blocks and composed views.
- **styles** (`src/styles/tokens.css`) — design tokens as CSS custom properties
  (color, spacing, radius, typography, motion) with light and dark variants.

The UI only displays what it can verify (the live connection state and the
fields the health endpoint actually returns). It fabricates no metrics,
findings, or analytics.

## Intended later direction (not implemented)

Later phases build an analytics pipeline on this foundation. The anticipated
shape, kept here only to guide structure:

1. **Evidence ingestion** — model SOC operational evidence into the database.
2. **Execution-gap & negative-space detection** — identify missing expected
   evidence and incomplete execution.
3. **Pattern / anomaly detection** — surface unusual operational patterns.
4. **Peer comparison & metric–risk divergence** — compare comparable entities.
5. **Investigation fingerprinting & findings** — produce evidence-backed
   findings.
6. **Supervisory review queue & dashboard** — present findings for human review.

Analytics code will live under `app/analytics` with corresponding models,
schemas, and services. The dependency direction and configuration/logging
conventions established in Phase 1 remain unchanged as these are added.
