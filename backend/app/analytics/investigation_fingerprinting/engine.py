"""Investigation-fingerprinting detection engine.

Three analytically distinct detectors operate on the loaded context:

IF-REP-001  Repetitive/template-driven workflow
    Within each (entity, reporting-period) window, counts how many eligible
    investigations share the same normalized action-sequence fingerprint.
    If the dominant fingerprint accounts for at least ``repetition_threshold``
    investigations AND at least ``repetition_rate_threshold`` of the window's
    eligible investigations, a finding is emitted.

IF-DEV-002  Sequence deviation
    For each investigation, compares its fingerprint against the entity's
    plurality-baseline fingerprint derived from investigations in earlier
    or same-period investigations (no future leakage).  If the normalized
    Levenshtein distance exceeds ``deviation_threshold`` and the baseline
    is supported by at least ``min_baseline_investigations`` cases, a
    finding is emitted.

IF-MEA-003  Missing expected action
    For closed investigations on HIGH or CRITICAL severity alerts, checks
    whether at least one of the configured ``expected_actions_for_closed_high_crit``
    action types (``VALIDATE`` or ``EVIDENCE_REVIEW``) is present.
    Justified by the domain's ``_FULL_WORKFLOW`` constant and NIST SP 800-61
    Rev. 3 (April 2025) emphasis on structured analysis and validation steps.

Determinism
-----------
All three detectors iterate entities in sorted order (``ctx.entity_ids()``).
Within each entity, investigations are processed in the order they appear in
``ctx.records`` (sorted by entity_id, period_start, investigation_id).
Fingerprint comparisons and plurality-baseline derivation are stable under
any permutation of the input because they operate on sorted / counted
structures.  No wall-clock time is read; no random state exists.

Read-only guarantee
-------------------
The engine reads only from ``ctx``; it never writes, flushes, or mutates
ORM objects or session state.

Future-leakage prevention
-------------------------
The plurality baseline for IF-DEV-002 is derived from investigations in the
same or earlier reporting periods.  An investigation is never compared
against a baseline that includes investigations from later periods.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterator

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.investigation_fingerprinting.config import FingerprintConfig
from app.analytics.investigation_fingerprinting.context import (
    FingerprintContext,
    InvestigationRecord,
)
from app.analytics.investigation_fingerprinting.findings import (
    DEVIATION_ANALYTIC_ID,
    MISSING_ACTION_ANALYTIC_ID,
    REPETITIVE_ANALYTIC_ID,
    AnyFingerprintFinding,
    FingerprintConfidence,
    MissingExpectedActionFinding,
    RepetitiveWorkflowFinding,
    SequenceDeviationFinding,
    _fingerprint_hash,
)
from app.analytics.investigation_fingerprinting.sequence import normalized_distance

# Severity ordering (subset used by missing-expected-action rule).
_SEVERITY_RANK: dict[str, int] = {
    "LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3
}


class FingerprintResult(BaseModel):
    """Structured, deterministic outcome of one fingerprinting run."""

    model_config = ConfigDict(frozen=True)

    entity_ids: tuple[str, ...] = Field(default_factory=tuple)
    total_investigations_assessed: int = 0
    repetitive_findings: tuple[RepetitiveWorkflowFinding, ...] = Field(
        default_factory=tuple
    )
    deviation_findings: tuple[SequenceDeviationFinding, ...] = Field(
        default_factory=tuple
    )
    missing_action_findings: tuple[MissingExpectedActionFinding, ...] = Field(
        default_factory=tuple
    )

    @property
    def finding_count(self) -> int:
        return (
            len(self.repetitive_findings)
            + len(self.deviation_findings)
            + len(self.missing_action_findings)
        )

    @property
    def all_findings(self) -> tuple[AnyFingerprintFinding, ...]:
        return (
            self.repetitive_findings
            + self.deviation_findings
            + self.missing_action_findings
        )


# ---------------------------------------------------------------------------
# Helper: group records by (entity_id, period_start)
# ---------------------------------------------------------------------------

def _group_by_entity_period(
    records: list[InvestigationRecord],
) -> dict[tuple[str, object], list[InvestigationRecord]]:
    """Group investigation records by (entity_id, period_start)."""
    groups: dict[tuple[str, object], list[InvestigationRecord]] = {}
    for rec in records:
        key = (rec.entity_id, rec.period_start)
        groups.setdefault(key, []).append(rec)
    return groups


def _eligible(rec: InvestigationRecord, min_len: int) -> bool:
    """Return True if this investigation can participate in sequence comparison."""
    return rec.action_count >= min_len


# ---------------------------------------------------------------------------
# IF-REP-001: Repetitive / template-driven workflow
# ---------------------------------------------------------------------------

def _repetitive_confidence(
    matching: int,
    total: int,
    rate: float,
    threshold: int,
) -> FingerprintConfidence:
    if matching >= threshold * 2 and rate >= 0.80:
        return FingerprintConfidence.HIGH
    if matching >= threshold and rate >= 0.50:
        return FingerprintConfidence.MODERATE
    return FingerprintConfidence.LOW


def _detect_repetitive(
    ctx: FingerprintContext,
    config: FingerprintConfig,
) -> list[RepetitiveWorkflowFinding]:
    findings: list[RepetitiveWorkflowFinding] = []
    groups = _group_by_entity_period(ctx.records)

    for (entity_id, period_start), recs in sorted(
        groups.items(), key=lambda kv: kv[0]
    ):
        eligible = [r for r in recs if _eligible(r, config.min_sequence_length)]
        if len(eligible) < config.min_comparable_investigations:
            continue

        # Count fingerprints.
        counter: Counter[tuple[str, ...]] = Counter(
            r.fingerprint for r in eligible
        )
        dominant_fp, dominant_count = counter.most_common(1)[0]
        total = len(eligible)
        rate = dominant_count / total

        if dominant_count >= config.repetition_threshold:
            matching_ids = tuple(
                sorted(
                    r.investigation_id
                    for r in eligible
                    if r.fingerprint == dominant_fp
                )
            )
            period_end = recs[0].period_end
            period_label = period_start.strftime("%Y-%m")  # type: ignore[union-attr]
            fp_hash = _fingerprint_hash(dominant_fp)
            finding_key = (
                f"{REPETITIVE_ANALYTIC_ID}:{entity_id}:{period_label}:{fp_hash}"
            )
            confidence = _repetitive_confidence(
                dominant_count, total, rate, config.repetition_threshold
            )
            summary = (
                f"Potential Template-Driven Investigation Pattern: entity "
                f"{entity_id} had {dominant_count} of {total} eligible "
                f"investigations ({rate:.0%}) in reporting period "
                f"{period_label} sharing the identical action sequence "
                f"{list(dominant_fp)}. This pattern may reflect mechanical "
                f"workflow execution rather than alert-driven triage. "
                f"Supervisory review is recommended; this finding does not "
                f"assert intent or fault on any individual or team."
            )
            findings.append(
                RepetitiveWorkflowFinding(
                    finding_key=finding_key,
                    entity_id=entity_id,
                    window_start=period_start,  # type: ignore[arg-type]
                    window_end=period_end,
                    period_label=period_label,
                    dominant_fingerprint=dominant_fp,
                    matching_investigation_count=dominant_count,
                    window_investigation_count=total,
                    repetition_rate=rate,
                    matching_investigation_ids=matching_ids,
                    confidence=confidence,
                    summary=summary,
                )
            )

    findings.sort(key=lambda f: f.finding_key)
    return findings


# ---------------------------------------------------------------------------
# IF-DEV-002: Sequence deviation
# ---------------------------------------------------------------------------

def _plurality_baseline(
    prior_recs: list[InvestigationRecord],
    min_baseline: int,
) -> tuple[tuple[str, ...], int] | None:
    """Derive the plurality-baseline fingerprint from prior investigations.

    Returns ``(fingerprint, count)`` of the most common fingerprint, or
    ``None`` when fewer than ``min_baseline`` eligible records are available.
    """
    if len(prior_recs) < min_baseline:
        return None
    counter: Counter[tuple[str, ...]] = Counter(r.fingerprint for r in prior_recs)
    dominant, count = counter.most_common(1)[0]
    return dominant, count


def _deviation_confidence(
    distance: float,
    threshold: float,
    baseline_count: int,
) -> FingerprintConfidence:
    if distance >= threshold + 0.20 and baseline_count >= 6:
        return FingerprintConfidence.HIGH
    if distance >= threshold and baseline_count >= 3:
        return FingerprintConfidence.MODERATE
    return FingerprintConfidence.LOW


def _detect_deviation(
    ctx: FingerprintContext,
    config: FingerprintConfig,
) -> list[SequenceDeviationFinding]:
    """Compare each investigation against its entity's plurality baseline.

    Future-leakage prevention: the baseline for an investigation in period P
    is derived from all eligible investigations of the same entity in periods
    strictly up to and including P (i.e. period_start <= P).
    """
    findings: list[SequenceDeviationFinding] = []
    seen_keys: set[str] = set()

    # Build a per-entity list of all records, sorted by period then inv_id.
    entity_records: dict[str, list[InvestigationRecord]] = {}
    for rec in ctx.records:
        entity_records.setdefault(rec.entity_id, []).append(rec)

    for entity_id in sorted(entity_records):
        recs = entity_records[entity_id]
        # recs is already sorted (entity_id, period_start, investigation_id)

        for i, rec in enumerate(recs):
            if not _eligible(rec, config.min_sequence_length):
                continue

            # Prior baseline: all eligible investigations at or before this period.
            prior = [
                r for r in recs[:i + 1]
                if (
                    r.investigation_id != rec.investigation_id
                    and _eligible(r, config.min_sequence_length)
                    and r.period_start <= rec.period_start
                )
            ]

            result = _plurality_baseline(prior, config.min_baseline_investigations)
            if result is None:
                continue

            baseline_fp, baseline_count = result
            dist = normalized_distance(rec.fingerprint, baseline_fp)

            if dist >= config.deviation_threshold:
                finding_key = f"{DEVIATION_ANALYTIC_ID}:{rec.investigation_id}"
                if finding_key in seen_keys:
                    continue
                seen_keys.add(finding_key)
                confidence = _deviation_confidence(
                    dist, config.deviation_threshold, baseline_count
                )
                summary = (
                    f"Potential Investigation Sequence Deviation: investigation "
                    f"{rec.investigation_id} (entity {entity_id}, period "
                    f"{rec.period_start.strftime('%Y-%m')}) has an action "
                    f"sequence {list(rec.fingerprint)} that differs from the "
                    f"entity's typical pattern {list(baseline_fp)} by a "
                    f"normalized edit distance of {dist:.2f} (threshold "
                    f"{config.deviation_threshold:.2f}, baseline derived from "
                    f"{baseline_count} prior investigations). Supervisory review "
                    f"is recommended."
                )
                findings.append(
                    SequenceDeviationFinding(
                        finding_key=finding_key,
                        entity_id=entity_id,
                        investigation_id=rec.investigation_id,
                        period_start=rec.period_start,
                        period_end=rec.period_end,
                        observed_fingerprint=rec.fingerprint,
                        baseline_fingerprint=baseline_fp,
                        normalized_distance=dist,
                        baseline_investigation_count=baseline_count,
                        confidence=confidence,
                        summary=summary,
                    )
                )

    findings.sort(key=lambda f: f.finding_key)
    return findings


# ---------------------------------------------------------------------------
# IF-MEA-003: Missing expected action
# ---------------------------------------------------------------------------

_CLOSED = "CLOSED"


def _mea_confidence(action_count: int) -> FingerprintConfidence:
    # For a closed HIGH/CRITICAL investigation with no VALIDATE or
    # EVIDENCE_REVIEW, confidence is HIGH when many actions were taken
    # (ruling out an incomplete record) and LOW for a very short sequence.
    if action_count >= 6:
        return FingerprintConfidence.HIGH
    if action_count >= 3:
        return FingerprintConfidence.MODERATE
    return FingerprintConfidence.LOW


def _detect_missing_action(
    ctx: FingerprintContext,
    config: FingerprintConfig,
) -> list[MissingExpectedActionFinding]:
    findings: list[MissingExpectedActionFinding] = []
    min_sev_rank = _SEVERITY_RANK.get(config.missing_action_min_severity, 2)

    for rec in ctx.records:
        if rec.status != _CLOSED:
            continue
        sev_rank = _SEVERITY_RANK.get(rec.alert_severity, -1)
        if sev_rank < min_sev_rank:
            continue

        # Check for presence of at least one expected action.
        present = set(rec.fingerprint)
        # Flag only when NONE of the expected actions are present.
        if present & config.expected_actions_for_closed_high_crit:
            continue

        missing = config.expected_actions_for_closed_high_crit
        finding_key = f"{MISSING_ACTION_ANALYTIC_ID}:{rec.investigation_id}"
        confidence = _mea_confidence(rec.action_count)
        missing_sorted = tuple(sorted(missing))
        summary = (
            f"Potential Missing Investigation Action: closed investigation "
            f"{rec.investigation_id} on a {rec.alert_severity} severity alert "
            f"(entity {rec.entity_id}, period "
            f"{rec.period_start.strftime('%Y-%m')}) does not include any of "
            f"the expected completeness actions {sorted(missing)}. "
            f"Observed sequence: {list(rec.fingerprint)}. "
            f"This finding does not assert fault; other records may "
            f"provide context not captured in the action log."
        )
        findings.append(
            MissingExpectedActionFinding(
                finding_key=finding_key,
                entity_id=rec.entity_id,
                investigation_id=rec.investigation_id,
                alert_id=rec.alert_id,
                period_start=rec.period_start,
                period_end=rec.period_end,
                observed_fingerprint=rec.fingerprint,
                missing_action_types=missing_sorted,
                alert_severity=rec.alert_severity,
                confidence=confidence,
                summary=summary,
            )
        )

    findings.sort(key=lambda f: f.finding_key)
    return findings


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------

def run_fingerprint_detection(
    ctx: FingerprintContext,
    config: FingerprintConfig,
) -> FingerprintResult:
    """Run all three fingerprinting detectors.

    Pure and side-effect free.  Identical context and config yield an equal
    result.  No wall-clock time is read.
    """
    total = len(ctx.records)

    repetitive = _detect_repetitive(ctx, config)
    deviation  = _detect_deviation(ctx, config)
    missing    = _detect_missing_action(ctx, config)

    return FingerprintResult(
        entity_ids=tuple(ctx.entity_ids()),
        total_investigations_assessed=total,
        repetitive_findings=tuple(repetitive),
        deviation_findings=tuple(deviation),
        missing_action_findings=tuple(missing),
    )
