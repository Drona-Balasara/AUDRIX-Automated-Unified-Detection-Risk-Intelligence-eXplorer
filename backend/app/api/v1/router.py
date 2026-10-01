"""Aggregate router for API v1.

New route modules are included here so :mod:`app.main` only needs to mount a
single router under the ``/api/v1`` prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import health

api_router = APIRouter()
api_router.include_router(health.router, tags=["system"])
