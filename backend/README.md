# SAT-SA Backend

FastAPI service for SAT-SA (Security Assessment & Supervisory Analytics).

Phase 1 provides the application foundation: configuration, logging, a
SQLAlchemy 2.x database layer, a versioned API, and a health endpoint. No
analytics are implemented yet.

## Requirements

- Python 3.12

## Setup

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); .venv/bin/activate elsewhere
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

- Health: `GET http://localhost:8000/api/v1/health`
- OpenAPI docs: `http://localhost:8000/docs`

The database is created automatically on startup at the location given by
`SATSA_DATABASE_URL` (defaults to a SQLite file under the repository `data/`
directory).

## Tests

```bash
pytest
```

Tests run against an isolated in-memory SQLite database and never touch the
development database or any external service.

## Layout

```
app/
├── main.py        application entry point (app factory, CORS, lifespan)
├── core/          typed configuration (config.py) and logging (logging.py)
├── api/v1/        versioned router and route modules
├── db/            engine, session factory, declarative base
├── models/        ORM models (minimal: system_metadata)
├── schemas/       Pydantic v2 response models
├── services/      application logic invoked by routes
├── analytics/     reserved for later analytics phases
└── utils/         small helpers
```

## Dependencies

| Package           | Purpose                                             |
| ----------------- | --------------------------------------------------- |
| fastapi           | Web framework and routing                           |
| uvicorn[standard] | ASGI server (with reload for development)           |
| pydantic          | Request/response validation (v2)                    |
| pydantic-settings | Typed environment configuration                     |
| SQLAlchemy        | ORM and database access (2.x)                       |
| pytest            | Test runner                                         |
| httpx             | HTTP client used by FastAPI's TestClient            |

## Conventions

- Routes stay thin and delegate to `services`; services own database access.
- Use type hints throughout; prefer SQLAlchemy 2.x `select()` over legacy Query.
- Never log credentials, tokens, headers, or request bodies.
- API responses never expose stack traces, the database URL, or internals.
