"""Negative-space detection (Phase 5).

A small, deterministic, read-only detection framework that reports *observable*
absences of expected evidence in the SOC operational record — a critical
monitored asset with no telemetry at all (monitoring coverage gap), or a
monitored asset whose telemetry stopped before the end of the observed window
(telemetry continuity gap).

The governing discipline is that absence is not automatically evidence of
failure: a finding is only emitted once existing data establishes that a signal
was reasonably expected during a defined window. Findings describe *potential*
visibility gaps in strictly observational language and never assert tampering,
intentional disabling, or malicious suppression.

It reuses the Phase 4 architecture (rule registry, frozen config, read-only
context, pure engine, FastAPI-independent service) without coupling to the
execution-gap internals. Rule ownership is disjoint from Phase 4: that phase
reasons about alert handling; this phase reasons about monitoring telemetry.

Public API:
- ``run_negative_space_detection`` — the service entry point (session in,
  structured result out).
- ``NegativeSpaceResult`` / ``NegativeSpaceFinding`` — the structured outputs.
- ``NegativeSpaceConfig`` — thresholds and temporal windows.
- ``get_rules`` / ``REGISTRY`` — the discoverable rule allowlist.
"""

from __future__ import annotations

from app.analytics.negative_space.config import NegativeSpaceConfig
from app.analytics.negative_space.engine import NegativeSpaceResult, run_rules
from app.analytics.negative_space.findings import (
    GapType,
    NegativeSpaceFinding,
    ReasonCode,
)
from app.analytics.negative_space.rules import REGISTRY, get_rules
from app.analytics.negative_space.service import run_negative_space_detection

__all__ = [
    "NegativeSpaceConfig",
    "NegativeSpaceResult",
    "run_rules",
    "NegativeSpaceFinding",
    "GapType",
    "ReasonCode",
    "REGISTRY",
    "get_rules",
    "run_negative_space_detection",
]
