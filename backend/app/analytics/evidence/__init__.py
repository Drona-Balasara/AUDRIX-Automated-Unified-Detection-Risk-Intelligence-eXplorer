"""Evidence and Confidence layer (Phase 9).

This package implements the shared evidence and confidence infrastructure for
all SAT-SA analytical findings.  Its purpose is to explain *which source
records* support a finding and *how sufficient* the supporting data is.

Design references
-----------------
- NIST SP 800-61 Rev. 3 (April 2025): Emphasises recording and preserving the
  provenance of incident-response actions and observations so findings can be
  traced to specific source records.
- NIST SP 800-55 Vol. 1 & 2 (December 2024): Information-security measurement
  programs acknowledge inherent data-quality limitations and uncertainty;
  measurement results should be accompanied by data-quality and uncertainty
  context.

Architecture
------------
Evidence and confidence are delivered as a *parallel mapping* keyed by
``finding_key``.  The existing Phase 4–8 finding models are frozen Pydantic
models that are not modified.  Each ``AnnotatedResult`` wraps the original
frozen result and adds an ``evidence_map: dict[finding_key, FindingEvidence]``.

Evidence model:
    ``EvidenceRef`` — lightweight pointer to one source record (type, ID, role,
    period, reason).  Absent evidence is represented as ABSENCE source type
    with a descriptive scope, never as a fabricated record ID.

    ``FindingEvidence`` — container for all evidence refs for one finding, plus
    a categorical ``EvidenceConfidence`` (HIGH/MODERATE/LOW) and the factors
    that led to it.

Confidence model:
    ``EvidenceConfidence`` — shared three-level categorical confidence enum.
    Represents data sufficiency, not risk or severity.

    ``ConfidenceFactor`` — machine-readable labels for specific data-quality
    considerations (e.g. FRAGILE_BASELINE, SMALL_BASELINE, ABSENCE_BASED).

    Aggregation: the weakest applicable factor determines the confidence level.

Public API:

- :class:`EvidenceRef` — one source record reference.
- :class:`FindingEvidence` — evidence container for one finding.
- :class:`EvidenceSourceType` — source type vocabulary.
- :class:`EvidenceConfidence` — shared confidence enum.
- :class:`ConfidenceFactor` — confidence factor vocabulary.
- :func:`make_finding_evidence` — constructor with deduplication/sorting.
- ``Annotated*Result`` wrappers — one per analytic.
- ``annotate_*`` service functions — one per analytic.
"""

from __future__ import annotations

from app.analytics.evidence.annotated import (
    AnnotatedAnomalyResult,
    AnnotatedExecutionGapResult,
    AnnotatedFingerprintResult,
    AnnotatedMetricRiskDivergenceResult,
    AnnotatedNegativeSpaceResult,
    AnnotatedPeerBenchmarkResult,
)
from app.analytics.evidence.model import (
    ConfidenceFactor,
    EvidenceConfidence,
    EvidenceRef,
    EvidenceSourceType,
    FindingEvidence,
    make_finding_evidence,
)
from app.analytics.evidence.service import (
    annotate_anomaly,
    annotate_execution_gap,
    annotate_fingerprint,
    annotate_metric_risk_divergence,
    annotate_negative_space,
    annotate_peer_benchmark,
)

__all__ = [
    # Model
    "EvidenceRef",
    "FindingEvidence",
    "EvidenceSourceType",
    "EvidenceConfidence",
    "ConfidenceFactor",
    "make_finding_evidence",
    # Annotated results
    "AnnotatedExecutionGapResult",
    "AnnotatedNegativeSpaceResult",
    "AnnotatedAnomalyResult",
    "AnnotatedPeerBenchmarkResult",
    "AnnotatedMetricRiskDivergenceResult",
    "AnnotatedFingerprintResult",
    # Service functions
    "annotate_execution_gap",
    "annotate_negative_space",
    "annotate_anomaly",
    "annotate_peer_benchmark",
    "annotate_metric_risk_divergence",
    "annotate_fingerprint",
]
