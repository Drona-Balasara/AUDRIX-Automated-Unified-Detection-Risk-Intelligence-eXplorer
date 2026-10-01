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
