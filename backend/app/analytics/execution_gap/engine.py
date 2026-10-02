"""Execution-gap detection engine.

The engine owns orchestration only: it runs the enabled rules against a
pre-loaded :class:`DetectionContext`, deduplicates findings by their stable
``finding_key`` (so re-running or overlapping rules cannot emit the same
condition twice), and returns a deterministically ordered, structured result.

The engine is pure and side-effect free: given the same context and config it
always produces an equal result. It performs no I/O, no logging, and no
database access — those belong to :mod:`app.analytics.execution_gap.service`.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.execution_gap.config import ExecutionGapConfig
from app.analytics.execution_gap.context import DetectionContext
from app.analytics.execution_gap.findings import ExecutionGapFinding
from app.analytics.execution_gap.rules import ExecutionGapRule, get_rules


class ExecutionGapResult(BaseModel):
    """Structured, deterministic outcome of one detection run.

    Deliberately carries no wall-clock timestamp: the result is defined purely
    by the input state and configuration, so two runs over identical data are
    equal. Run metadata such as duration is emitted via logging, not here.
    """

    model_config = ConfigDict(frozen=True)

    entity_ids: tuple[str, ...] = Field(default_factory=tuple)
    rule_ids: tuple[str, ...] = Field(default_factory=tuple)
    findings: tuple[ExecutionGapFinding, ...] = Field(default_factory=tuple)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    def counts_by_rule(self) -> dict[str, int]:
        """Finding counts keyed by rule id, for every rule that ran."""
        counts = Counter(f.rule_id for f in self.findings)
        return {rule_id: counts.get(rule_id, 0) for rule_id in self.rule_ids}

    def counts_by_gap_type(self) -> dict[str, int]:
        """Finding counts keyed by gap-type value."""
        return dict(Counter(f.gap_type.value for f in self.findings))


def run_rules(
    ctx: DetectionContext,
    config: ExecutionGapConfig,
    rules: tuple[ExecutionGapRule, ...] | None = None,
) -> ExecutionGapResult:
    """Evaluate ``rules`` (default: all enabled) against ``ctx``.

    Findings are deduplicated by ``finding_key`` and ordered deterministically
    by ``(rule_id, finding_key)`` so output does not depend on rule or row
    iteration order.
    """
    if rules is None:
        rules = get_rules(enabled_only=True)

    seen: set[str] = set()
    collected: list[ExecutionGapFinding] = []
    for rule in rules:
        for finding in rule.evaluate(ctx, config):
            if finding.finding_key in seen:
                continue
            seen.add(finding.finding_key)
            collected.append(finding)

    collected.sort(key=lambda f: (f.rule_id, f.finding_key))

    return ExecutionGapResult(
        entity_ids=tuple(ctx.entity_ids()),
        rule_ids=tuple(rule.rule_id for rule in rules),
        findings=tuple(collected),
    )
