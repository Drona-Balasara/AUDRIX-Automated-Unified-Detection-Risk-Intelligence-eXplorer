# Anomaly Detection

Phase 6 introduces SAT-SA's third analytic: **unsupervised anomaly detection**.
Where execution-gap detection (Phase 4) inspects how recorded work was handled
and negative-space detection (Phase 5) inspects what the record fails to
contain, anomaly detection asks a different question: given the whole population
of entity-reporting-period behaviour, **which observations look unusual relative
to the modelled norm?** It is deterministic, read-only, and deliberately modest
about what its signal means.

## What this analytic is — and is not

- It is an **unsupervised, population-relative** screen. It learns the shape of
  the observed population and flags observations that sit in its sparse regions.
- It is **not** a risk score, a performance verdict, or an accusation. A flagged
  observation is "unusual relative to the modelled population," nothing more.
  The finding carries an analytical anomaly signal, never a SAT-SA risk score.
- It never reads ground truth, scenario labels, planted-anomaly identifiers, or
  any synthetic-only metadata. Features are built purely from operational
  records, so the same code runs unchanged on real data.

## Research inspiration (not an implementation of any framework)

The design draws on two widely documented ideas, adapted rather than copied:

- **scikit-learn's IsolationForest** — an unsupervised method that isolates
  points by recursive random partitioning; anomalies require fewer splits to
  isolate. We fix `random_state` for exact reproducibility and treat the output
  as a signal, not a probability of wrongdoing.
- **MITRE's separation of detection strategies from analytics** — anomaly
  detection is kept in its own modular, explainable package, decoupled from the
  rule-based Phase 4/5 analytics, so each can evolve independently.

## The modelling grain and feature construction

The unit of observation is one **entity × reporting period**. Features are a
small, meaningful set of numeric aggregates computed from raw operational
records within each period `[period_start, period_end)` — never from reported
headline KPIs, which are what peer benchmarking examines instead.

The feature schema is **immutable** and declared once in
`app/analytics/anomaly/features.py`. Each `FeatureSpec` documents its source
tables/fields, aggregation, unit, missing-value policy, and whether it is
eligible for the model. Nine features are model-eligible:

| Feature | Unit | Meaning |
| --- | --- | --- |
| `alerts_per_asset` | ratio per asset | alert volume normalised by asset estimate |
| `crit_high_rate` | rate | fraction of alerts at HIGH/CRITICAL severity |
| `tp_rate` | rate | fraction of alerts confirmed true-positive |
| `closure_rate` | rate | fraction of alerts closed within the period |
| `inv_coverage` | rate | investigations opened per alert |
| `esc_rate` | rate | escalations per alert |
| `mean_inv_dur_h` | hours | mean closed-investigation duration |
| `mean_actions` | count per investigation | mean investigation-action count |
| `mean_evidence` | count per investigation | mean evidence items per investigation |

Two further aggregates (`alert_count`, `rem_rate`) are retained as **context
only** (`anomaly_eligible = False`) and are never fed to the model —
`alert_count` because raw volume would let a high-throughput entity dominate the
geometry, and `rem_rate` because it is undefined in zero-true-positive periods.

### Design decisions that keep the model honest

- **No identifier leakage.** `entity_id`, asset/alert/investigation ids,
  categoricals, timestamps, and free text are never features. A unit test
  asserts the schema is disjoint from any identifier.
- **Volume is normalised, not modelled.** Rates and per-asset / per-investigation
  ratios are used so a large entity is not automatically "anomalous"; raw counts
  are context-only.
- **Undefined denominators are explicit.** A rate whose denominator is zero is
  recorded as `None` (missing), never a silent `0.0`. An observation missing any
  eligible feature is **dropped from the model population**, not imputed.
- **No standardisation.** IsolationForest is tree-based and scale-invariant, so
  the raw feature vector is used directly; adding needless standardisation would
  only obscure the features without changing the partitioning.
- **Deterministic ordering and types.** Observations are built in a stable
  `(entity_id, period_start)` order with consistent `float | None` typing.

## The model and its configuration

The estimator is scikit-learn's `IsolationForest`, configured through the frozen,
validated `AnomalyConfig`:

| Field | Default | Role |
| --- | --- | --- |
| `n_estimators` | 200 | number of isolation trees |
| `max_samples` | `"auto"` | sub-sample size per tree |
| `contamination` | 0.1 | expected minority fraction (decision threshold) |
| `random_state` | 20240601 | fixed seed → exact reproducibility |
| `min_training_samples` | 20 | minimum population before any modelling |
| `model_version` | `anomaly-iforest-v1` | stamped into finding metadata |

`contamination` is set to a **small-minority expectation (10%)**, not tuned to
surface the synthetic dataset's planted scenarios. Choosing `"auto"` empirically
flagged a third of the population — a flood — so an explicit, documented value is
used instead. There is no wall-clock input anywhere; `bootstrap=False` and the
fixed `random_state` make two runs on identical data bit-for-bit equal.

### The insufficient-sample safeguard

If fewer than `min_training_samples` complete observations exist, the analytic
does **not** fit a model or fabricate findings. It returns an explicit
`AnomalyStatus.INSUFFICIENT_SAMPLE` result with zero findings and `None`
metadata. An empty database is simply the degenerate case of this rule.

## Score orientation (documented to avoid confusion)

scikit-learn's `score_samples` returns **higher = more normal**, which is the
opposite of intuition. To present an unambiguous signal, the engine negates and
min-max–normalises the raw scores into `normalized_anomaly_score ∈ [0, 1]` where
**higher = more anomalous**. Both the raw score and the normalised score are
exposed on each finding; neither is a probability or a risk value. If all scores
are identical the normalised value is defined as `0.0` for every observation.

## Structured finding schema

Each `AnomalyFinding` (frozen, in `findings.py`) carries:

- `finding_key` — stable `AN-001:{entity_id}:{YYYY-MM}` key for dedup/ordering,
- `analytic_id` (`AN-001`), `entity_id`, `period_start`, `period_end`,
- `decision` (`ANOMALOUS` / `NOT_ANOMALOUS`),
- `raw_anomaly_score` and `normalized_anomaly_score` (analytical, not risk),
- `feature_snapshot` — the eligible feature vector that was scored,
- `notable_features` — the three features furthest from the population median
  (robust distance, deterministic tie-break by name), each with its value and
  the population median for context,
- `summary` — a neutral explanation ("unusual relative to the modelled
  population"), with no claim of malice, misconduct, or policy violation.

Findings are deduplicated by key and emitted in sorted key order. Safe
`ModelMetadata` (feature names, parameters, training and scored counts,
`random_state`, model version) travels with the result; **no model binary is
ever persisted to the database.**

## Expected behaviour on the synthetic dataset

With the default seed the population is 36 observations (6 entities × 6 monthly
periods), all complete (zero dropped). The model fits and flags **4
observations (~11%)** — not a flood — and the flagged set includes the entity
carrying the planted metric-risk-divergence profile, which surfaces naturally
without any threshold manipulation or scenario-specific code. Ground truth is
used **only** by the integration test as an evaluation oracle.

## Known limitations

- Population-relative: with very few entities the "norm" is thin, which is
  exactly why the minimum-sample guard exists.
- The signal is unlabelled and screening-only; it ranks unusualness, it does not
  explain cause or assign severity.
- `contamination` encodes an expectation of how large the unusual minority is; a
  materially different real-world population may warrant re-review of that value.

## Security and logging

The service logs only safe operational metadata — analytic id, status, counts,
model version, `contamination`, `random_state`, and run duration. It never logs
feature values, operational rows, credentials, or any record content.

## Programmatic usage

```python
from app.analytics.anomaly import run_anomaly_detection, AnomalyConfig

result = run_anomaly_detection(session)                 # defaults
result = run_anomaly_detection(session, AnomalyConfig(min_training_samples=10))
if result.status.value == "OK":
    for finding in result.findings:
        ...  # observational, deterministic, read-only
```

The entry point is FastAPI-independent, strictly read-only, and deterministic:
identical database state and configuration always yield an equal result.
