# Peer Benchmarking

Phase 6's second analytic is **peer benchmarking**: a deterministic, read-only
comparison of an entity-reporting-period's reported KPIs against the **robust
baseline of its genuinely comparable peers in the same period**. Where anomaly
detection screens the whole population for unusual *operational* behaviour, peer
benchmarking asks a narrower, fairer question: **relative to peers like me, in
the same period, is this reported metric materially out of line?** Findings are
observational, per metric, and carry no performance verdict.

## Comparison discipline

Four rules make the comparison defensible, and each is enforced in code and
covered by a unit test:

- **Never compare an entity against itself.** The subject's own value is
  excluded from the baseline it is measured against.
- **Never pool incompatible peers.** Entities are grouped by a single declared
  peer dimension (`scale`, `peer_group`, or `sector`); groups are never merged
  to inflate a sample.
- **Same reporting period only.** A subject in period *P* is compared solely to
  peers that reported in period *P*; period-1 peers can never prop up a period-2
  comparison.
- **Enough peers, or an explicit status.** Below the configured minimum of
  comparable peers the analytic emits an `INSUFFICIENT_PEERS` status — never a
  misleading deviation manufactured from too thin a sample.

## Benchmarkable metrics

The eight reported KPIs on `PerformanceMetric` are benchmarked, each declared in
`app/analytics/peer_benchmark/metrics.py` with a unit and a zero-spread absolute
floor:

`mttr_hours` (hours, floor 2.0) and the seven rates — `closure_rate`,
`sla_compliance`, `escalation_rate`, `investigation_completeness`,
`recurrence_rate`, `remediation_rate`, `evidence_completeness` (floor 0.15).

Each metric is evaluated independently; multiple breaches on one entity-period
produce **separate machine-readable findings**, one per metric.

## Robust statistics (median / scaled MAD)

Peer baselines use robust statistics so a single extreme peer cannot distort the
reference point:

- **Baseline** = the **median** of the peers' values (subject excluded).
- **Dispersion** = the **median absolute deviation (MAD)**, scaled by
  `mad_scale_factor` (default 1.4826, which makes the scaled MAD a consistent
  estimator of the standard deviation for normal data).
- **Deviation measure** = `|self − median| / (scaled MAD)`. A value is flagged
  when this distance is **≥ `mad_threshold`** (default 3.5, the Iglewicz–Hoaglin
  modified z-score cutoff). The threshold is inclusive at equality, which is
  deterministic and unit-tested.

### Zero-MAD policy (documented, never a divide-by-zero)

When peers are identical (or there is a single peer under a lowered minimum) the
scaled MAD is zero and the robust distance is undefined. The engine does **not**
divide by zero and does **not** silently hide the case. Instead it falls back to
a documented **absolute-difference floor** per metric: the subject is flagged
only if `|self − median|` meets or exceeds that metric's
`zero_mad_abs_threshold`. Such findings are tagged with the
`ABSOLUTE_ZERO_MAD` basis so the fallback is visible; MAD-based findings carry
the `ROBUST_MAD` basis.

## Configuration

The frozen, validated `PeerBenchmarkConfig`:

| Field | Default | Role |
| --- | --- | --- |
| `peer_dimension` | `"scale"` | grouping dimension (`scale` / `peer_group` / `sector`) |
| `peer_min_count` | 2 | minimum comparable peers before any comparison |
| `mad_threshold` | 3.5 | robust-distance cutoff (inclusive) |
| `mad_scale_factor` | 1.4826 | MAD → std-dev-consistent scaling |
| `abs_threshold_overrides` | `{}` | per-metric overrides of the zero-MAD floor |

### Why `scale` is the default dimension

On the synthetic dataset the `sector`/`peer_group` dimensions resolve to pairs,
so every entity has exactly one peer — a degenerate baseline. `scale` is the
only dimension with a ≥3-member group (the MEDIUM group), yielding a genuine
robust median/MAD. The other dimensions remain selectable and correctly report
insufficient peers rather than pretending to benchmark.

## Structured finding schema

Each `PeerDeviationFinding` (frozen) carries a deterministic
`finding_key` (`PB-001:{entity_id}:{YYYY-MM}:{metric}`), the `analytic_id`
(`PB-001`), `entity_id`, `period_start`/`period_end`, `metric_name` and
`metric_unit`, the subject's `entity_value`, the `peer_baseline` (median),
`peer_population_count`, `peer_group`, the `deviation_measure`, the
`deviation_basis` (`ROBUST_MAD` / `ABSOLUTE_ZERO_MAD`), the `direction`
(`ABOVE` / `BELOW`), and a neutral `summary`.

The summary states only that the value is "materially above/below the comparable
peer baseline" — explicitly **not** "performing badly." Insufficient-peer cases
are reported as `BenchmarkStatus` records (with the peer population count),
never as findings.

## Expected behaviour on the synthetic dataset

With the default seed and the default `scale` dimension the analytic produces 28
findings across the MEDIUM group and 18 insufficient-peer statuses for the
LARGE/SMALL singletons; both deviation bases are exercised. Switching to the
`sector` dimension yields **zero findings and 36 insufficient-peer statuses** —
the honest result for degenerate groups. The entity carrying the planted
metric-risk-divergence profile surfaces in the `scale` findings across periods.

## Known limitations

- Benchmarking quality is bounded by group size; small real-world peer groups
  will legitimately spend much of their time in the insufficient-peers state.
- A metric breach is a comparative observation, not a judgement; direction and
  magnitude are reported without interpretation.

## Security and logging

The service logs only safe operational metadata — analytic id, peer dimension,
entity-scope size, group counts, finding counts, insufficient-peer counts, and
run duration. It never logs reported KPI values or record content.

## Programmatic usage

```python
from app.analytics.peer_benchmark import run_peer_benchmark, PeerBenchmarkConfig

result = run_peer_benchmark(session)                                   # scale
result = run_peer_benchmark(session, PeerBenchmarkConfig(peer_dimension="sector"))
for finding in result.findings:
    ...                      # per-metric, observational, deterministic
for status in result.statuses:
    ...                      # explicit insufficient-peer accounting
```

The entry point is FastAPI-independent, strictly read-only, and deterministic:
identical database state and configuration always yield an equal result.
