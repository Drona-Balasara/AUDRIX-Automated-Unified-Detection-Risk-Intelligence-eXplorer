"""Supervisory-analytics pipeline.

SAT-SA's analytical capabilities are introduced incrementally. Implemented so
far:

- ``execution_gap`` (Phase 4): deterministic, read-only detection of observable
  execution deviations in the SOC operational record.
- ``negative_space`` (Phase 5): deterministic, read-only detection of observable
  absences of expected monitoring evidence (missing telemetry on critical
  assets; telemetry that disappeared mid-window), emitted only once an
  expectation of the evidence has been established.
- ``anomaly`` (Phase 6): deterministic, read-only unsupervised anomaly detection
  (scikit-learn IsolationForest) over entity-reporting-period feature
  observations. Exposes a raw/normalized anomaly signal and a decision, never a
  SAT-SA risk score, and returns an explicit insufficient-sample result rather
  than modelling too few observations.
- ``peer_benchmark`` (Phase 6): deterministic, read-only comparison of an
  entity-period's reported KPIs against the robust (median/scaled-MAD) baseline
  of its genuinely comparable, same-period peers, with the entity's own value
  excluded and an explicit insufficient-peers status when too few peers exist.

Reserved for later phases (not yet implemented): metric-risk divergence,
investigation fingerprinting, and evidence/confidence/prioritization scoring.
"""
