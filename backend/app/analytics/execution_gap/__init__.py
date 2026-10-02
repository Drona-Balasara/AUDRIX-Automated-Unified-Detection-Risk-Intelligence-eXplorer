"""Execution-gap detection (Phase 4).

A small, deterministic, read-only detection framework that reports *observable*
execution deviations in the SOC operational record (e.g. a confirmed critical
alert closed with no escalation). It separates rule definitions and
configuration from normalized input access, rule evaluation, and structured
results, so later phases (Evidence, Confidence, Prioritization, Supervisory
Review) can build on stable finding keys and machine-readable reason codes.

Public API:
- ``run_execution_gap_detection`` — the service entry point (session in,
  structured result out).
- ``ExecutionGapResult`` / ``ExecutionGapFinding`` — the structured outputs.
- ``ExecutionGapConfig`` — thresholds and temporal windows.
- ``get_rules`` / ``REGISTRY`` — the discoverable rule allowlist.
"""

from __future__ import annotations

from app.analytics.execution_gap.config import ExecutionGapConfig
from app.analytics.execution_gap.engine import ExecutionGapResult, run_rules
from app.analytics.execution_gap.findings import (
    ExecutionGapFinding,
    GapType,
    ReasonCode,
)
from app.analytics.execution_gap.rules import REGISTRY, get_rules
from app.analytics.execution_gap.service import run_execution_gap_detection

__all__ = [
    "ExecutionGapConfig",
    "ExecutionGapResult",
    "run_rules",
    "ExecutionGapFinding",
    "GapType",
    "ReasonCode",
    "REGISTRY",
    "get_rules",
    "run_execution_gap_detection",
]
