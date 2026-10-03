"""Metric-risk divergence detection engine.

The engine iterates over entities in the loaded context, computes direction-
adjusted trend scores for the headline and quality metric groups, and emits a
:class:`MetricRiskDivergenceFinding` when the divergence condition is met.

Trend method
------------
For each metric the engine computes a *direction-adjusted* series: values are
negated for metrics where ``higher_is_better=False`` so that every series has
the same sign convention (positive slope → improving).

The trend score for a single metric is the **normalised least-squares slope**:

  1.  Fit a least-squares line through the N (x=period_index, y=adjusted_value)
      points.
  2.  Multiply the raw slope by (N − 1) to convert from per-period rate to a
      total-period change estimate.
  3.  Normalise by the observed value range of the series (max − min).  If the
      range is zero, the series is flat and the score is 0.0.

This produces a dimensionless score in roughly [−1, +1] that is comparable
across metrics with different units (rates vs. hours).

When the series has fewer than 3 points, the least-squares approach is
unreliable (a 2-point fit is exact and says nothing about trend), so the engine
falls back to a first-vs-last relative-change score:

  score = (last − first) / max(abs(first), abs(last), 1e-9)

clamped to [−1, +1].

Composite scoring
-----------------
The composite score for a group is the **mean of the individual metric trend
scores**, excluding metrics whose absolute score falls below
``config.min_trend_magnitude`` (treating them as flat).  If all metrics in a
group are flat, the composite is 0.0 and no divergence is flagged.

Divergence condition
--------------------
A finding is emitted when ALL of the following hold:
  - period_count >= config.min_observation_periods
  - headline_composite >= config.headline_min_improvement
  - quality_composite <= −config.quality_min_deterioration

Determinism
-----------
- Entities are processed in sorted order (entity_ids() returns sorted list).
- Periods are pre-sorted ascending in the context (see context.load_context).
- No wall-clock time is read; no random state exists.
- Identical database content + config always yields an equal result.

Read-only guarantee
-------------------
The engine reads from ``ctx`` only; it never writes, flushes, or mutates any
ORM object or session state.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.metric_risk_divergence.config import MetricRiskDivergenceConfig
from app.analytics.metric_risk_divergence.context import DivergenceContext
from app.analytics.metric_risk_divergence.findings import (
    ANALYTIC_ID,
    ConfidenceLevel,
    MetricRiskDivergenceFinding,
    MetricSnapshot,
)
from app.analytics.metric_risk_divergence.metrics import (
    HEADLINE_METRICS,
    METRIC_REGISTRY,
    QUALITY_METRICS,
    MetricGroup,
    MetricSpec,
)


class MetricRiskDivergenceResult(BaseModel):
    """Structured, deterministic outcome of one divergence-detection run."""

    model_config = ConfigDict(frozen=True)

    analytic_id: str = ANALYTIC_ID
    entity_ids: tuple[str, ...] = Field(default_factory=tuple)
    # Entities that had too few periods to assess.
    skipped_entity_ids: tuple[str, ...] = Field(default_factory=tuple)
    findings: tuple[MetricRiskDivergenceFinding, ...] = Field(default_factory=tuple)

    @property
    def finding_count(self) -> int:
        return len(self.findings)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _direction_adjusted(value: float, spec: MetricSpec) -> float:
    """Return ``value`` negated when lower is better, so + always means better."""
    return value if spec.higher_is_better else -value


def _normalised_slope(series: list[float]) -> float:
    """Dimensionless trend score for an already direction-adjusted series.

    Returns a score in roughly [−1, +1].  0.0 for flat or constant series.
    Uses least-squares slope for n >= 3, first-vs-last relative change for n == 2.
    Never called with n < 2 (callers gate on min_observation_periods >= 2).
    """
    n = len(series)
    if n < 2:
        return 0.0

    value_range = max(series) - min(series)

    if n == 2:
        # First-vs-last relative change, clamped to [−1, +1].
        first, last = series[0], series[-1]
        denom = max(abs(first), abs(last), 1e-9)
        raw = (last - first) / denom
        return max(-1.0, min(1.0, raw))

    # Least-squares slope: b = (n*Σxy − Σx*Σy) / (n*Σx² − (Σx)²)
    # x = period index 0..n-1, y = direction-adjusted value.
    xs = list(range(n))
    sum_x = sum(xs)
    sum_y = sum(series)
    sum_xy = sum(x * y for x, y in zip(xs, series))
    sum_x2 = sum(x * x for x in xs)
    denom_ls = n * sum_x2 - sum_x * sum_x
    if denom_ls == 0:
        return 0.0
    slope = (n * sum_xy - sum_x * sum_y) / denom_ls

    # Convert slope (per-period rate) → total-period change estimate.
    total_change = slope * (n - 1)

    if value_range == 0.0:
        # Constant series: no trend regardless of slope.
        return 0.0

    # Normalise by range; clamp to [−1, +1] to handle edge cases with extreme
    # outliers that push the normalised value outside the unit interval.
    return max(-1.0, min(1.0, total_change / value_range))


def _metric_trend_score(
    periods: list,  # list of (start, end, PerformanceMetric) from context
    spec: MetricSpec,
) -> float | None:
    """Compute the trend score for one metric over the entity's periods.

    Returns ``None`` when the metric value is unavailable for all periods.
    The PerformanceMetric schema uses NOT NULL columns, so None only arises
    if a future schema extension makes a field nullable; handled conservatively
    by excluding that period from the series rather than treating None as zero.
    """
    series: list[float] = []
    for _start, _end, pm in periods:
        raw = getattr(pm, spec.name, None)
        if raw is None:
            # Missing value: skip this period rather than zero-fill.
            continue
        series.append(_direction_adjusted(float(raw), spec))

    if len(series) < 2:
        return None
    return _normalised_slope(series)


def _composite_score(
    metric_scores: list[float | None],
    min_magnitude: float,
) -> float:
    """Mean of non-None scores whose absolute value exceeds min_magnitude.

    Flat metrics (|score| < min_magnitude) are excluded to avoid noise
    amplification.  Returns 0.0 when all scores are None or flat.
    """
    contributing = [
        s for s in metric_scores
        if s is not None and abs(s) >= min_magnitude
    ]
    if not contributing:
        return 0.0
    return sum(contributing) / len(contributing)


def _confidence(
    period_count: int,
    headline_score: float,
    quality_score: float,
    config: MetricRiskDivergenceConfig,
) -> tuple[ConfidenceLevel, str]:
    """Derive data-quality confidence level and a one-line rationale note.

    Confidence reflects observational sufficiency, not risk severity.
    - HIGH:     >= 5 periods AND both composites at least 2x threshold.
    - MODERATE: >= 4 periods (default min) AND condition met.
    - LOW:      condition met but fewer periods or marginal magnitudes.
    """
    headline_ratio = headline_score / config.headline_min_improvement
    quality_ratio = abs(quality_score) / config.quality_min_deterioration

    if (
        period_count >= 5
        and headline_ratio >= 2.0
        and quality_ratio >= 2.0
    ):
        level = ConfidenceLevel.HIGH
        note = (
            f"{period_count} reporting periods with both composite signals at "
            f"least 2× the detection threshold (headline ×{headline_ratio:.1f}, "
            f"quality ×{quality_ratio:.1f})."
        )
    elif period_count >= config.min_observation_periods and min(headline_ratio, quality_ratio) >= 1.0:
        level = ConfidenceLevel.MODERATE
        note = (
            f"{period_count} reporting periods; both composites exceed their "
            f"thresholds (headline ×{headline_ratio:.1f}, quality ×{quality_ratio:.1f}). "
            "Additional periods would strengthen the signal."
        )
    else:
        level = ConfidenceLevel.LOW
        note = (
            f"Only {period_count} reporting periods available; trend estimates "
            "have wide uncertainty at this sample size.  Treat as a preliminary "
            "signal requiring further investigation."
        )
    return level, note


def _build_snapshots(
    periods: list,
    metric_specs: tuple[MetricSpec, ...],
    min_magnitude: float,
) -> tuple[tuple[MetricSnapshot, ...], list[float | None]]:
    """Build per-metric snapshots and return their trend scores.

    Returns a tuple of (snapshots, trend_scores) so the caller can use both
    the structured snapshot objects and the raw scores for composite computation
    without re-running the trend calculation.
    """
    snapshots: list[MetricSnapshot] = []
    trend_scores: list[float | None] = []

    for spec in metric_specs:
        period_values: list[tuple[str, float]] = []
        for start, _end, pm in periods:
            raw = getattr(pm, spec.name, None)
            if raw is not None:
                label = start.strftime("%Y-%m")
                period_values.append((label, float(raw)))

        score = _metric_trend_score(periods, spec)
        trend_scores.append(score)

        snapshots.append(
            MetricSnapshot(
                name=spec.name,
                group=spec.group.value,
                higher_is_better=spec.higher_is_better,
                period_values=tuple(period_values),
                trend_score=score,
            )
        )

    return tuple(snapshots), trend_scores


def run_divergence_detection(
    ctx: DivergenceContext,
    config: MetricRiskDivergenceConfig,
) -> MetricRiskDivergenceResult:
    """Detect metric-risk divergence for all entities in ``ctx``.

    Pure and side-effect-free: identical context and config yield an equal
    result.  No wall-clock time is read; no I/O is performed.
    """
    findings: list[MetricRiskDivergenceFinding] = []
    skipped: list[str] = []

    for entity_id in ctx.entity_ids():  # sorted order → deterministic
        epm = ctx.entity_metrics[entity_id]
        periods = epm.periods  # already sorted ascending by period_start

        # --- Insufficient-period guard ---------------------------------------
        if epm.period_count < config.min_observation_periods:
            skipped.append(entity_id)
            continue

        # --- Build per-metric snapshots and trend scores --------------------
        headline_snapshots, headline_scores = _build_snapshots(
            periods, HEADLINE_METRICS, config.min_trend_magnitude
        )
        quality_snapshots, quality_scores = _build_snapshots(
            periods, QUALITY_METRICS, config.min_trend_magnitude
        )

        # --- Composite scores -----------------------------------------------
        headline_composite = _composite_score(headline_scores, config.min_trend_magnitude)
        quality_composite = _composite_score(quality_scores, config.min_trend_magnitude)

        # --- Divergence condition -------------------------------------------
        if not (
            headline_composite >= config.headline_min_improvement
            and quality_composite <= -config.quality_min_deterioration
        ):
            continue

        # --- Build finding --------------------------------------------------
        window_start_dt = periods[0][0]
        window_end_dt = periods[-1][1]
        window_start_label = periods[0][0].strftime("%Y-%m")
        window_end_label = periods[-1][0].strftime("%Y-%m")

        confidence, confidence_note = _confidence(
            epm.period_count, headline_composite, quality_composite, config
        )

        summary = (
            f"Potential Metric-Risk Divergence: entity {entity_id} shows an "
            f"improving headline performance trend (composite score "
            f"{headline_composite:+.3f}) alongside a deteriorating operational-"
            f"quality trend (composite score {quality_composite:+.3f}) across "
            f"{epm.period_count} reporting periods "
            f"({window_start_label} to {window_end_label}). "
            "The observed relationship between reported KPIs and underlying "
            "operational evidence warrants supervisory review. This finding "
            "describes a pattern in the data; it does not assert intent, cause, "
            "or fault on the part of any individual or team."
        )

        finding_key = (
            f"{ANALYTIC_ID}:{entity_id}:{window_start_label}:{window_end_label}"
        )

        findings.append(
            MetricRiskDivergenceFinding(
                finding_key=finding_key,
                entity_id=entity_id,
                window_start=window_start_dt,
                window_end=window_end_dt,
                supporting_period_count=epm.period_count,
                headline_trend_score=headline_composite,
                quality_trend_score=quality_composite,
                headline_snapshots=headline_snapshots,
                quality_snapshots=quality_snapshots,
                confidence=confidence,
                summary=summary,
                confidence_note=confidence_note,
            )
        )

    findings.sort(key=lambda f: f.finding_key)
    skipped.sort()

    return MetricRiskDivergenceResult(
        entity_ids=tuple(ctx.entity_ids()),
        skipped_entity_ids=tuple(skipped),
        findings=tuple(findings),
    )
