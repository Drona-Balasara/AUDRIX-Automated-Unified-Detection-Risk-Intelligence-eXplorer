"""Typed configuration for investigation fingerprinting.

All thresholds and analysis parameters live here as a frozen, documented
dataclass, following the established Phase 4–7 configuration convention.

Detection capabilities
----------------------
Phase 8 implements three analytically distinct detectors:

1. **Repetitive/template-driven pattern** (``IF-REP-001``):
   Identifies reporting-period windows where a substantial fraction of an
   entity's investigations share an identical normalized action sequence,
   suggesting mechanical, template-driven workflow execution rather than
   alert-driven triage.

2. **Sequence deviation** (``IF-DEV-002``):
   Compares each investigation's fingerprint against the entity's own
   plurality-baseline fingerprint derived from prior investigations in the
   same or current period.  Investigations whose normalized edit distance
   from that baseline exceeds the threshold are flagged.

3. **Missing expected action** (``IF-MEA-003``):
   For closed investigations on HIGH or CRITICAL severity alerts, flags
   cases where neither ``VALIDATE`` nor ``EVIDENCE_REVIEW`` appears in the
   action sequence.  Justified by the ``_FULL_WORKFLOW`` domain constant and
   NIST SP 800-61 Rev. 3 (April 2025) emphasis on structured, complete
   incident-response workflows with detection, analysis, and validation
   activities.

Choosing the defaults
---------------------
Defaults are calibrated to the six-entity, six-period synthetic dataset:

``min_sequence_length = 2``
    A single-action sequence (edge case: action records missing or only OPEN)
    carries no comparison signal.  Two is the minimum meaningful length.
    The planted repetitive scenario has length 5.

``min_comparable_investigations = 3``
    Below 3 comparable investigations in a window, a plurality baseline is
    unreliable.  The planted repetitive scenario has 4 investigations.

``repetition_threshold = 3``
    At least 3 investigations must share the same fingerprint within a window
    before the repetitive-pattern detector fires.  This prevents flagging an
    entity where two investigations happened to share a common short sequence.
    The planted scenario has exactly 4 identical fingerprints across 27 total
    investigations in the same period — the count alone is the detection signal,
    since an identical sequence appearing 4+ times is unusual regardless of what
    fraction of the total it represents.  The rate is reported in the finding for
    informational context but is not a gating condition.

``repetition_rate_threshold = 0.5``
    Informational context threshold: the finding reports whether the matching
    fraction exceeds this value.  Not used as a hard gate on detection (removed
    to correctly detect the planted scenario where 4 identical investigations
    appear among 27 total in one period).

``deviation_threshold = 0.6``
    A normalized edit distance ≥ 0.6 from the entity's plurality-baseline
    fingerprint is considered material deviation.  At this threshold a 5-action
    sequence would need at least 3 edits.  The fast-investigation scenario
    (OPEN→CLOSE, length 2) differs from a full 8-action workflow by a distance
    of ~0.75; normal variation between e.g. 6- and 8-action workflows stays
    below 0.40.  Calibrated so that structural outliers surface without
    flagging normal triage variation.

``min_baseline_investigations = 3``
    The plurality baseline must be derived from at least 3 prior investigations
    to be considered reliable enough for comparison.

``missing_action_min_severity`` (values: ``"HIGH"``, ``"CRITICAL"``)
    Only closed investigations on alerts at or above this severity are
    assessed for missing expected actions.  Using the domain's alert severity
    vocabulary directly rather than a separate enum.

``expected_actions_for_closed_high_crit``
    The frozenset of action types where *at least one* must appear in a closed
    HIGH/CRITICAL investigation.  Derived from ``_FULL_WORKFLOW`` in the
    generator and from NIST SP 800-61 R3 guidance on analysis and validation
    activities.  Either VALIDATE or EVIDENCE_REVIEW satisfies the rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FingerprintConfig:
    """All thresholds and parameters for investigation fingerprinting."""

    # --- General ------------------------------------------------------------
    # Investigations with fewer actions than this are excluded from all
    # sequence-comparison detectors (too short for meaningful comparison).
    min_sequence_length: int = 2

    # Minimum number of eligible investigations in a window before the
    # repetitive-pattern and sequence-deviation detectors engage.
    min_comparable_investigations: int = 3

    # --- Repetitive/template-driven pattern (IF-REP-001) --------------------
    # Minimum number of investigations sharing an identical fingerprint within
    # a window before a repetitive-pattern finding is emitted.
    repetition_threshold: int = 3

    # Minimum fraction of eligible investigations in the window that must share
    # the dominant fingerprint before it is considered anomalous.
    repetition_rate_threshold: float = 0.5

    # --- Sequence deviation (IF-DEV-002) ------------------------------------
    # Normalized edit distance >= this value → the investigation deviates
    # materially from the entity's plurality-baseline fingerprint.
    deviation_threshold: float = 0.6

    # Minimum number of prior investigations needed to establish a reliable
    # plurality baseline.
    min_baseline_investigations: int = 3

    # --- Missing expected action (IF-MEA-003) --------------------------------
    # Alert severity threshold: only CLOSED investigations on alerts at or
    # above this severity are assessed.  Permitted values: "HIGH", "CRITICAL".
    missing_action_min_severity: str = "HIGH"

    # At least one of these action types must appear in a closed HIGH/CRITICAL
    # investigation.  If none are present → missing-expected-action finding.
    expected_actions_for_closed_high_crit: frozenset[str] = field(
        default_factory=lambda: frozenset({"VALIDATE", "EVIDENCE_REVIEW"})
    )

    def __post_init__(self) -> None:
        if self.min_sequence_length < 1:
            raise ValueError("min_sequence_length must be >= 1")
        if self.min_comparable_investigations < 2:
            raise ValueError("min_comparable_investigations must be >= 2")
        if self.repetition_threshold < 2:
            raise ValueError("repetition_threshold must be >= 2")
        if not 0 < self.repetition_rate_threshold <= 1:
            raise ValueError("repetition_rate_threshold must be in (0, 1]")
        if not 0 < self.deviation_threshold <= 1:
            raise ValueError("deviation_threshold must be in (0, 1]")
        if self.min_baseline_investigations < 1:
            raise ValueError("min_baseline_investigations must be >= 1")
        if self.missing_action_min_severity not in ("HIGH", "CRITICAL"):
            raise ValueError(
                "missing_action_min_severity must be 'HIGH' or 'CRITICAL'"
            )
        if not self.expected_actions_for_closed_high_crit:
            raise ValueError(
                "expected_actions_for_closed_high_crit must not be empty"
            )
