"""Public service functions for the evidence and confidence layer.

These functions are the primary integration points called by the Phase 4–8
service boundaries.  Each one accepts an already-loaded analytics context and
its corresponding detection result, builds the evidence map, and returns an
annotated result that wraps the original frozen result.

All functions are FastAPI-independent, deterministic, read-only, and
side-effect-free.  They issue no database queries; all source record IDs are
extracted from the in-memory context and result objects.

Logging emits only safe operational metadata.
"""

from __future__ import annotations

from time import perf_counter

from app.analytics.evidence.annotated import (
    AnnotatedAnomalyResult,
    AnnotatedExecutionGapResult,
    AnnotatedFingerprintResult,
    AnnotatedMetricRiskDivergenceResult,
    AnnotatedNegativeSpaceResult,
    AnnotatedPeerBenchmarkResult,
)
from app.analytics.evidence.builders import (
    build_anomaly_evidence,
    build_execution_gap_evidence,
    build_fingerprint_evidence,
    build_metric_risk_divergence_evidence,
    build_negative_space_evidence,
    build_peer_benchmark_evidence,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


def annotate_execution_gap(result) -> AnnotatedExecutionGapResult:
    """Attach evidence and confidence to an execution-gap result."""
    t0 = perf_counter()
    evidence_map = build_execution_gap_evidence(result)
    duration_ms = (perf_counter() - t0) * 1000.0
    logger.info(
        "evidence built: analytic=EG-001 findings=%d evidence_entries=%d duration_ms=%.1f",
        result.finding_count, len(evidence_map), duration_ms,
    )
    return AnnotatedExecutionGapResult(result=result, evidence_map=evidence_map)


def annotate_negative_space(result) -> AnnotatedNegativeSpaceResult:
    """Attach evidence and confidence to a negative-space result."""
    t0 = perf_counter()
    evidence_map = build_negative_space_evidence(result)
    duration_ms = (perf_counter() - t0) * 1000.0
    logger.info(
        "evidence built: analytic=NS-001 findings=%d evidence_entries=%d duration_ms=%.1f",
        result.finding_count, len(evidence_map), duration_ms,
    )
    return AnnotatedNegativeSpaceResult(result=result, evidence_map=evidence_map)


def annotate_anomaly(result) -> AnnotatedAnomalyResult:
    """Attach evidence and confidence to an anomaly-detection result."""
    t0 = perf_counter()
    evidence_map = build_anomaly_evidence(result)
    duration_ms = (perf_counter() - t0) * 1000.0
    logger.info(
        "evidence built: analytic=AN-001 findings=%d evidence_entries=%d duration_ms=%.1f",
        result.finding_count, len(evidence_map), duration_ms,
    )
    return AnnotatedAnomalyResult(result=result, evidence_map=evidence_map)


def annotate_peer_benchmark(result) -> AnnotatedPeerBenchmarkResult:
    """Attach evidence and confidence to a peer-benchmark result."""
    t0 = perf_counter()
    evidence_map = build_peer_benchmark_evidence(result)
    duration_ms = (perf_counter() - t0) * 1000.0
    logger.info(
        "evidence built: analytic=PB-001 findings=%d evidence_entries=%d duration_ms=%.1f",
        result.finding_count, len(evidence_map), duration_ms,
    )
    return AnnotatedPeerBenchmarkResult(result=result, evidence_map=evidence_map)


def annotate_metric_risk_divergence(result) -> AnnotatedMetricRiskDivergenceResult:
    """Attach evidence and confidence to a metric-risk-divergence result."""
    t0 = perf_counter()
    evidence_map = build_metric_risk_divergence_evidence(result)
    duration_ms = (perf_counter() - t0) * 1000.0
    logger.info(
        "evidence built: analytic=MRD-001 findings=%d evidence_entries=%d duration_ms=%.1f",
        result.finding_count, len(evidence_map), duration_ms,
    )
    return AnnotatedMetricRiskDivergenceResult(result=result, evidence_map=evidence_map)


def annotate_fingerprint(result) -> AnnotatedFingerprintResult:
    """Attach evidence and confidence to an investigation-fingerprinting result."""
    t0 = perf_counter()
    evidence_map = build_fingerprint_evidence(result)
    duration_ms = (perf_counter() - t0) * 1000.0
    logger.info(
        "evidence built: analytic=IF findings=%d evidence_entries=%d duration_ms=%.1f",
        result.finding_count, len(evidence_map), duration_ms,
    )
    return AnnotatedFingerprintResult(result=result, evidence_map=evidence_map)
