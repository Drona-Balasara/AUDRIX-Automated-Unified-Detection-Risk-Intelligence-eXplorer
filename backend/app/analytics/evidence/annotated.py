"""Annotated-result containers for evidence-enriched analytic output.

Each ``Annotated*Result`` wraps the original frozen result from a Phase 4–8
analytics run and adds a parallel ``evidence_map`` that associates each
finding's ``finding_key`` with a ``FindingEvidence`` object.

Design rationale
----------------
The Phase 4–8 finding models are frozen Pydantic models: they cannot be mutated
after construction, and it would be unsafe and brittle to rebuild them with new
optional fields.  Instead, Phase 9 delivers evidence and confidence as a
*parallel mapping* keyed by the stable ``finding_key`` that every finding
already carries.

A consumer that only needs the detection result can use the wrapped
``result`` directly.  A consumer that needs evidence and confidence looks up
``evidence_map[finding_key]``.  Both are frozen and deterministic.

These wrappers are thin by design: they add exactly one field
(``evidence_map``) and helper properties/methods over the wrapped result.
No analytic logic lives here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Phase analytic result types — imported at module level (not inside class body)
# to avoid Pydantic treating them as unannotated fields.
from app.analytics.anomaly.engine import AnomalyResult
from app.analytics.execution_gap.engine import ExecutionGapResult
from app.analytics.investigation_fingerprinting.engine import FingerprintResult
from app.analytics.metric_risk_divergence.engine import MetricRiskDivergenceResult
from app.analytics.negative_space.engine import NegativeSpaceResult
from app.analytics.peer_benchmark.engine import PeerBenchmarkResult

from app.analytics.evidence.model import FindingEvidence

# Type alias for the parallel evidence mapping.
EvidenceMap = dict[str, FindingEvidence]


class AnnotatedExecutionGapResult(BaseModel):
    """ExecutionGapResult enriched with per-finding evidence and confidence."""

    model_config = ConfigDict(frozen=True)

    result: ExecutionGapResult
    evidence_map: EvidenceMap = Field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return self.result.finding_count

    def evidence_for(self, finding_key: str) -> FindingEvidence | None:
        return self.evidence_map.get(finding_key)


class AnnotatedNegativeSpaceResult(BaseModel):
    """NegativeSpaceResult enriched with per-finding evidence and confidence."""

    model_config = ConfigDict(frozen=True)

    result: NegativeSpaceResult
    evidence_map: EvidenceMap = Field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return self.result.finding_count

    def evidence_for(self, finding_key: str) -> FindingEvidence | None:
        return self.evidence_map.get(finding_key)


class AnnotatedAnomalyResult(BaseModel):
    """AnomalyResult enriched with per-finding evidence and confidence."""

    model_config = ConfigDict(frozen=True)

    result: AnomalyResult
    evidence_map: EvidenceMap = Field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return self.result.finding_count

    def evidence_for(self, finding_key: str) -> FindingEvidence | None:
        return self.evidence_map.get(finding_key)


class AnnotatedPeerBenchmarkResult(BaseModel):
    """PeerBenchmarkResult enriched with per-finding evidence and confidence."""

    model_config = ConfigDict(frozen=True)

    result: PeerBenchmarkResult
    evidence_map: EvidenceMap = Field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return self.result.finding_count

    def evidence_for(self, finding_key: str) -> FindingEvidence | None:
        return self.evidence_map.get(finding_key)


class AnnotatedMetricRiskDivergenceResult(BaseModel):
    """MetricRiskDivergenceResult enriched with per-finding evidence and confidence."""

    model_config = ConfigDict(frozen=True)

    result: MetricRiskDivergenceResult
    evidence_map: EvidenceMap = Field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return self.result.finding_count

    def evidence_for(self, finding_key: str) -> FindingEvidence | None:
        return self.evidence_map.get(finding_key)


class AnnotatedFingerprintResult(BaseModel):
    """FingerprintResult enriched with per-finding evidence and confidence."""

    model_config = ConfigDict(frozen=True)

    result: FingerprintResult
    evidence_map: EvidenceMap = Field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return self.result.finding_count

    def evidence_for(self, finding_key: str) -> FindingEvidence | None:
        return self.evidence_map.get(finding_key)
