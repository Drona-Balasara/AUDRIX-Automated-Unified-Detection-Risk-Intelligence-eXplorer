"""Investigation Fingerprinting (Phase 8).

Represents SOC investigations as ordered action-type sequences (fingerprints)
and identifies potentially unusual, repetitive, incomplete, or template-driven
investigation workflows using transparent, deterministic sequence analysis.

A fingerprint is the ordered tuple of ``ActionType`` string values extracted
from an investigation's ``InvestigationAction`` rows, sorted by
``sequence_number`` (with ``occurred_at`` as tiebreaker).  For example:
``("OPEN", "ASSET_LOOKUP", "EVENT_SEARCH", "CLOSE")``.

Three analytically distinct detectors are implemented:

``IF-REP-001`` — Potential Template-Driven Investigation Pattern
    Within each entity-reporting-period window, flags windows where a
    substantial fraction of investigations share an identical normalized
    action sequence.  Detects the planted
    ``REPETITIVE_INVESTIGATION_WORKFLOW`` ground-truth scenario.

``IF-DEV-002`` — Potential Investigation Sequence Deviation
    For each investigation, compares its fingerprint against the entity's
    plurality-baseline fingerprint derived from prior investigations (no
    future leakage).  Investigations whose normalized edit distance from
    that baseline exceeds the threshold are flagged.

``IF-MEA-003`` — Potential Missing Investigation Action
    For closed investigations on HIGH or CRITICAL severity alerts, flags
    cases where neither ``VALIDATE`` nor ``EVIDENCE_REVIEW`` appears in the
    action sequence.  Justified by the domain's full-workflow constant and
    NIST SP 800-61 Rev. 3 (April 2025) guidance on structured incident
    analysis and validation.

Sequence similarity uses a dependency-free normalized Levenshtein edit
distance implementation in :mod:`app.analytics.investigation_fingerprinting.sequence`.
No external ML dependency is introduced.  No database migration is required.

Public API:

- :func:`run_investigation_fingerprinting` — service entry point.
- :class:`FingerprintConfig` — typed, immutable configuration.
- :class:`FingerprintResult` — structured outcome.
- :class:`RepetitiveWorkflowFinding`
- :class:`SequenceDeviationFinding`
- :class:`MissingExpectedActionFinding`
"""

from __future__ import annotations

from app.analytics.investigation_fingerprinting.config import FingerprintConfig
from app.analytics.investigation_fingerprinting.engine import (
    FingerprintResult,
    run_fingerprint_detection,
)
from app.analytics.investigation_fingerprinting.findings import (
    DEVIATION_ANALYTIC_ID,
    MISSING_ACTION_ANALYTIC_ID,
    REPETITIVE_ANALYTIC_ID,
    FingerprintConfidence,
    FingerprintFindingType,
    MissingExpectedActionFinding,
    RepetitiveWorkflowFinding,
    SequenceDeviationFinding,
)
from app.analytics.investigation_fingerprinting.sequence import (
    Fingerprint,
    build_fingerprint,
    normalized_distance,
    similarity,
)
from app.analytics.investigation_fingerprinting.service import (
    run_investigation_fingerprinting,
)

__all__ = [
    "run_investigation_fingerprinting",
    "run_fingerprint_detection",
    "FingerprintConfig",
    "FingerprintResult",
    "RepetitiveWorkflowFinding",
    "SequenceDeviationFinding",
    "MissingExpectedActionFinding",
    "FingerprintFindingType",
    "FingerprintConfidence",
    "Fingerprint",
    "build_fingerprint",
    "normalized_distance",
    "similarity",
    "REPETITIVE_ANALYTIC_ID",
    "DEVIATION_ANALYTIC_ID",
    "MISSING_ACTION_ANALYTIC_ID",
]
