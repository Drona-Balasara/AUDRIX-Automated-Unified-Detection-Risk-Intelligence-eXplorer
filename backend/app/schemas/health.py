"""Health and system-status response schemas (Pydantic v2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Structured response for ``GET /api/v1/health``.

    Deliberately exposes only safe, stable fields. Filesystem paths, the
    database URL, environment variables, and internal details are never
    included.
    """

    service: str = Field(description="Stable machine identifier for this service.")
    status: Literal["ok", "degraded"] = Field(
        description="'ok' when core dependencies respond; 'degraded' otherwise."
    )
    version: str = Field(description="Application version.")
    environment: str = Field(description="Deployment environment name.")
    database: Literal["ok", "unavailable"] = Field(
        description="Result of a lightweight database connectivity check."
    )
