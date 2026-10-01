"""ORM models.

Phase 1 intentionally defines only a minimal :class:`SystemMetadata` table. The
SOC domain entities (alerts, investigations, findings, evidence, review items,
assets, telemetry, escalations, performance metrics, and so on) belong to later
phases and are deliberately not modeled here.
"""

from __future__ import annotations

from app.models.system_metadata import SystemMetadata

__all__ = ["SystemMetadata"]
