"""Supervisory-analytics pipeline.

SAT-SA's analytical capabilities are introduced incrementally. Implemented so
far:

- ``execution_gap`` (Phase 4): deterministic, read-only detection of observable
  execution deviations in the SOC operational record.

Reserved for later phases (not yet implemented): negative-space detection,
anomaly detection, peer benchmarking, metric-risk divergence, investigation
fingerprinting, and evidence/confidence/prioritization scoring.
"""
