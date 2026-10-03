"""Structured investigation-fingerprinting finding schema.

Each finding is a deterministic, strictly observational statement about an
investigation's action sequence.  Findings describe *what was observed* —
the concrete action sequence, its comparison target, and the resulting metric —
never an attribution of intent, negligence, misconduct, or competence.

Finding types
-------------
``REPETITIVE_WORKFLOW``
    A substantial fraction of investigations within an entity-period window
    share an identical normalized action sequence, suggesting mechanical or
    template-driven workflow execution.  Neutral title: "Potential
    Template-Driven Investigation Pattern".

``SEQUENCE_DEVIATION``
    An individual investigation's action sequence differs materially from the
    entity's plurality-baseline fingerprint as measured by normalized edit
    distance.  Neutral title: "Potential Investigation Sequence Deviation".

``MISSING_EXPECTED_ACTION``
    A closed investigation on a HIGH or CRITICAL severity alert does not contain
    any of the expected completeness actions (``VALIDATE`` or
    ``EVIDENCE_REVIEW``), which are present in the domain's documented full
    workflow and align with NIST SP 800-61 Rev. 3 emphasis on structured
    analysis and validation activities.  Neutral title: "Potential Missing
    Investigation Action".

Language discipline
-------------------
All three finding types use neutral, observational language.  The system
never accuses an analyst, team, or organisation of deliberate action, laziness,
misconduct, or fraud.  Analyst identifiers are referenced only as opaque
synthetic labels (``ANALYST-001`` style from the domain model) and only when
needed to describe the observed pattern; they carry no blame.

Finding keys
------------
Keys are deterministic strings, stable across repeated runs over identical
data:

- ``IF-REP-001:<entity_id>:<period_label>:<fingerprint_hash>``
- ``IF-DEV-002:<investigation_id>``
- ``IF-MEA-003:<investigation_id>``

where ``period_label`` is ``YYYY-MM`` derived from the window start and
``fingerprint_hash`` is a short, stable hex digest of the dominant fingerprint
tuple (not a security hash; used only for uniqueness).
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StrEnum

# Stable analytic-id prefix for all three Phase 8 detectors.
ANALYTIC_PREFIX = "IF"

REPETITIVE_ANALYTIC_ID  = "IF-REP-001"
DEVIATION_ANALYTIC_ID   = "IF-DEV-002"
MISSING_ACTION_ANALYTIC_ID = "IF-MEA-003"


class FingerprintFindingType(StrEnum):
    """Machine-readable type of an investigation-fingerprinting finding."""

    REPETITIVE_WORKFLOW     = "REPETITIVE_WORKFLOW"
    SEQUENCE_DEVIATION      = "SEQUENCE_DEVIATION"
    MISSING_EXPECTED_ACTION = "MISSING_EXPECTED_ACTION"


class FingerprintConfidence(StrEnum):
    """Data-quality / observational-sufficiency indicator.

    Not a risk or severity score.  Reflects how much evidence supports the
    finding, not how serious the pattern is.
    """

    HIGH     = "HIGH"
    MODERATE = "MODERATE"
    LOW      = "LOW"


def _fingerprint_hash(fingerprint: tuple[str, ...]) -> str:
    """Return a short, stable hex string for use in finding keys.

    Uses SHA-256 of the pipe-joined fingerprint tuple; takes first 8 hex chars.
    Not a security hash; used only for stable key uniqueness.
    """
    raw = "|".join(fingerprint).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:8]


class RepetitiveWorkflowFinding(BaseModel):
    """Entity-period window where investigations share an identical fingerprint.

    Neutral title: "Potential Template-Driven Investigation Pattern".
    """

    model_config = ConfigDict(frozen=True)

    finding_key: str
    analytic_id: str = REPETITIVE_ANALYTIC_ID
    finding_type: FingerprintFindingType = FingerprintFindingType.REPETITIVE_WORKFLOW

    entity_id: str
    # Reporting window: start of the first period used, end of the last.
    window_start: datetime
    window_end: datetime
    period_label: str  # "YYYY-MM" of window_start

    # The fingerprint shared by the cluster.
    dominant_fingerprint: tuple[str, ...] = Field(default_factory=tuple)
    # How many investigations share this fingerprint.
    matching_investigation_count: int
    # Total eligible investigations in the window.
    window_investigation_count: int
    # Fraction: matching / total.
    repetition_rate: float

    # Stable references: investigation IDs that share the dominant fingerprint.
    matching_investigation_ids: tuple[str, ...] = Field(default_factory=tuple)

    confidence: FingerprintConfidence
    summary: str


class SequenceDeviationFinding(BaseModel):
    """One investigation whose sequence materially differs from the entity baseline.

    Neutral title: "Potential Investigation Sequence Deviation".
    """

    model_config = ConfigDict(frozen=True)

    finding_key: str
    analytic_id: str = DEVIATION_ANALYTIC_ID
    finding_type: FingerprintFindingType = FingerprintFindingType.SEQUENCE_DEVIATION

    entity_id: str
    investigation_id: str
    period_start: datetime
    period_end: datetime

    # The investigation's own action sequence.
    observed_fingerprint: tuple[str, ...] = Field(default_factory=tuple)
    # The entity's plurality-baseline fingerprint used for comparison.
    baseline_fingerprint: tuple[str, ...] = Field(default_factory=tuple)
    # Normalized edit distance in [0, 1]; higher = more different.
    normalized_distance: float
    # Number of prior investigations used to establish the baseline.
    baseline_investigation_count: int

    confidence: FingerprintConfidence
    summary: str


class MissingExpectedActionFinding(BaseModel):
    """Closed HIGH/CRITICAL investigation missing a completeness action.

    Neutral title: "Potential Missing Investigation Action".
    """

    model_config = ConfigDict(frozen=True)

    finding_key: str
    analytic_id: str = MISSING_ACTION_ANALYTIC_ID
    finding_type: FingerprintFindingType = FingerprintFindingType.MISSING_EXPECTED_ACTION

    entity_id: str
    investigation_id: str
    alert_id: str
    period_start: datetime
    period_end: datetime

    # The investigation's action sequence (what was found).
    observed_fingerprint: tuple[str, ...] = Field(default_factory=tuple)
    # The action types that were expected but absent.
    missing_action_types: tuple[str, ...] = Field(default_factory=tuple)
    alert_severity: str

    confidence: FingerprintConfidence
    summary: str


# Union type for any fingerprinting finding (used in result container).
AnyFingerprintFinding = (
    RepetitiveWorkflowFinding
    | SequenceDeviationFinding
    | MissingExpectedActionFinding
)
