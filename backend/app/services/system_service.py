"""System-level service functions.

Encapsulates the small amount of database interaction Phase 1 needs: seeding the
system metadata row and performing a lightweight connectivity check for the
health endpoint. Keeping this logic here (not in route handlers) preserves the
API -> service -> database dependency direction.
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.system_metadata import SystemMetadata

logger = get_logger(__name__)

# Bumped by later phases when the schema changes. Phase 1 is the baseline.
SCHEMA_VERSION = "0.1.0"


def ensure_system_metadata(session: Session) -> SystemMetadata:
    """Ensure exactly one system-metadata row exists, creating it if absent.

    The caller is responsible for committing the transaction.
    """
    existing = session.scalars(select(SystemMetadata).limit(1)).first()
    if existing is not None:
        return existing

    record = SystemMetadata(schema_version=SCHEMA_VERSION)
    session.add(record)
    session.flush()
    logger.info("Seeded system metadata (schema_version=%s).", SCHEMA_VERSION)
    return record


def check_database(session: Session) -> bool:
    """Return ``True`` if a trivial query against the database succeeds.

    Used by the health endpoint. Failures are logged (without leaking the
    database URL) and reported as an unavailable database rather than raised.
    """
    try:
        session.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - health check must not propagate errors
        logger.warning("Database connectivity check failed.")
        return False


def build_health(session: Session) -> dict[str, str]:
    """Assemble the health payload from configuration and a live DB check."""
    settings = get_settings()
    database_ok = check_database(session)
    return {
        "service": settings.service_id,
        "status": "ok" if database_ok else "degraded",
        "version": settings.version,
        "environment": settings.environment,
        "database": "ok" if database_ok else "unavailable",
    }
