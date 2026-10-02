"""SQLAlchemy 2.x engine and session lifecycle.

A single :class:`~sqlalchemy.engine.Engine` is created from the configured
database URL. Sessions are produced per request by :func:`get_db`, which yields
a session bound to that request and always closes it afterwards. Sessions are
never shared across requests or threads.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.base import Base

logger = get_logger(__name__)

settings = get_settings()


def enable_sqlite_fk(target_engine: Engine) -> None:
    """Enforce SQLite foreign keys for every connection of ``target_engine``.

    SQLite does not enforce foreign keys unless ``PRAGMA foreign_keys=ON`` is set
    per connection. Enabling it here means a failed/invalid ingestion import can
    never leave dangling references, and the pragma is applied before any
    transaction begins. No-op for non-SQLite backends.
    """
    if not target_engine.url.get_backend_name().startswith("sqlite"):
        return

    @event.listens_for(target_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _make_engine_kwargs(database_url: str) -> dict[str, object]:
    """Return engine keyword arguments appropriate for the backend in use.

    SQLite needs ``check_same_thread=False`` so a connection created in one
    thread can be used by FastAPI's threadpool workers. This is safe because
    each request uses its own :class:`Session` and we do not share connections
    across threads for concurrent writes.
    """
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


def _ensure_sqlite_dir(database_url: str) -> None:
    """Create the parent directory for a file-backed SQLite database if needed."""
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        db_path = database_url[len(prefix):]
        # Skip in-memory databases (e.g. ``sqlite://`` or ``:memory:``).
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_engine(settings.database_url, **_make_engine_kwargs(settings.database_url))
enable_sqlite_fk(engine)

# ``expire_on_commit=False`` keeps returned objects usable after commit, which is
# convenient for request handlers that serialize an object immediately.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    """Create all tables and seed baseline system metadata.

    Importing :mod:`app.models` here (rather than at module load) ensures every
    model is registered on ``Base.metadata`` before ``create_all`` runs, while
    avoiding import cycles.
    """
    from app import models  # noqa: F401  (import for side-effect: model registration)
    from app.services.system_service import ensure_system_metadata

    Base.metadata.create_all(bind=engine)

    with SessionLocal() as session:
        ensure_system_metadata(session)
        session.commit()

    logger.info("Database initialized (schema created, system metadata ensured).")
