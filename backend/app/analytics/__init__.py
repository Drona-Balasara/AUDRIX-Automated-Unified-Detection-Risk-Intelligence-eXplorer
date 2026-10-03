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

- ``metric_risk_divergence`` (Phase 7): deterministic, read-only detection of
  reporting periods where headline SOC performance metrics show an improving
  trend while underlying operational-quality indicators materially deteriorate.
  Uses direction-adjusted least-squares trend scoring over the reported
  ``PerformanceMetric`` KPIs; emits neutral findings for supervisory review
  without making any assertion about intent or cause.

- ``investigation_fingerprinting`` (Phase 8): deterministic, read-only
  investigation-sequence analysis.  Represents each investigation as an ordered
  tuple of ``ActionType`` values (a *fingerprint*), then runs three analytically
  distinct detectors: ``IF-REP-001`` flags entity-period windows where a
  substantial number of investigations share an identical fingerprint (Potential
  Template-Driven Investigation Pattern); ``IF-DEV-002`` flags individual
  investigations whose sequence deviates materially from the entity's
  plurality-baseline fingerprint via normalized Levenshtein edit distance
  (Potential Investigation Sequence Deviation); ``IF-MEA-003`` flags closed
  HIGH/CRITICAL investigations that contain neither ``VALIDATE`` nor
  ``EVIDENCE_REVIEW`` (Potential Missing Investigation Action).  No external
  dependencies are added; edit distance is a dependency-free stdlib
  implementation.

- ``evidence`` (Phase 9): shared evidence and confidence layer usable by all
  SAT-SA analytics.  For every finding produced by Phases 4–8, an
  ``AnnotatedResult`` wrapper carries a parallel ``evidence_map`` keyed by
  ``finding_key``.  Each ``FindingEvidence`` entry holds an ordered tuple of
  ``EvidenceRef`` objects (source type, stable record ID, role, period label,
  reason) plus a categorical ``EvidenceConfidence`` (HIGH / MODERATE / LOW)
  and the specific ``ConfidenceFactor`` list that led to it.  Evidence is built
  from already-loaded context with no additional database queries; source record
  IDs are verified against real ORM rows in integration tests; absent evidence
  is represented as an ``ABSENCE`` source type rather than a fabricated record
  ID.  Confidence reflects data sufficiency, not risk or severity.

Reserved for later phases (not yet implemented): supervisory review queue and
prioritization scoring.
"""
