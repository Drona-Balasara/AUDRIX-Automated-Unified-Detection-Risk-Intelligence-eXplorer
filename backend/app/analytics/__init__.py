"""Supervisory-analytics pipeline.

SAT-SA's analytical capabilities are introduced incrementally. Implemented so
far:

- ``execution_gap`` (Phase 4): deterministic, read-only detection of observable
  execution deviations in the SOC operational record.
- ``negative_space`` (Phase 5): deterministic, read-only detection of observable
  absences of expected monitoring evidence (missing telemetry on critical
  assets; telemetry that disappeared mid-window), emitted only once an
  expectation of the evidence has been established.

Reserved for later phases (not yet implemented): anomaly detection, peer
benchmarking, metric-risk divergence, investigation fingerprinting, and
evidence/confidence/prioritization scoring.
"""
