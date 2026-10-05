"""SAT-SA FastAPI application entry point.

Builds the application, configures logging and CORS, initializes the database on
startup, mounts the versioned API, and installs a safe fallback exception
handler that logs full details server-side while returning a generic message to
clients (never a stack trace, database URL, or secret).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database foundation on startup and seed if empty."""
    from app.db.session import init_db, SessionLocal
    from app.services.seed_service import seed_synthetic_data

    logger.info("Starting %s (%s) in %s mode.", settings.app_name, settings.version, settings.environment)
    init_db()

    try:
        with SessionLocal() as session:
            seed_synthetic_data(session, force=False)
    except Exception:
        logger.exception("Auto-seed during startup encountered an error; continuing startup.")

    yield
    logger.info("Shutting down %s.", settings.app_name)


def create_app() -> FastAPI:
    """Application factory. Keeps construction testable and import-friendly."""
    app = FastAPI(
        title=settings.app_name,
        description=settings.app_descriptor,
        version=settings.version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Log unexpected errors in full; return a generic, safe response."""
        logger.exception("Unhandled error processing %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "An internal error occurred."},
        )

    @app.get("/", tags=["system"], summary="Root health & status")
    def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "status": "online",
            "version": settings.version,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    @app.get("/health", tags=["system"], summary="Root health check alias")
    def health_root() -> dict[str, str]:
        return {
            "status": "ok",
            "service": settings.service_id,
            "endpoint": "/api/v1/health",
        }

    @app.post("/api/v1/seed", tags=["system"], summary="Seed synthetic local dataset and run assessment")
    @app.get("/api/v1/seed", tags=["system"], summary="Seed synthetic local dataset and run assessment (GET alias)")
    def seed_endpoint(force: bool = False) -> dict[str, object]:
        """Manually trigger ingestion of the local synthetic dataset and initial assessment."""
        from app.db.session import SessionLocal
        from app.services.seed_service import seed_synthetic_data

        with SessionLocal() as session:
            return seed_synthetic_data(session, force=force)

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
