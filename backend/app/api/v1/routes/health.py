"""Health and system-status endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.health import HealthResponse
from app.services.system_service import build_health

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Service health check")
def get_health(session: Session = Depends(get_db)) -> HealthResponse:
    """Report service identity, version, environment, and database connectivity.

    Returns HTTP 200 whenever the service is running. The ``status`` and
    ``database`` fields communicate whether the database dependency is healthy,
    so monitoring can distinguish "up" from "up but degraded".
    """
    return HealthResponse(**build_health(session))
