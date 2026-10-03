# Metric-Risk Divergence Detection

Phase 7 introduces **metric-risk divergence detection**: a deterministic,
read-only analytic that identifies reporting periods where an entity's headline
SOC performance metrics show an improving trend while its underlying
operational-quality indicators materially deteriorate — a divergence pattern
that warrants human supervisory review.

## What this analytic does — and does not — claim

This analytic identifies a *pattern in the observed data*. It does not assert
intent, cause, fraud, manipulation, or fault on the part of any individual,
analyst, manager, or organisation. The finding type is called
**"Potential Metric-Risk Divergence"** — the word "potential" is deliberate
and must be preserved in any presentation layer. Interpretation requires
contextual human judgement that is outside the scope of this analytic.

## Analytical framework

Consistent with **NIST SP 800-55 Vol. 2** (December 2024), which distinguishes
output/efficiency metrics (what the SOC reports upward) from effectiveness and
quality indicators (how sound the operational work actually was), the detector
separates `PerformanceMetric` fields into two groups.

### Headline KPIs

Metrics a SOC typically surfaces in management reports. A positive trend in
these is *apparent improvement* — which becomes suspicious only when it
diverges from the quality group.

| Field | Direction | Rationale |
|---|---|---|
| `closure_rate` | Higher = better | Share of investigations closed |
| `sla_compliance` | Higher = better | Share of work meeting SLA |
| `mttr_hours` | Lower = better | Mean time to resolve (inverted before scoring) |

### Operational-quality indicators

Metrics that reflect the depth, coverage, and completeness of the actual
work performed. These are the indicators whose deterioration is the second
half of a divergence signal.

| Field | Direction | Rationale |
|---|---|---|
| `investigation_completeness` | Higher = better | Share of investigations with a complete workflow |
| `evidence_completeness` | Higher = better | Average evidence coverage per investigation |
| `remediation_rate` | Higher = better | Share of true-positive alerts with completed remediation |
| `recurrence_rate` | Lower = better | Share of recurring alerts (inverted before scoring) |

### Why `escalation_rate` is excluded

`escalation_rate` is absent from both groups. Its directional meaning is
context-dependent: elevated escalation can indicate appropriate risk-management
or it can indicate operational overload; suppressed escalation can indicate
mature triage or missed incidents. Including it in either composite would
introduce ambiguity that a deterministic trend detector cannot resolve.

## Trend method

### Direction adjustment

For metrics where lower is better (`mttr_hours`, `recurrence_rate`) the engine
negates the raw value before fitting, so every series has a consistent sign
convention: a positive slope always means *improving*.

### Normalised least-squares slope

For each direction-adjusted series with **n ≥ 3** points the engine fits an
ordinary least-squares line and computes a dimensionless score:

```
raw_slope = OLS slope (per-period rate of change)
total_change = raw_slope × (n − 1)
score = total_change / (max_value − min_value)      [clamped to −1 … +1]
```

This produces a score roughly in [−1, +1] that is comparable across metrics
with different units (rates vs. hours).

When **n = 2** (or when `min_observation_periods` is lowered to 2), the engine
uses a simpler first-vs-last relative-change score instead:

```
score = (last − first) / max(|first|, |last|, ε)   [clamped to −1 … +1]
```

### Composite score

The composite score for a group is the **mean of the individual metric scores**
after excluding metrics whose absolute score falls below `min_trend_magnitude`
(treating them as flat). If all metrics in a group are flat the composite is
0.0 and no divergence is flagged.

### Divergence condition

A finding is emitted when **all three** conditions hold:

1. The entity has at least `min_observation_periods` reporting periods.
2. `headline_composite ≥ headline_min_improvement`
3. `quality_composite ≤ −quality_min_deterioration`

## Thresholds and configuration

All parameters live in `app/analytics/metric_risk_divergence/config.py`.

| Parameter | Default | Purpose |
|---|---|---|
| `min_observation_periods` | 4 | Minimum periods before assessing |
| `headline_min_improvement` | 0.20 | Minimum headline composite score to flag |
| `quality_min_deterioration` | 0.20 | Minimum quality deterioration magnitude to flag |
| `min_trend_magnitude` | 0.05 | Per-metric scores below this are treated as flat |

### Default calibration

The defaults target the six-entity, six-period synthetic dataset. The
stochastic baseline produces headline composite noise up to approximately ±0.14
across the five non-planted entities; the threshold of 0.20 sits above this
noise floor while being easily crossed by the planted scenario (composite 1.00).

**Production deployments must recalibrate all four parameters** to their own
entity population, time horizon, and acceptable false-positive rate.

## Missing data and edge cases

| Situation | Behaviour |
|---|---|
| Entity has fewer than `min_observation_periods` | Skipped; recorded in `skipped_entity_ids`; no finding |
| Entity has no `PerformanceMetric` rows | Skipped; no crash |
| All headline metrics are flat | `headline_composite = 0.0`; no finding |
| All quality metrics are flat | `quality_composite = 0.0`; no finding |
| `PerformanceMetric` column is `NULL` (future schema) | Period excluded from that metric's series; not zero-filled |
| Period at or beyond entity's `data_period_end` | Excluded from context (future-leakage guard) |

## Future-leakage prevention

The analytic never reads wall-clock time. Each entity's observation window is
bounded by its `data_period_end` field: periods whose `period_start ≥
data_period_end` are excluded. A re-run at any later date against the same
ingested data yields an identical result.

## Finding output

Each finding is a `MetricRiskDivergenceFinding` (frozen Pydantic model):

| Field | Description |
|---|---|
| `finding_key` | Stable deterministic key: `MRD-001:<entity>:<start_label>:<end_label>` |
| `analytic_id` | `"MRD-001"` |
| `entity_id` | Subject entity |
| `window_start` / `window_end` | First period start / last period end (UTC) |
| `supporting_period_count` | Number of periods in the trend window |
| `headline_trend_score` | Direction-adjusted composite (positive = improving) |
| `quality_trend_score` | Direction-adjusted composite (negative = deteriorating) |
| `headline_snapshots` | Per-metric period-values and individual trend scores |
| `quality_snapshots` | Per-metric period-values and individual trend scores |
| `confidence` | `HIGH` / `MODERATE` / `LOW` (data sufficiency, not risk) |
| `confidence_note` | One-line rationale for the confidence level |
| `summary` | Neutral, human-readable description of the observed pattern |

### Confidence levels

Confidence reflects **observational sufficiency** — how much evidence supports
the divergence observation — not how serious the divergence is. Risk/severity
assessment is reserved for later phases.

| Level | Condition |
|---|---|
| `HIGH` | ≥ 5 periods AND both composites ≥ 2× their respective threshold |
| `MODERATE` | ≥ `min_observation_periods` AND both composites ≥ 1× threshold |
| `LOW` | Condition met but fewer periods or marginal magnitudes |

## Limitations

- **Small sample.** With 4–6 periods per entity the normalised slope is a coarse
  trend estimate with wide uncertainty. Treat findings as signals for review,
  not statistically robust conclusions.
- **No causal inference.** The analytic identifies co-occurrence of two trends;
  it makes no claim about which (if either) is caused by the other.
- **Composite masking.** If one headline metric improves strongly while others
  are flat, the composite may cross the threshold even though the signal is
  narrow. The per-metric snapshots in the finding allow a reviewer to inspect
  the individual contributors.
- **No schema-level missingness.** The current `PerformanceMetric` model uses
  `NOT NULL` columns, so all eight fields are always present when a row exists.
  If a future schema extension introduces nullable columns the engine handles
  them conservatively by excluding that period from the affected metric's series.
- **No PostgreSQL migration required.** The analytic reads from existing tables
  with no schema changes; SQLite and (future) PostgreSQL are both supported.

## Synthetic dataset evaluation

The planted ground-truth scenario (`METRIC_RISK_DIVERGENCE`, entity ENT-04)
seeds the following trajectory across 6 monthly periods:

| Metric | Period 1 → Period 6 | Group |
|---|---|---|
| `closure_rate` | 0.80 → 0.96 | Headline (↑) |
| `sla_compliance` | 0.82 → 0.97 | Headline (↑) |
| `mttr_hours` | 7.0 → 3.0 | Headline (↓, improving) |
| `investigation_completeness` | 0.80 → 0.42 | Quality (↓, deteriorating) |
| `evidence_completeness` | 0.82 → 0.45 | Quality (↓, deteriorating) |
| `recurrence_rate` | 0.10 → 0.34 | Quality (↑, deteriorating) |
| `remediation_rate` | 0.78 → 0.44 | Quality (↓, deteriorating) |

**Result:** ENT-04 is detected with `headline_trend_score = 1.00`,
`quality_trend_score = −1.00`, confidence `HIGH`, across all 6 periods.
No other entity fires under default thresholds (the next-highest headline
composite is 0.14 on ENT-03, below the 0.20 threshold).

## Code organisation

```
backend/app/analytics/metric_risk_divergence/
    __init__.py      Public API and module docstring
    config.py        MetricRiskDivergenceConfig (frozen dataclass)
    metrics.py       METRIC_REGISTRY — direction registry for all tracked metrics
    context.py       load_context() — bulk-query context loader (no N+1)
    engine.py        run_divergence_detection() — pure detection logic
    findings.py      MetricRiskDivergenceFinding, MetricSnapshot, ConfidenceLevel
    service.py       run_metric_risk_divergence() — public service entry point

backend/tests/
    test_metric_risk_divergence.py             44 unit tests
    test_metric_risk_divergence_integration.py 16 integration tests (full dataset)
```
