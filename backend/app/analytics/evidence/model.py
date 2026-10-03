"""Core evidence and confidence data models for Phase 9.

This module defines the shared, reusable evidence layer that all SAT-SA
analytical findings can reference.  Its purpose is to explain *which source
records* support a finding and *how sufficient* the supporting data is, without
duplicating database rows or inventing records that do not exist.

Design principles
-----------------
Traceability (NIST SP 800-61 Rev. 3, April 2025):
    Analytical findings should be traceable to the specific source records and
    observations that triggered them.  Every ``EvidenceRef`` carries enough
    information to retrieve the source record deterministically: a stable type
    label, the record's primary ID, its role in the finding, and a concise
    human-readable reason.

Data quality and uncertainty (NIST SP 800-55 Vol. 1 & 2, December 2024):
    Information-security measurement programs acknowledge inherent data-quality
    limitations and uncertainty.  The ``EvidenceConfidence`` enum and
    ``ConfidenceFactor`` vocabulary translate that guidance into a categorical,
    explainable confidence model — no pseudo-precise percentages.

Non-fabrication:
    Evidence references MUST correspond to records that actually exist in the
    source database.  Absent evidence (e.g. missing telemetry) is represented
    by an ``EvidenceRef`` with ``source_type=EvidenceSourceType.ABSENCE``, a
    descriptive ``source_id``, and a ``reason`` that explains what was expected
    but not found.  No fake record IDs are created.

Separation of concerns:
    Evidence does not equal risk or severity.  Confidence describes data
    sufficiency, not how dangerous a finding is.  Confidence must never be
    conflated with a risk score in display, logging, or downstream processing.

Backward compatibility:
    Existing finding models (Phases 4–8) are frozen Pydantic models that cannot
    be mutated.  Phase 9 enriches findings through a parallel mapping keyed by
    ``finding_key``, not by embedding new fields in the frozen models.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StrEnum


# ---------------------------------------------------------------------------
# Source type vocabulary
# ---------------------------------------------------------------------------

class EvidenceSourceType(StrEnum):
    """Vocabulary of source record types that can be cited as evidence.

    Each value corresponds to an ORM model (or a computed observation type)
    present in the SAT-SA domain model.  New types should only be added when
    the underlying schema can support them.
    """

    ALERT               = "ALERT"
    INVESTIGATION       = "INVESTIGATION"
    INVESTIGATION_ACTION = "INVESTIGATION_ACTION"
    ESCALATION          = "ESCALATION"
    REMEDIATION         = "REMEDIATION"
    TELEMETRY_RECORD    = "TELEMETRY_RECORD"
    PERFORMANCE_METRIC  = "PERFORMANCE_METRIC"
    ENTITY              = "ENTITY"
    ASSET               = "ASSET"

    # Computed / derived observation types (not raw ORM rows):
    ENTITY_PERIOD_OBSERVATION = "ENTITY_PERIOD_OBSERVATION"
    # Used for peer-benchmark baselines where the evidence is a set of peer
    # performance-metric rows, not a single primary-key record.
    PEER_BASELINE       = "PEER_BASELINE"

    # Structured absence: records that were expected but not present.
    # source_id should describe the expected record scope (e.g. "asset_id:AST-00123").
    ABSENCE             = "ABSENCE"


# ---------------------------------------------------------------------------
# Confidence vocabulary
# ---------------------------------------------------------------------------

class EvidenceConfidence(StrEnum):
    """Shared data-quality / observational-sufficiency indicator.

    HIGH:     Multiple direct source records support the finding; the analytical
              baseline is well-established; all relevant observations are
              complete; no significant data-quality concerns.

    MODERATE: The finding is supported by adequate evidence, but at least one
              factor limits full confidence — e.g. a small comparison population,
              some missing observations, or a baseline derived from few prior
              cases.  The pattern is detectable but the evidence base is not
              exhaustive.

    LOW:      The finding is technically valid but rests on a thin or fragile
              evidence base — e.g. a minimum-threshold observation count, a
              baseline derived from very few cases, large fractions of missing
              data, or a period so short that the trend estimate is noisy.  The
              finding warrants review but should be treated as a preliminary
              signal.

    This enum is intentionally isomorphic with the existing ``ConfidenceLevel``
    (metric_risk_divergence) and ``FingerprintConfidence`` (investigation_fingerprinting)
    enums; those phase-specific enums are preserved for backward compatibility
    and are not replaced.
    """

    HIGH     = "HIGH"
    MODERATE = "MODERATE"
    LOW      = "LOW"


class ConfidenceFactor(StrEnum):
    """Machine-readable labels for the specific factors that influence confidence.

    Each factor is evaluated independently; the weakest applicable factor
    determines the overall confidence level (see ``confidence.py``).

    These factor names are descriptive rather than formulaic: they communicate
    *why* confidence is limited to a human reviewer without making a precise
    statistical claim.
    """

    # Positive factors (supporting HIGH confidence)
    MULTIPLE_DIRECT_RECORDS  = "MULTIPLE_DIRECT_RECORDS"   # >=3 direct source records
    COMPLETE_OBSERVATIONS    = "COMPLETE_OBSERVATIONS"     # no dropped/missing records
    LARGE_COMPARISON_POP     = "LARGE_COMPARISON_POP"      # >=6 comparable peers/observations

    # Neutral/moderate factors
    ADEQUATE_RECORD_COUNT    = "ADEQUATE_RECORD_COUNT"     # 1–2 direct records (sufficient but minimal)
    MODERATE_BASELINE        = "MODERATE_BASELINE"         # 3–5 baseline observations
    PARTIAL_OBSERVATIONS     = "PARTIAL_OBSERVATIONS"      # some dropped/incomplete records

    # Limiting factors (leading to MODERATE or LOW confidence)
    SINGLE_RECORD            = "SINGLE_RECORD"             # exactly 1 direct source record
    SMALL_BASELINE           = "SMALL_BASELINE"            # baseline from < 3 prior observations
    FRAGILE_BASELINE         = "FRAGILE_BASELINE"          # baseline explicitly documented as fragile
    INSUFFICIENT_PERIODS     = "INSUFFICIENT_PERIODS"      # fewer than recommended observation periods
    DROPPED_OBSERVATIONS     = "DROPPED_OBSERVATIONS"      # some observations dropped (incomplete)
    ABSENCE_BASED            = "ABSENCE_BASED"             # finding depends on absence of evidence
    MINIMUM_THRESHOLD        = "MINIMUM_THRESHOLD"         # finding is at the exact minimum threshold


# ---------------------------------------------------------------------------
# Evidence reference
# ---------------------------------------------------------------------------

class EvidenceRef(BaseModel):
    """One reference to a source record that supports a finding.

    An ``EvidenceRef`` is a lightweight pointer, not a copy of the database row.
    It carries enough information to locate the record deterministically and to
    explain its relevance to the finding.  It never embeds full row contents,
    credentials, or raw payloads.

    Ordering and stability
    ----------------------
    ``EvidenceRef`` objects within a ``FindingEvidence`` are ordered
    deterministically by ``(source_type, source_id)`` so repeated runs over
    identical data produce identical output.  Source IDs are stable primary-key
    or composite-key strings from the domain model.

    Absence representation
    ----------------------
    When a finding depends on the *absence* of an expected record (e.g. no
    telemetry for a monitored asset, no escalation for a critical alert), use
    ``source_type=EvidenceSourceType.ABSENCE``.  The ``source_id`` should
    describe the expected scope (e.g. ``"entity:ENT-01|asset:AST-00042"``).
    The ``reason`` field must explain what was expected and that it was not
    found.  Do not fabricate a fake primary-key ID for the non-existent record.
    """

    model_config = ConfigDict(frozen=True)

    source_type: EvidenceSourceType
    # Stable identifier for the source record.  For ORM rows: the primary key
    # string.  For computed observations: a deterministic composite key such as
    # "{entity_id}:{period_label}".  For absences: a descriptive scope string.
    source_id: str
    # Describes the record's role relative to the finding (e.g. "triggering_alert",
    # "missing_escalation", "peer_observation", "baseline_investigation").
    role: str
    # Optional reporting-period label ("YYYY-MM") anchoring the record to a
    # specific assessment window.  None when the record is not period-scoped.
    period_label: str | None = None
    # Concise human-readable explanation of why this record supports the finding.
    # Maximum ~200 chars; free of sensitive data or raw record contents.
    reason: str


# ---------------------------------------------------------------------------
# Finding evidence container
# ---------------------------------------------------------------------------

class FindingEvidence(BaseModel):
    """Structured evidence and confidence annotation for one finding.

    A ``FindingEvidence`` is keyed by the finding's stable ``finding_key`` and
    is stored in a parallel mapping (``AnnotatedResult.evidence_map``) rather
    than embedded in the frozen finding model.

    Evidence refs are stored in deterministic order (sorted by
    ``(source_type, source_id)`` after construction) and deduplicated by
    ``(source_type, source_id)`` to prevent accidental duplicate references.

    The ``confidence`` field represents data sufficiency, not risk or severity.
    ``confidence_factors`` lists the specific factors that led to this confidence
    level, ordered from most significant to least.  At least one factor must be
    present whenever confidence is not ``HIGH``.
    """

    model_config = ConfigDict(frozen=True)

    # Matches the finding's finding_key exactly.
    finding_key: str
    # Matches the analytic_id of the finding (e.g. "EG-001", "AN-001", "PB-001").
    analytic_id: str

    # Ordered, deduplicated evidence references.
    evidence_refs: tuple[EvidenceRef, ...] = Field(default_factory=tuple)

    # Data-quality confidence.
    confidence: EvidenceConfidence

    # Ordered list of confidence factors (most significant first).
    # Empty only when confidence is HIGH with no limiting factors.
    confidence_factors: tuple[ConfidenceFactor, ...] = Field(default_factory=tuple)

    # One-line note explaining the confidence level (for human reviewers).
    # Must be neutral and must not assert intent, blame, or risk severity.
    confidence_note: str = ""

    @property
    def evidence_count(self) -> int:
        return len(self.evidence_refs)

    @property
    def has_absence_evidence(self) -> bool:
        """True when at least one evidence reference represents an absence."""
        return any(
            r.source_type == EvidenceSourceType.ABSENCE
            for r in self.evidence_refs
        )


def make_finding_evidence(
    finding_key: str,
    analytic_id: str,
    refs: list[EvidenceRef],
    confidence: EvidenceConfidence,
    factors: list[ConfidenceFactor],
    confidence_note: str = "",
) -> FindingEvidence:
    """Construct a ``FindingEvidence``, deduplicating and sorting refs.

    Deduplication key: ``(source_type, source_id)``.  When duplicates exist,
    the first occurrence (in input order) is retained.  After deduplication,
    refs are sorted by ``(source_type, source_id)`` for stable ordering.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[EvidenceRef] = []
    for ref in refs:
        key = (ref.source_type, ref.source_id)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    unique.sort(key=lambda r: (r.source_type, r.source_id))

    # Deduplicate factors while preserving order.
    seen_f: set[str] = set()
    unique_f: list[ConfidenceFactor] = []
    for f in factors:
        if f not in seen_f:
            seen_f.add(f)
            unique_f.append(f)

    return FindingEvidence(
        finding_key=finding_key,
        analytic_id=analytic_id,
        evidence_refs=tuple(unique),
        confidence=confidence,
        confidence_factors=tuple(unique_f),
        confidence_note=confidence_note,
    )
