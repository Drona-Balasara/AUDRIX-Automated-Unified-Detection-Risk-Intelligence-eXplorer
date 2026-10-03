"""Evidence builders for each Phase 4–8 analytic.

Each builder accepts the already-loaded analytic context (and/or the result)
and returns an ``EvidenceMap`` (``dict[finding_key, FindingEvidence]``) without
issuing any database queries.  All source record IDs required are obtained from
the in-memory context objects that were already populated by ``load_context()``.

No N+1 queries, no additional database round-trips, no fabrication of evidence.

Builder contract
----------------
- A builder takes a result and its corresponding context.
- It returns an ``EvidenceMap`` keyed by ``finding_key``.
- Every finding in the result has exactly one entry in the returned map.
- If a finding has no direct source records (rare edge case), it still gets an
  entry — with an ABSENCE or ENTITY_PERIOD_OBSERVATION reference and a LOW
  confidence explanation.
- The builder is pure and side-effect-free: identical inputs → identical output.

Period labels
-------------
Period labels are ``YYYY-MM`` strings derived from ``period_start``.  When
``period_start`` is not tz-aware (SQLite strips tz), the label is still safe
because it uses only ``year`` and ``month``.
"""

from __future__ import annotations

from datetime import datetime

from app.analytics.evidence.confidence import (
    aggregate_confidence,
    factors_from_counts,
    note_from_confidence,
)
from app.analytics.evidence.model import (
    ConfidenceFactor,
    EvidenceConfidence,
    EvidenceRef,
    EvidenceSourceType,
    FindingEvidence,
    make_finding_evidence,
)


def _period_label(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# Phase 4 — Execution Gap
# ---------------------------------------------------------------------------

def build_execution_gap_evidence(result) -> dict[str, FindingEvidence]:
    """Build evidence for all execution-gap findings.

    ExecutionGapFinding already carries direct source record IDs (alert_id,
    investigation_id, asset_id, related_alert_ids).  Evidence refs are built
    directly from those fields without querying the database.

    Rule-specific evidence:
    - ESCALATION_GAP: triggering alert + (optional) investigation; absence of
      escalation is represented as an ABSENCE ref.
    - INVESTIGATION_GAP: triggering alert; absence of investigation is ABSENCE.
    - REMEDIATION_GAP: one or more recurring alerts; absence of remediation is
      ABSENCE.
    """
    ANALYTIC_ID = "EG-001"
    evidence_map: dict[str, FindingEvidence] = {}

    for finding in result.findings:
        refs: list[EvidenceRef] = []
        period = _period_label(finding.observed_at or finding.window_start)

        # --- Alert reference(s) -------------------------------------------
        if finding.alert_id:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ALERT,
                source_id=finding.alert_id,
                role="triggering_alert",
                period_label=period,
                reason=(
                    f"Alert {finding.alert_id} (severity {finding.alert_severity or 'unknown'}) "
                    f"is the direct trigger for this {finding.gap_type.value} finding."
                ),
            ))

        for aid in finding.related_alert_ids:
            if aid != finding.alert_id:
                refs.append(EvidenceRef(
                    source_type=EvidenceSourceType.ALERT,
                    source_id=aid,
                    role="related_recurring_alert",
                    period_label=period,
                    reason=(
                        f"Alert {aid} is part of the recurring alert group "
                        f"(recurrence_key={finding.recurrence_key!r}) supporting this finding."
                    ),
                ))

        # --- Investigation reference ---------------------------------------
        if finding.investigation_id:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.INVESTIGATION,
                source_id=finding.investigation_id,
                role="associated_investigation",
                period_label=period,
                reason=(
                    f"Investigation {finding.investigation_id} is directly linked "
                    f"to the triggering alert and supports this gap finding."
                ),
            ))

        # --- Asset reference ----------------------------------------------
        if finding.asset_id:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ASSET,
                source_id=finding.asset_id,
                role="affected_asset",
                period_label=period,
                reason=f"Asset {finding.asset_id} is the monitored system associated with this alert.",
            ))

        # --- Absence reference (the missing expected condition) -----------
        gap = finding.gap_type.value
        abs_scope = f"entity:{finding.entity_id}"
        if finding.alert_id:
            abs_scope += f"|alert:{finding.alert_id}"
        if finding.recurrence_key:
            abs_scope += f"|recurrence_key:{finding.recurrence_key}"

        if "ESCALATION" in gap:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ABSENCE,
                source_id=f"missing_escalation|{abs_scope}",
                role="missing_expected_escalation",
                period_label=period,
                reason=finding.expected_condition,
            ))
        elif "INVESTIGATION" in gap:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ABSENCE,
                source_id=f"missing_investigation|{abs_scope}",
                role="missing_expected_investigation",
                period_label=period,
                reason=finding.expected_condition,
            ))
        elif "REMEDIATION" in gap:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ABSENCE,
                source_id=f"missing_remediation|{abs_scope}",
                role="missing_expected_remediation",
                period_label=period,
                reason=finding.expected_condition,
            ))

        # --- Confidence ---------------------------------------------------
        direct = sum(1 for r in refs if r.source_type not in (
            EvidenceSourceType.ABSENCE, EvidenceSourceType.ASSET
        ))
        factors = factors_from_counts(
            direct_record_count=direct,
            has_absence=True,  # all execution-gap findings involve an absence
        )
        # Recurring-remediation gaps reference multiple alerts → more evidence
        if len(finding.related_alert_ids) >= 3:
            factors.append(ConfidenceFactor.MULTIPLE_DIRECT_RECORDS)

        conf = aggregate_confidence(factors, refs)
        note = note_from_confidence(conf, factors, direct)

        evidence_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )

    return evidence_map


# ---------------------------------------------------------------------------
# Phase 5 — Negative Space
# ---------------------------------------------------------------------------

def build_negative_space_evidence(result) -> dict[str, FindingEvidence]:
    """Build evidence for all negative-space findings.

    NegativeSpaceFinding carries asset_id and related_telemetry_ids.  For
    MONITORING_GAP (no telemetry ever), the evidence is an absence + asset.
    For TELEMETRY_DISAPPEARANCE, the evidence is the last-observed telemetry
    rows (related_telemetry_ids) + an absence for the silent period.
    """
    ANALYTIC_ID = "NS-001"
    evidence_map: dict[str, FindingEvidence] = {}

    for finding in result.findings:
        refs: list[EvidenceRef] = []
        obs_label = _period_label(finding.observation_start)
        gap = finding.gap_type.value

        # --- Asset reference ----------------------------------------------
        if finding.asset_id:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ASSET,
                source_id=finding.asset_id,
                role="monitored_asset",
                period_label=obs_label,
                reason=(
                    f"Asset {finding.asset_id} "
                    f"(criticality: {finding.asset_criticality or 'unknown'}, "
                    f"expected telemetry: {finding.expected_telemetry or 'unknown'}) "
                    f"is the subject of this {gap} finding."
                ),
            ))

        # --- Telemetry record references (for disappearance gaps) --------
        for tid in finding.related_telemetry_ids:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.TELEMETRY_RECORD,
                source_id=tid,
                role="last_observed_telemetry",
                period_label=obs_label,
                reason=(
                    f"Telemetry record {tid} establishes the last observed "
                    f"telemetry boundary before the monitoring gap began."
                ),
            ))

        # --- Absence reference -------------------------------------------
        abs_scope = f"entity:{finding.entity_id}"
        if finding.asset_id:
            abs_scope += f"|asset:{finding.asset_id}"

        if "MONITORING_GAP" in gap:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ABSENCE,
                source_id=f"missing_telemetry|{abs_scope}",
                role="missing_expected_telemetry",
                period_label=obs_label,
                reason=finding.expected_evidence,
            ))
        else:
            silence = f"{finding.silence_hours:.1f}h" if finding.silence_hours else "unknown"
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ABSENCE,
                source_id=f"telemetry_gap|{abs_scope}",
                role="telemetry_continuity_gap",
                period_label=obs_label,
                reason=(
                    f"No telemetry observed for {silence} after the last recorded "
                    f"telemetry boundary ({finding.expected_evidence})."
                ),
            ))

        # --- Confidence ---------------------------------------------------
        baseline_n = finding.baseline_observation_count or 0
        direct = len(finding.related_telemetry_ids) + (1 if finding.asset_id else 0)

        factors = factors_from_counts(
            direct_record_count=direct,
            baseline_count=baseline_n,
            has_absence=True,
        )
        # Monitoring gaps (zero telemetry) are more reliable than disappearance
        # because they don't depend on a baseline — flag disappearance as
        # moderate-baseline if we have few baseline rows.
        if "DISAPPEARANCE" in gap and baseline_n < 3:
            if ConfidenceFactor.SMALL_BASELINE not in factors:
                factors.append(ConfidenceFactor.SMALL_BASELINE)

        conf = aggregate_confidence(factors, refs)
        note = note_from_confidence(conf, factors, direct)

        evidence_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )

    return evidence_map


# ---------------------------------------------------------------------------
# Phase 6 — Anomaly Detection
# ---------------------------------------------------------------------------

def build_anomaly_evidence(result) -> dict[str, FindingEvidence]:
    """Build evidence for anomaly findings.

    AnomalyFinding is keyed at entity-period grain and carries no direct source
    record IDs.  Evidence references a computed ENTITY_PERIOD_OBSERVATION.
    Confidence is derived from the model metadata: observation count, dropped
    observations, and normalized anomaly score.
    """
    ANALYTIC_ID = "AN-001"
    evidence_map: dict[str, FindingEvidence] = {}

    scored = result.scored_observation_count
    dropped = result.dropped_incomplete_count
    total_obs = result.observation_count
    meta = result.model_metadata

    for finding in result.findings:
        period = _period_label(finding.period_start)
        obs_id = f"{finding.entity_id}:{period}"

        refs = [
            EvidenceRef(
                source_type=EvidenceSourceType.ENTITY_PERIOD_OBSERVATION,
                source_id=obs_id,
                role="anomalous_observation",
                period_label=period,
                reason=(
                    f"Entity {finding.entity_id} in period {period} produced "
                    f"a normalized anomaly signal of "
                    f"{finding.normalized_anomaly_score:.2f} (threshold model: "
                    f"contamination={meta.contamination if meta else 'n/a'}, "
                    f"population={scored} observations)."
                ),
            ),
        ]

        # Add notable features as context (not direct records, but derived facts).
        for nf in finding.notable_features[:2]:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ENTITY_PERIOD_OBSERVATION,
                source_id=f"{obs_id}:feature:{nf.name}",
                role="notable_feature",
                period_label=period,
                reason=(
                    f"Feature '{nf.name}' value {nf.value:.3g} deviates from "
                    f"population median {nf.population_median:.3g}."
                ),
            ))

        factors = factors_from_counts(
            direct_record_count=1,          # 1 entity-period observation
            baseline_count=scored,          # population used for scoring
            has_dropped=dropped > 0,
            min_large_comparison=20,
            min_adequate_baseline=15,
        )

        # Anomaly score close to threshold → minimum-threshold
        if finding.normalized_anomaly_score < 0.55:
            factors.append(ConfidenceFactor.MINIMUM_THRESHOLD)

        if dropped > 0:
            factors.append(ConfidenceFactor.DROPPED_OBSERVATIONS)

        conf = aggregate_confidence(factors, refs)
        note = note_from_confidence(conf, factors, 1)

        evidence_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )

    return evidence_map


# ---------------------------------------------------------------------------
# Phase 6 — Peer Benchmarking
# ---------------------------------------------------------------------------

def build_peer_benchmark_evidence(result) -> dict[str, FindingEvidence]:
    """Build evidence for peer-deviation findings.

    PeerDeviationFinding carries entity_id, period_start, metric_name,
    entity_value, peer_baseline, peer_population_count.  Evidence references
    the subject entity-period observation and a PEER_BASELINE reference.
    """
    ANALYTIC_ID = "PB-001"
    evidence_map: dict[str, FindingEvidence] = {}

    for finding in result.findings:
        period = _period_label(finding.period_start)

        refs = [
            EvidenceRef(
                source_type=EvidenceSourceType.PERFORMANCE_METRIC,
                source_id=f"{finding.entity_id}:{period}:{finding.metric_name}",
                role="subject_metric_observation",
                period_label=period,
                reason=(
                    f"Entity {finding.entity_id} reported {finding.metric_name}="
                    f"{finding.entity_value:.3g} in period {period}, "
                    f"{finding.direction.value.lower()} the peer baseline of "
                    f"{finding.peer_baseline:.3g}."
                ),
            ),
            EvidenceRef(
                source_type=EvidenceSourceType.PEER_BASELINE,
                source_id=(
                    f"peer_group:{finding.peer_group}:"
                    f"period:{period}:"
                    f"metric:{finding.metric_name}"
                ),
                role="peer_baseline",
                period_label=period,
                reason=(
                    f"Peer baseline (median={finding.peer_baseline:.3g}) derived from "
                    f"{finding.peer_population_count} comparable peers "
                    f"(group={finding.peer_group!r}) in the same period, "
                    f"subject's own value excluded."
                ),
            ),
        ]

        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=finding.peer_population_count,
            min_adequate_baseline=3,
            min_large_comparison=6,
        )

        # Zero-MAD (identical peers) → fragile baseline
        from app.analytics.peer_benchmark.findings import DeviationBasis
        if finding.deviation_basis == DeviationBasis.ABSOLUTE_ZERO_MAD:
            factors.append(ConfidenceFactor.FRAGILE_BASELINE)

        conf = aggregate_confidence(factors, refs)
        note = note_from_confidence(conf, factors, 1)

        evidence_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )

    return evidence_map


# ---------------------------------------------------------------------------
# Phase 7 — Metric-Risk Divergence
# ---------------------------------------------------------------------------

def build_metric_risk_divergence_evidence(result) -> dict[str, FindingEvidence]:
    """Build evidence for metric-risk-divergence findings.

    MetricRiskDivergenceFinding already carries a confidence field from Phase 7.
    The Phase 9 evidence layer adds explicit evidence references and maps the
    existing confidence level to the shared EvidenceConfidence enum without
    changing the detection logic.
    """
    ANALYTIC_ID = "MRD-001"
    evidence_map: dict[str, FindingEvidence] = {}

    for finding in result.findings:
        start_label = _period_label(finding.window_start)
        end_label = _period_label(finding.window_end)
        refs: list[EvidenceRef] = []

        # One performance-metric observation ref per supporting period.
        for snap in finding.headline_snapshots:
            for period_label, value in snap.period_values:
                refs.append(EvidenceRef(
                    source_type=EvidenceSourceType.PERFORMANCE_METRIC,
                    source_id=(
                        f"{finding.entity_id}:{period_label}:{snap.name}"
                    ),
                    role="headline_kpi",
                    period_label=period_label,
                    reason=(
                        f"Headline KPI '{snap.name}'={value:.4g} in period {period_label}."
                    ),
                ))
        for snap in finding.quality_snapshots:
            for period_label, value in snap.period_values:
                refs.append(EvidenceRef(
                    source_type=EvidenceSourceType.PERFORMANCE_METRIC,
                    source_id=(
                        f"{finding.entity_id}:{period_label}:{snap.name}"
                    ),
                    role="quality_indicator",
                    period_label=period_label,
                    reason=(
                        f"Quality indicator '{snap.name}'={value:.4g} in period {period_label}."
                    ),
                ))

        # Map the existing Phase 7 confidence to the shared enum.
        from app.analytics.metric_risk_divergence.findings import ConfidenceLevel
        _conf_map = {
            ConfidenceLevel.HIGH:     EvidenceConfidence.HIGH,
            ConfidenceLevel.MODERATE: EvidenceConfidence.MODERATE,
            ConfidenceLevel.LOW:      EvidenceConfidence.LOW,
        }
        conf = _conf_map.get(finding.confidence, EvidenceConfidence.MODERATE)

        factors = factors_from_counts(
            direct_record_count=len(refs),
            period_count=finding.supporting_period_count,
            min_adequate_periods=4,
        )

        if finding.supporting_period_count < 4:
            factors.append(ConfidenceFactor.INSUFFICIENT_PERIODS)
        elif finding.supporting_period_count < 5:
            factors.append(ConfidenceFactor.MODERATE_BASELINE)

        # Confidence note comes from Phase 7's own note; use it directly.
        note = finding.confidence_note or note_from_confidence(conf, factors, len(refs))

        evidence_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )

    return evidence_map


# ---------------------------------------------------------------------------
# Phase 8 — Investigation Fingerprinting
# ---------------------------------------------------------------------------

def build_fingerprint_evidence(result) -> dict[str, FindingEvidence]:
    """Build evidence for all three investigation-fingerprinting finding types.

    - IF-REP-001: matching_investigation_ids are the direct evidence refs.
    - IF-DEV-002: investigation_id is the direct ref; baseline is PEER_BASELINE.
    - IF-MEA-003: investigation_id + alert_id are the direct refs; missing
      actions are ABSENCE refs.
    """
    evidence_map: dict[str, FindingEvidence] = {}
    evidence_map.update(_build_rep_evidence(result.repetitive_findings))
    evidence_map.update(_build_dev_evidence(result.deviation_findings))
    evidence_map.update(_build_mea_evidence(result.missing_action_findings))
    return evidence_map


def _build_rep_evidence(findings) -> dict[str, FindingEvidence]:
    """IF-REP-001: repetitive-workflow evidence."""
    from app.analytics.investigation_fingerprinting.findings import FingerprintConfidence
    ANALYTIC_ID = "IF-REP-001"
    _conf_map = {
        FingerprintConfidence.HIGH:     EvidenceConfidence.HIGH,
        FingerprintConfidence.MODERATE: EvidenceConfidence.MODERATE,
        FingerprintConfidence.LOW:      EvidenceConfidence.LOW,
    }
    result_map: dict[str, FindingEvidence] = {}

    for finding in findings:
        refs: list[EvidenceRef] = []
        for iid in finding.matching_investigation_ids:
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.INVESTIGATION,
                source_id=iid,
                role="repeated_pattern_investigation",
                period_label=finding.period_label,
                reason=(
                    f"Investigation {iid} shares the identical action sequence "
                    f"{list(finding.dominant_fingerprint)} with "
                    f"{finding.matching_investigation_count - 1} other investigations "
                    f"in this period."
                ),
            ))

        conf = _conf_map.get(finding.confidence, EvidenceConfidence.MODERATE)
        n = finding.matching_investigation_count
        factors = factors_from_counts(
            direct_record_count=n,
            at_minimum_threshold=(n == 3),  # exactly at default threshold
        )
        if finding.repetition_rate < 0.25:
            factors.append(ConfidenceFactor.MODERATE_BASELINE)

        note = note_from_confidence(conf, factors, n)
        result_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )
    return result_map


def _build_dev_evidence(findings) -> dict[str, FindingEvidence]:
    """IF-DEV-002: sequence-deviation evidence."""
    from app.analytics.investigation_fingerprinting.findings import FingerprintConfidence
    ANALYTIC_ID = "IF-DEV-002"
    _conf_map = {
        FingerprintConfidence.HIGH:     EvidenceConfidence.HIGH,
        FingerprintConfidence.MODERATE: EvidenceConfidence.MODERATE,
        FingerprintConfidence.LOW:      EvidenceConfidence.LOW,
    }
    result_map: dict[str, FindingEvidence] = {}

    for finding in findings:
        period = _period_label(finding.period_start)
        refs = [
            EvidenceRef(
                source_type=EvidenceSourceType.INVESTIGATION,
                source_id=finding.investigation_id,
                role="deviating_investigation",
                period_label=period,
                reason=(
                    f"Investigation {finding.investigation_id} has sequence "
                    f"{list(finding.observed_fingerprint)} with normalized "
                    f"edit distance {finding.normalized_distance:.2f} from the "
                    f"entity baseline {list(finding.baseline_fingerprint)}."
                ),
            ),
            EvidenceRef(
                source_type=EvidenceSourceType.PEER_BASELINE,
                source_id=(
                    f"entity_baseline:{finding.entity_id}:"
                    f"n={finding.baseline_investigation_count}"
                ),
                role="entity_baseline_fingerprint",
                period_label=period,
                reason=(
                    f"Plurality baseline derived from {finding.baseline_investigation_count} "
                    f"prior investigations of entity {finding.entity_id}."
                ),
            ),
        ]

        conf = _conf_map.get(finding.confidence, EvidenceConfidence.LOW)
        n_baseline = finding.baseline_investigation_count

        factors = factors_from_counts(
            direct_record_count=1,
            baseline_count=n_baseline,
            fragile_baseline=(n_baseline < 3),
            min_adequate_baseline=3,
            min_large_comparison=8,
        )
        # Explicitly flag as fragile: this is the documented Phase 8 limitation.
        if n_baseline < 5:
            if ConfidenceFactor.FRAGILE_BASELINE not in factors:
                factors.append(ConfidenceFactor.FRAGILE_BASELINE)

        note = note_from_confidence(conf, factors, 1)
        result_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )
    return result_map


def _build_mea_evidence(findings) -> dict[str, FindingEvidence]:
    """IF-MEA-003: missing-expected-action evidence."""
    from app.analytics.investigation_fingerprinting.findings import FingerprintConfidence
    ANALYTIC_ID = "IF-MEA-003"
    _conf_map = {
        FingerprintConfidence.HIGH:     EvidenceConfidence.HIGH,
        FingerprintConfidence.MODERATE: EvidenceConfidence.MODERATE,
        FingerprintConfidence.LOW:      EvidenceConfidence.LOW,
    }
    result_map: dict[str, FindingEvidence] = {}

    for finding in findings:
        period = _period_label(finding.period_start)
        refs = [
            EvidenceRef(
                source_type=EvidenceSourceType.INVESTIGATION,
                source_id=finding.investigation_id,
                role="incomplete_investigation",
                period_label=period,
                reason=(
                    f"Closed investigation {finding.investigation_id} on a "
                    f"{finding.alert_severity} severity alert has action sequence "
                    f"{list(finding.observed_fingerprint)} — missing expected "
                    f"completeness action(s): {sorted(finding.missing_action_types)}."
                ),
            ),
            EvidenceRef(
                source_type=EvidenceSourceType.ALERT,
                source_id=finding.alert_id,
                role="associated_high_crit_alert",
                period_label=period,
                reason=(
                    f"Alert {finding.alert_id} with severity {finding.alert_severity} "
                    f"is the trigger for this investigation."
                ),
            ),
        ]

        for missing in sorted(finding.missing_action_types):
            refs.append(EvidenceRef(
                source_type=EvidenceSourceType.ABSENCE,
                source_id=(
                    f"missing_action|inv:{finding.investigation_id}|action:{missing}"
                ),
                role="missing_expected_action",
                period_label=period,
                reason=(
                    f"Action type '{missing}' is absent from the investigation's "
                    f"sequence but expected for closed {finding.alert_severity} "
                    f"severity investigations per the domain's full-workflow definition."
                ),
            ))

        conf = _conf_map.get(finding.confidence, EvidenceConfidence.MODERATE)
        n_actions = len(finding.observed_fingerprint)
        factors = factors_from_counts(
            direct_record_count=2,
            has_absence=True,
        )
        # Very short sequence → less clear the investigation was substantive
        if n_actions <= 2:
            factors.append(ConfidenceFactor.MINIMUM_THRESHOLD)

        note = note_from_confidence(conf, factors, 2)
        result_map[finding.finding_key] = make_finding_evidence(
            finding_key=finding.finding_key,
            analytic_id=ANALYTIC_ID,
            refs=refs,
            confidence=conf,
            factors=factors,
            confidence_note=note,
        )
    return result_map
