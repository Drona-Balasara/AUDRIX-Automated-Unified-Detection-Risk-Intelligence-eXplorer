"""Deterministic confidence aggregation rules.

Translates specific per-finding data-quality factors into a categorical
``EvidenceConfidence`` level (HIGH / MODERATE / LOW) using transparent,
explainable rules.

Design rationale
----------------
NIST SP 800-55 Vol. 1 & 2 (December 2024) distinguish between implementation,
effectiveness, and impact measures and acknowledge that all measurements carry
inherent data-quality limitations and uncertainty.  Rather than assigning a
pseudo-precise probability (e.g. "87.4% confidence"), SAT-SA uses a three-level
categorical scale with explicit factor labels so a human reviewer can understand
*why* confidence is limited, not just *that* it is.

Aggregation rule (weakest-factor wins)
---------------------------------------
1. Start at HIGH.
2. If any MODERATE-limiting factor is present → floor to MODERATE.
3. If any LOW-limiting factor is present → floor to LOW.
4. If no evidence refs exist → LOW (no supporting data).

Limiting factors by level:

LOW limiters (any one → LOW):
    FRAGILE_BASELINE, SMALL_BASELINE, MINIMUM_THRESHOLD, ABSENCE_BASED
    (when it is the *only* evidence type), INSUFFICIENT_PERIODS

MODERATE limiters (any one → MODERATE, unless a LOW limiter is also present):
    SINGLE_RECORD, DROPPED_OBSERVATIONS, PARTIAL_OBSERVATIONS,
    MODERATE_BASELINE

Note: ``ABSENCE_BASED`` lowers to MODERATE (not LOW) when direct records are
also present.  It lowers to LOW only when absence is the sole evidence.
"""

from __future__ import annotations

from app.analytics.evidence.model import (
    ConfidenceFactor,
    EvidenceConfidence,
    EvidenceRef,
    EvidenceSourceType,
)

# Factors that immediately floor confidence to LOW (strongest limiters).
_LOW_FACTORS: frozenset[ConfidenceFactor] = frozenset({
    ConfidenceFactor.FRAGILE_BASELINE,
    ConfidenceFactor.SMALL_BASELINE,
    ConfidenceFactor.MINIMUM_THRESHOLD,
    ConfidenceFactor.INSUFFICIENT_PERIODS,
})

# Factors that floor confidence to MODERATE (weaker limiters).
_MODERATE_FACTORS: frozenset[ConfidenceFactor] = frozenset({
    ConfidenceFactor.SINGLE_RECORD,
    ConfidenceFactor.DROPPED_OBSERVATIONS,
    ConfidenceFactor.PARTIAL_OBSERVATIONS,
    ConfidenceFactor.MODERATE_BASELINE,
})


def aggregate_confidence(
    factors: list[ConfidenceFactor],
    refs: list[EvidenceRef],
) -> EvidenceConfidence:
    """Determine the overall confidence level from the provided factors and refs.

    Rules applied in order:
    1. No refs → LOW.
    2. Only ABSENCE refs → LOW (absence-based with no direct records).
    3. Any LOW factor present → LOW.
    4. ABSENCE_BASED factor with direct records also present → MODERATE.
    5. Any MODERATE factor present → MODERATE.
    6. Otherwise → HIGH.
    """
    if not refs:
        return EvidenceConfidence.LOW

    # Check if all refs are absence-based.
    non_absence = [r for r in refs if r.source_type != EvidenceSourceType.ABSENCE]
    if not non_absence:
        return EvidenceConfidence.LOW

    factor_set = set(factors)

    # Any LOW limiter → LOW.
    if factor_set & _LOW_FACTORS:
        return EvidenceConfidence.LOW

    # ABSENCE_BASED with direct records also present → at most MODERATE.
    if ConfidenceFactor.ABSENCE_BASED in factor_set:
        return EvidenceConfidence.MODERATE

    # Any MODERATE limiter → MODERATE.
    if factor_set & _MODERATE_FACTORS:
        return EvidenceConfidence.MODERATE

    return EvidenceConfidence.HIGH


def factors_from_counts(
    *,
    direct_record_count: int,
    baseline_count: int = 0,
    period_count: int = 0,
    has_dropped: bool = False,
    has_absence: bool = False,
    fragile_baseline: bool = False,
    at_minimum_threshold: bool = False,
    insufficient_periods: bool = False,
    min_adequate_baseline: int = 3,
    min_large_comparison: int = 6,
    min_adequate_periods: int = 4,
) -> list[ConfidenceFactor]:
    """Build a factor list from common per-finding statistics.

    This helper covers the most frequent patterns.  Callers can add extra
    factors to the returned list before passing to ``aggregate_confidence()``.

    Parameters
    ----------
    direct_record_count:
        Number of direct source records (ALERT, INVESTIGATION, TELEMETRY_RECORD,
        etc.) cited as evidence.  Does not count ABSENCE or computed refs.
    baseline_count:
        Number of observations used to derive a comparison baseline (for
        deviation/benchmarking analytics).
    period_count:
        Number of reporting periods covered by the finding's observation window.
    has_dropped:
        True when some observations were dropped (incomplete records excluded).
    has_absence:
        True when at least one evidence reference is an ABSENCE type.
    fragile_baseline:
        True when the baseline is explicitly documented as fragile (e.g. Phase 8
        IF-DEV-002 with minimal prior investigations).
    at_minimum_threshold:
        True when the finding is at the exact minimum detection threshold.
    insufficient_periods:
        True when the observation window is shorter than the recommended minimum.
    min_adequate_baseline:
        Minimum baseline count to avoid SMALL_BASELINE factor.
    min_large_comparison:
        Minimum count for LARGE_COMPARISON_POP factor.
    min_adequate_periods:
        Minimum period count to avoid INSUFFICIENT_PERIODS factor.
    """
    factors: list[ConfidenceFactor] = []

    # Record count factors.
    if direct_record_count >= 3:
        factors.append(ConfidenceFactor.MULTIPLE_DIRECT_RECORDS)
    elif direct_record_count == 1:
        factors.append(ConfidenceFactor.SINGLE_RECORD)
    elif direct_record_count >= 1:
        factors.append(ConfidenceFactor.ADEQUATE_RECORD_COUNT)

    # Baseline factors.
    if baseline_count > 0:
        if fragile_baseline or baseline_count < min_adequate_baseline:
            if fragile_baseline:
                factors.append(ConfidenceFactor.FRAGILE_BASELINE)
            else:
                factors.append(ConfidenceFactor.SMALL_BASELINE)
        elif baseline_count >= min_large_comparison:
            factors.append(ConfidenceFactor.LARGE_COMPARISON_POP)
        else:
            factors.append(ConfidenceFactor.MODERATE_BASELINE)

    # Period count factors.
    if period_count > 0:
        if insufficient_periods or period_count < min_adequate_periods:
            factors.append(ConfidenceFactor.INSUFFICIENT_PERIODS)

    # Data-quality modifiers.
    if has_dropped:
        factors.append(ConfidenceFactor.DROPPED_OBSERVATIONS)
    if has_absence:
        factors.append(ConfidenceFactor.ABSENCE_BASED)
    if at_minimum_threshold:
        factors.append(ConfidenceFactor.MINIMUM_THRESHOLD)

    return factors


def note_from_confidence(
    confidence: EvidenceConfidence,
    factors: list[ConfidenceFactor],
    direct_count: int,
) -> str:
    """Generate a short human-readable confidence note.

    The note is neutral: it describes data sufficiency and does not make a risk
    or severity claim.
    """
    if confidence == EvidenceConfidence.HIGH:
        return (
            f"{direct_count} direct source record(s) cited; "
            "no significant data-quality limitations identified."
        )
    limiting = [f.value for f in factors if f in _LOW_FACTORS | _MODERATE_FACTORS]
    if not limiting:
        limiting_txt = "minor data-quality considerations"
    else:
        limiting_txt = "; ".join(limiting[:3]).replace("_", " ").lower()
    if confidence == EvidenceConfidence.MODERATE:
        return (
            f"Adequate evidence available but limited by: {limiting_txt}. "
            "Review the evidence references for full context."
        )
    return (
        f"Thin or fragile evidence base: {limiting_txt}. "
        "Treat as a preliminary signal; additional investigation recommended."
    )
