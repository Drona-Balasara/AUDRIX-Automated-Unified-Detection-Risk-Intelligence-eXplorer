"""Pydantic v2 schemas for Phase 1 responses.

Only configuration and system/health response models are defined in this phase.
"""

from __future__ import annotations

from app.schemas.health import HealthResponse

__all__ = ["HealthResponse"]
