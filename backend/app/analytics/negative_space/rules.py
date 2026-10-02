"""Negative-space rule definitions and the rule registry.

Each rule is a small, self-contained unit with a stable identifier, a
human-readable name/description, an explicit enabled flag, and a pure
``evaluate`` method that reads the shared :class:`DetectionContext` and returns
structured findings. Rules never touch the database or HTTP layer; the engine
owns orchestration. Thresholds come from :class:`NegativeSpaceConfig` — no magic
numbers live in the rule bodies.

The governing discipline: a rule establishes that evidence was *reasonably
expected* before reporting its absence. "No rows exist" is never equated with
"negative space" without an explicit expectation condition.

Rule ownership (avoiding duplicate findings for one underlying problem):
- NS-001 owns "critical monitored asset with NO telemetry at all".
- NS-002 owns "monitored asset with a prior telemetry baseline whose signal
  then stopped before the end of the observed window".
These conditions are disjoint by construction (NS-001 requires zero telemetry;
NS-002 requires at least ``disappearance_min_baseline_periods`` records), so an
asset cannot be double-reported. Neither overlaps the Phase 4 execution-gap
rules, which operate on alerts/investigations/escalations/remediations rather
than on monitoring telemetry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.analytics.negative_space.config import (
    NegativeSpaceConfig,
    criticality_at_least,
)
from app.analytics.negative_space.context import DetectionContext, ensure_utc
from app.analytics.negative_space.findings import (
    GapType,
    NegativeSpaceFinding,
    ReasonCode,
)

class NegativeSpaceRule(ABC):
    """Base class for a deterministic, read-only negative-space rule."""

    rule_id: str
    name: str
    description: str
    gap_type: GapType
    enabled: bool = True

    @abstractmethod
    def evaluate(
        self, ctx: DetectionContext, config: NegativeSpaceConfig
    ) -> list[NegativeSpaceFinding]:
        """Return findings for this rule given the context and configuration."""
        raise NotImplementedError


class CriticalAssetMissingTelemetryRule(NegativeSpaceRule):
    rule_id = "NS-001"
    name = "Critical asset without telemetry"
    description = (
        "A critical asset explicitly flagged as monitoring-expected for which no "
        "telemetry of any kind was recorded across the entire observed window."
    )
    gap_type = GapType.MONITORING_GAP

    def evaluate(self, ctx, config):
        findings: list[NegativeSpaceFinding] = []
        for entity_id in ctx.entity_ids():
            entity = ctx.entities[entity_id]
            start = ensure_utc(entity.data_period_start)
            end = ensure_utc(entity.data_period_end)
            if start is None or end is None:
                # Cannot establish an observation window; do not infer a gap.
                continue
            window_hours = (end - start).total_seconds() / 3600.0
            if window_hours < config.monitoring_min_observation_hours:
                # Observed window too short for a total absence to be meaningful.
                continue
            for asset in ctx.assets_by_entity[entity_id]:
                if config.monitoring_require_expected_flag and not asset.monitoring_expected:
                    continue
                if not criticality_at_least(asset.criticality, config.monitoring_min_criticality):
                    continue
                if ctx.telemetry_for(asset.asset_id):
                    # Some telemetry exists: this is not a total monitoring gap.
                    # Continuity gaps are owned by NS-002.
                    continue
                expected_tel = asset.expected_telemetry
                findings.append(
                    NegativeSpaceFinding(
                        finding_key=f"{self.rule_id}:{asset.asset_id}",
                        rule_id=self.rule_id,
                        gap_type=self.gap_type,
                        reason_code=ReasonCode.CRITICAL_ASSET_NO_TELEMETRY,
                        entity_id=entity_id,
                        asset_id=asset.asset_id,
                        observation_start=start,
                        observation_end=end,
                        asset_criticality=asset.criticality,
                        expected_telemetry=expected_tel,
                        summary=(
                            f"Potential monitoring coverage gap: {asset.criticality} asset "
                            f"{asset.asset_id} is flagged monitoring-expected but has no "
                            "telemetry across the observed window."
                        ),
                        expected_evidence=(
                            f"A {asset.criticality} monitoring-expected asset is expected to "
                            "produce at least one telemetry record"
                            + (f" (expected category {expected_tel})" if expected_tel else "")
                            + " within the observed window."
                        ),
                        observed_evidence=(
                            "No telemetry record of any category references this asset across "
                            "the entire observed window. This is a potential visibility gap and "
                            "does not by itself indicate tampering or intentional disabling."
                        ),
                    )
                )
        return findings


class TelemetryDisappearanceRule(NegativeSpaceRule):
    rule_id = "NS-002"
    name = "Telemetry disappearance"
    description = (
        "A monitored asset that produced a prior telemetry baseline whose signal "
        "then stopped, leaving a trailing silence to the end of the observed "
        "window that exceeds the configured threshold."
    )
    gap_type = GapType.TELEMETRY_DISAPPEARANCE

    def evaluate(self, ctx, config):
        findings: list[NegativeSpaceFinding] = []
        for entity_id in ctx.entity_ids():
            entity = ctx.entities[entity_id]
            end = ensure_utc(entity.data_period_end)
            if end is None:
                continue
            for asset in ctx.assets_by_entity[entity_id]:
                if config.monitoring_require_expected_flag and not asset.monitoring_expected:
                    continue
                telemetry = ctx.telemetry_for(asset.asset_id)
                if len(telemetry) < config.disappearance_min_baseline_periods:
                    # No meaningful prior baseline: an asset that was never (or
                    # barely) observed cannot be said to have "disappeared".
                    continue
                last_seen = max(ensure_utc(t.period_end) for t in telemetry)
                if last_seen >= end:
                    # Still reporting through the end of the window.
                    continue
                silence_hours = (end - last_seen).total_seconds() / 3600.0
                if silence_hours < config.telemetry_gap_threshold_hours:
                    # Trailing gap below the configured threshold: not a disappearance.
                    continue
                related = tuple(sorted(t.telemetry_id for t in telemetry))
                findings.append(
                    NegativeSpaceFinding(
                        finding_key=f"{self.rule_id}:{asset.asset_id}",
                        rule_id=self.rule_id,
                        gap_type=self.gap_type,
                        reason_code=ReasonCode.TELEMETRY_CONTINUITY_GAP,
                        entity_id=entity_id,
                        asset_id=asset.asset_id,
                        related_telemetry_ids=related,
                        observation_start=last_seen,
                        observation_end=end,
                        last_evidence_at=last_seen,
                        asset_criticality=asset.criticality,
                        expected_telemetry=asset.expected_telemetry,
                        baseline_observation_count=len(telemetry),
                        silence_hours=round(silence_hours, 3),
                        summary=(
                            f"Potential telemetry continuity gap: asset {asset.asset_id} "
                            f"produced telemetry for {len(telemetry)} prior period(s) but has "
                            "been silent through the end of the observed window."
                        ),
                        expected_evidence=(
                            f"An asset with {len(telemetry)} prior telemetry record(s) is "
                            "expected to continue producing telemetry through the observed "
                            "window."
                        ),
                        observed_evidence=(
                            "No telemetry references this asset after its last observed record; "
                            f"the trailing silence is approximately {silence_hours:.0f}h. This is "
                            "a potential monitoring continuity gap and does not by itself "
                            "indicate tampering or intentional disabling."
                        ),
                    )
                )
        return findings


# Explicit allowlist of negative-space rules (discoverable and testable).
REGISTRY: tuple[NegativeSpaceRule, ...] = (
    CriticalAssetMissingTelemetryRule(),
    TelemetryDisappearanceRule(),
)


def get_rules(enabled_only: bool = True) -> tuple[NegativeSpaceRule, ...]:
    """Return the registered rules, optionally only the enabled ones."""
    if enabled_only:
        return tuple(rule for rule in REGISTRY if rule.enabled)
    return REGISTRY
