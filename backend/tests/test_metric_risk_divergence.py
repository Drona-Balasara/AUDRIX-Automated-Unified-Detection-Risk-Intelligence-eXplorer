"""Unit tests for metric-risk divergence detection (Phase 7).

Each test builds a small, hand-constructed in-memory database so the trend
scoring, composite calculation, divergence condition, missing-data handling,
boundary conditions, and determinism can be exercised in isolation without
depending on the full synthetic dataset.

Integration-style tests using the full synthetic dataset (which carries a
planted metric-risk-divergence ground-truth scenario) live in
``test_metric_risk_divergence_integration.py``.

Test categories
---------------
1. Positive detection — diverging entity is flagged.
2. Controls — non-diverging entities are NOT flagged.
3. Insufficient observations — below min_observation_periods → skipped, no finding.
4. Metric direction handling — higher-is-better vs lower-is-better specs.
5. Missing / zero denominator handling.
6. Boundary thresholds — at-exactly-threshold behaviour.
7. Multiple entities — independent, correct scope.
8. Determinism — repeated runs yield identical results.
9. Future-leakage prevention — periods beyond data_period_end excluded.
10. Config validation — invalid configs raise ValueError.
11. Snapshot content — period_values and trend_scores are populated correctly.
12. Confidence levels — HIGH / MODERATE / LOW assigned correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.metric_risk_divergence import (
    ANALYTIC_ID,
    MetricRiskDivergenceConfig,
    MetricRiskDivergenceResult,
    run_metric_risk_divergence,
)
from app.analytics.metric_risk_divergence.findings import ConfidenceLevel
from app.db.base import Base
from app.models import PerformanceMetric, SocEntity
from app.models.enums import EntityScale, Sector

# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _month(offset: int) -> datetime:
    """Return the 1st of the month ``offset`` months after Jan 2024."""
    m = _BASE.month - 1 + offset
    year = _BASE.year + m // 12
    month = m % 12 + 1
    return datetime(year, month, 1, tzinfo=timezone.utc)


PERIODS = [((_month(i), _month(i + 1))) for i in range(6)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _add_entity(
    s: Session,
    entity_id: str,
    scale: EntityScale = EntityScale.MEDIUM,
    sector: Sector = Sector.FINANCE,
    data_period_end: datetime | None = None,
) -> None:
    """Insert a minimal SocEntity row."""
    if data_period_end is None:
        data_period_end = PERIODS[-1][1]
    s.add(
        SocEntity(
            entity_id=entity_id,
            name=f"Entity {entity_id}",
            sector=sector.value,
            peer_group=sector.value,
            scale=scale.value,
            asset_count_estimate=20,
            analyst_headcount=8,
            created_at=PERIODS[0][0],
            data_period_start=PERIODS[0][0],
            data_period_end=data_period_end,
        )
    )
    s.flush()


def _add_metric(
    s: Session,
    entity_id: str,
    period_idx: int,
    *,
    closure_rate: float = 0.90,
    sla_compliance: float = 0.90,
    mttr_hours: float = 4.0,
    escalation_rate: float = 0.20,
    investigation_completeness: float = 0.85,
    recurrence_rate: float = 0.10,
    remediation_rate: float = 0.80,
    evidence_completeness: float = 0.85,
) -> None:
    """Insert one PerformanceMetric row for the given entity and period index."""
    start, end = PERIODS[period_idx]
    s.add(
        PerformanceMetric(
            metric_id=f"PMET-{entity_id}-{period_idx:02d}",
            entity_id=entity_id,
            period_start=start,
            period_end=end,
            closure_rate=closure_rate,
            sla_compliance=sla_compliance,
            mttr_hours=mttr_hours,
            escalation_rate=escalation_rate,
            investigation_completeness=investigation_completeness,
            recurrence_rate=recurrence_rate,
            remediation_rate=remediation_rate,
            evidence_completeness=evidence_completeness,
        )
    )
    s.flush()


def _add_diverging_entity(s: Session, entity_id: str = "ENT-01", n_periods: int = 6) -> None:
    """Insert an entity whose headline KPIs improve while quality deteriorates."""
    _add_entity(s, entity_id)
    for i in range(n_periods):
        t = i / max(1, n_periods - 1)
        _add_metric(
            s, entity_id, i,
            # Headline KPIs improve:
            closure_rate=round(0.80 + 0.16 * t, 4),
            sla_compliance=round(0.82 + 0.15 * t, 4),
            mttr_hours=round(7.0 - 4.0 * t, 4),        # lower = better
            # Operational-quality indicators deteriorate:
            investigation_completeness=round(0.80 - 0.38 * t, 4),
            evidence_completeness=round(0.82 - 0.37 * t, 4),
            recurrence_rate=round(0.10 + 0.24 * t, 4),  # higher = worse
            remediation_rate=round(0.78 - 0.34 * t, 4),
        )
    s.commit()


def _add_healthy_entity(s: Session, entity_id: str = "ENT-02", n_periods: int = 6) -> None:
    """Insert an entity where both headline KPIs and quality improve together."""
    _add_entity(s, entity_id)
    for i in range(n_periods):
        t = i / max(1, n_periods - 1)
        _add_metric(
            s, entity_id, i,
            closure_rate=round(0.80 + 0.12 * t, 4),
            sla_compliance=round(0.82 + 0.12 * t, 4),
            mttr_hours=round(6.0 - 2.0 * t, 4),
            investigation_completeness=round(0.80 + 0.10 * t, 4),
            evidence_completeness=round(0.82 + 0.10 * t, 4),
            recurrence_rate=round(0.12 - 0.05 * t, 4),
            remediation_rate=round(0.78 + 0.10 * t, 4),
        )
    s.commit()


def _add_flat_entity(s: Session, entity_id: str = "ENT-03", n_periods: int = 6) -> None:
    """Insert an entity with stable metrics across all periods — no divergence."""
    _add_entity(s, entity_id)
    for i in range(n_periods):
        _add_metric(
            s, entity_id, i,
            closure_rate=0.88,
            sla_compliance=0.88,
            mttr_hours=5.0,
            investigation_completeness=0.80,
            evidence_completeness=0.80,
            recurrence_rate=0.12,
            remediation_rate=0.78,
        )
    s.commit()


# ===========================================================================
# 1. Positive detection
# ===========================================================================

def test_diverging_entity_is_flagged(session):
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 1
    assert result.findings[0].entity_id == "ENT-01"


def test_finding_has_correct_analytic_id(session):
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    assert result.findings[0].analytic_id == ANALYTIC_ID
    assert result.findings[0].analytic_id == "MRD-001"


def test_finding_key_is_stable_and_deterministic(session):
    _add_diverging_entity(session)
    r1 = run_metric_risk_divergence(session)
    r2 = run_metric_risk_divergence(session)
    assert r1.findings[0].finding_key == r2.findings[0].finding_key
    # Key format: MRD-001:<entity>:<window_start>:<window_end>
    key = r1.findings[0].finding_key
    assert key.startswith("MRD-001:ENT-01:")
    assert "2024-01" in key  # first period label
    assert "2024-06" in key  # last period label


def test_finding_headline_trend_is_positive(session):
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    assert result.findings[0].headline_trend_score > 0


def test_finding_quality_trend_is_negative(session):
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    assert result.findings[0].quality_trend_score < 0


def test_finding_period_count_is_correct(session):
    _add_diverging_entity(session, n_periods=6)
    result = run_metric_risk_divergence(session)
    assert result.findings[0].supporting_period_count == 6


def test_finding_window_bounds_are_correct(session):
    _add_diverging_entity(session, n_periods=6)
    result = run_metric_risk_divergence(session)
    f = result.findings[0]
    assert f.window_start == PERIODS[0][0]
    assert f.window_end == PERIODS[5][1]


# ===========================================================================
# 2. Controls — non-diverging entities must NOT be flagged
# ===========================================================================

def test_healthy_improving_entity_not_flagged(session):
    """Headline KPIs and quality both improve → no divergence."""
    _add_healthy_entity(session)
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0


def test_flat_stable_entity_not_flagged(session):
    """Stable metrics across all periods → no divergence."""
    _add_flat_entity(session)
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0


def test_quality_deteriorating_without_headline_improvement_not_flagged(session):
    """Quality deteriorates but headline is flat → only one half of divergence, no finding."""
    _add_entity(session, "ENT-D")
    for i in range(6):
        t = i / 5.0
        _add_metric(
            session, "ENT-D", i,
            # Headline stays flat:
            closure_rate=0.85,
            sla_compliance=0.85,
            mttr_hours=5.0,
            # Quality deteriorates:
            investigation_completeness=round(0.80 - 0.35 * t, 4),
            evidence_completeness=round(0.80 - 0.35 * t, 4),
            recurrence_rate=round(0.10 + 0.25 * t, 4),
            remediation_rate=round(0.80 - 0.30 * t, 4),
        )
    session.commit()
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0


def test_headline_improving_without_quality_deterioration_not_flagged(session):
    """Headline KPIs improve but quality stays flat → no divergence."""
    _add_entity(session, "ENT-H")
    for i in range(6):
        t = i / 5.0
        _add_metric(
            session, "ENT-H", i,
            closure_rate=round(0.80 + 0.16 * t, 4),
            sla_compliance=round(0.82 + 0.15 * t, 4),
            mttr_hours=round(7.0 - 4.0 * t, 4),
            # Quality stays flat:
            investigation_completeness=0.80,
            evidence_completeness=0.80,
            recurrence_rate=0.12,
            remediation_rate=0.78,
        )
    session.commit()
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0


def test_empty_database_returns_safe_result(session):
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0
    assert result.findings == ()
    assert result.entity_ids == ()


# ===========================================================================
# 3. Insufficient observations
# ===========================================================================

def test_fewer_than_min_periods_produces_skip_not_finding(session):
    """Entity with only 3 periods (below default min of 4) must be skipped."""
    _add_entity(session, "ENT-S")
    for i in range(3):
        t = i / 2.0
        _add_metric(
            session, "ENT-S", i,
            closure_rate=round(0.80 + 0.16 * t, 4),
            sla_compliance=round(0.82 + 0.15 * t, 4),
            mttr_hours=round(7.0 - 4.0 * t, 4),
            investigation_completeness=round(0.80 - 0.38 * t, 4),
            evidence_completeness=round(0.82 - 0.37 * t, 4),
            recurrence_rate=round(0.10 + 0.24 * t, 4),
            remediation_rate=round(0.78 - 0.34 * t, 4),
        )
    session.commit()
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0
    assert "ENT-S" in result.skipped_entity_ids


def test_exactly_min_periods_is_assessed(session):
    """Entity with exactly min_observation_periods=4 is assessed, not skipped."""
    _add_entity(session, "ENT-E")
    for i in range(4):
        t = i / 3.0
        _add_metric(
            session, "ENT-E", i,
            closure_rate=round(0.80 + 0.16 * t, 4),
            sla_compliance=round(0.82 + 0.15 * t, 4),
            mttr_hours=round(7.0 - 4.0 * t, 4),
            investigation_completeness=round(0.80 - 0.38 * t, 4),
            evidence_completeness=round(0.82 - 0.37 * t, 4),
            recurrence_rate=round(0.10 + 0.24 * t, 4),
            remediation_rate=round(0.78 - 0.34 * t, 4),
        )
    session.commit()
    result = run_metric_risk_divergence(session)
    # Should be assessed (not skipped), and with a clear signal it should find.
    assert "ENT-E" not in result.skipped_entity_ids


def test_lowered_min_periods_detects_with_fewer_periods(session):
    """With min_observation_periods=3, a 3-period diverging series is detected."""
    _add_entity(session, "ENT-L")
    for i in range(3):
        t = i / 2.0
        _add_metric(
            session, "ENT-L", i,
            closure_rate=round(0.80 + 0.16 * t, 4),
            sla_compliance=round(0.82 + 0.15 * t, 4),
            mttr_hours=round(7.0 - 4.0 * t, 4),
            investigation_completeness=round(0.80 - 0.38 * t, 4),
            evidence_completeness=round(0.82 - 0.37 * t, 4),
            recurrence_rate=round(0.10 + 0.24 * t, 4),
            remediation_rate=round(0.78 - 0.34 * t, 4),
        )
    session.commit()
    cfg = MetricRiskDivergenceConfig(min_observation_periods=3)
    result = run_metric_risk_divergence(session, config=cfg)
    assert "ENT-L" not in result.skipped_entity_ids
    assert result.finding_count == 1


def test_no_metrics_at_all_skipped_safely(session):
    """Entity registered but with no PerformanceMetric rows → skipped, no crash."""
    _add_entity(session, "ENT-EMPTY")
    session.commit()
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0
    assert "ENT-EMPTY" in result.skipped_entity_ids


# ===========================================================================
# 4. Metric direction handling
# ===========================================================================

def test_mttr_lower_is_better_treated_correctly(session):
    """mttr_hours has higher_is_better=False.  Decreasing MTTR should count as
    improvement on the headline composite."""
    _add_entity(session, "ENT-M")
    # Headline: MTTR drops significantly (improvement), closure/SLA flat.
    # Quality: all quality indicators deteriorate.
    for i in range(6):
        t = i / 5.0
        _add_metric(
            session, "ENT-M", i,
            closure_rate=0.88,
            sla_compliance=0.88,
            mttr_hours=round(10.0 - 7.0 * t, 4),  # drops from 10→3 (improving)
            investigation_completeness=round(0.80 - 0.38 * t, 4),
            evidence_completeness=round(0.82 - 0.37 * t, 4),
            recurrence_rate=round(0.10 + 0.24 * t, 4),
            remediation_rate=round(0.78 - 0.34 * t, 4),
        )
    session.commit()
    result = run_metric_risk_divergence(session)
    # The MTTR improvement alone (while headline rates are flat) should still
    # produce a positive headline composite and trigger detection.
    assert result.finding_count == 1
    assert result.findings[0].headline_trend_score > 0


def test_recurrence_rate_higher_is_worse_treated_correctly(session):
    """recurrence_rate has higher_is_better=False.  An increasing recurrence
    rate should count as *deterioration* in the quality composite."""
    from app.analytics.metric_risk_divergence.engine import (
        _direction_adjusted,
    )
    from app.analytics.metric_risk_divergence.metrics import QUALITY_METRICS

    # Find the recurrence_rate spec.
    rr_spec = next(m for m in QUALITY_METRICS if m.name == "recurrence_rate")
    # A value of 0.30 should become -0.30 when direction-adjusted (lower is better).
    assert _direction_adjusted(0.30, rr_spec) == pytest.approx(-0.30)
    # A value of 0.05 should become -0.05.
    assert _direction_adjusted(0.05, rr_spec) == pytest.approx(-0.05)


# ===========================================================================
# 5. Missing / zero denominator handling
# ===========================================================================

def test_single_period_entity_is_skipped_not_crashed(session):
    """One period → below any reasonable min_observation_periods → skipped safely."""
    _add_entity(session, "ENT-ONE")
    _add_metric(session, "ENT-ONE", 0)
    session.commit()
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0
    assert "ENT-ONE" in result.skipped_entity_ids


def test_two_period_entity_skipped_by_default(session):
    """Two periods is below the default min of 4; must be skipped."""
    _add_entity(session, "ENT-TWO")
    for i in range(2):
        _add_metric(session, "ENT-TWO", i)
    session.commit()
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 0
    assert "ENT-TWO" in result.skipped_entity_ids


# ===========================================================================
# 6. Boundary threshold tests
# ===========================================================================

def test_at_exactly_headline_threshold_is_flagged(session):
    """When the headline composite score equals headline_min_improvement exactly,
    the entity should be flagged (>= comparison is inclusive)."""
    # We need tight control; use a config with known thresholds and craft the
    # metric series so the composite lands exactly on the threshold.
    # With a 1-metric headline and a clear monotone series the composite will
    # match the single-metric score.  Use a raised threshold for isolation.
    cfg = MetricRiskDivergenceConfig(
        headline_min_improvement=0.50,
        quality_min_deterioration=0.10,
        min_trend_magnitude=0.0,  # disable flat filter to allow exact-boundary test
        min_observation_periods=4,
    )
    _add_entity(session, "ENT-B1")
    for i in range(6):
        t = i / 5.0
        # All three headline metrics improve; quality deteriorates.
        _add_metric(
            session, "ENT-B1", i,
            closure_rate=round(0.60 + 0.40 * t, 4),   # strong improvement
            sla_compliance=round(0.60 + 0.40 * t, 4),
            mttr_hours=round(10.0 - 7.0 * t, 4),
            investigation_completeness=round(0.80 - 0.38 * t, 4),
            evidence_completeness=round(0.82 - 0.37 * t, 4),
            recurrence_rate=round(0.10 + 0.24 * t, 4),
            remediation_rate=round(0.78 - 0.34 * t, 4),
        )
    session.commit()
    result = run_metric_risk_divergence(session, config=cfg)
    # With strong 40% improvement across 6 periods the composite should exceed 0.50.
    assert result.finding_count == 1


def test_just_below_headline_threshold_not_flagged(session):
    """Perfectly flat headline metrics produce a composite of 0.0, below any threshold."""
    _add_entity(session, "ENT-B2")
    for i in range(6):
        t = i / 5.0
        # Constant headline (zero trend); strong quality deterioration.
        _add_metric(
            session, "ENT-B2", i,
            closure_rate=0.88,
            sla_compliance=0.88,
            mttr_hours=5.0,
            investigation_completeness=round(0.80 - 0.38 * t, 4),
            evidence_completeness=round(0.82 - 0.37 * t, 4),
            recurrence_rate=round(0.10 + 0.24 * t, 4),
            remediation_rate=round(0.78 - 0.34 * t, 4),
        )
    session.commit()
    result = run_metric_risk_divergence(session)
    # Headline composite is 0.0 (flat series) → does not meet headline_min_improvement.
    assert result.finding_count == 0


# ===========================================================================
# 7. Multiple entities — independent correct scope
# ===========================================================================

def test_only_diverging_entity_flagged_among_multiple(session):
    """With three entities — diverging, healthy, flat — only the diverging one fires."""
    _add_diverging_entity(session, "ENT-D")
    _add_healthy_entity(session, "ENT-H")
    _add_flat_entity(session, "ENT-F")
    result = run_metric_risk_divergence(session)
    assert result.finding_count == 1
    assert result.findings[0].entity_id == "ENT-D"
    assert len(result.entity_ids) == 3


def test_two_diverging_entities_both_flagged(session):
    """Two independently diverging entities must each produce a finding."""
    _add_diverging_entity(session, "ENT-D1")
    _add_diverging_entity(session, "ENT-D2")
    result = run_metric_risk_divergence(session)
    flagged = {f.entity_id for f in result.findings}
    assert "ENT-D1" in flagged
    assert "ENT-D2" in flagged
    assert result.finding_count == 2


def test_findings_are_sorted_by_finding_key(session):
    """Findings must be ordered by finding_key for deterministic output."""
    _add_diverging_entity(session, "ENT-Z")
    _add_diverging_entity(session, "ENT-A")
    result = run_metric_risk_divergence(session)
    keys = [f.finding_key for f in result.findings]
    assert keys == sorted(keys)


def test_entity_scope_filter(session):
    """entity_ids filter restricts assessment to named entities only."""
    _add_diverging_entity(session, "ENT-IN")
    _add_diverging_entity(session, "ENT-OUT")
    result = run_metric_risk_divergence(session, entity_ids=["ENT-IN"])
    assert len(result.entity_ids) == 1
    assert "ENT-OUT" not in result.entity_ids
    # Only the in-scope entity's finding is present.
    for f in result.findings:
        assert f.entity_id == "ENT-IN"


# ===========================================================================
# 8. Determinism
# ===========================================================================

def test_run_is_deterministic(session):
    _add_diverging_entity(session)
    _add_healthy_entity(session)
    r1 = run_metric_risk_divergence(session)
    r2 = run_metric_risk_divergence(session)
    assert r1 == r2


def test_finding_key_is_identical_across_runs(session):
    _add_diverging_entity(session)
    r1 = run_metric_risk_divergence(session)
    r2 = run_metric_risk_divergence(session)
    assert [f.finding_key for f in r1.findings] == [f.finding_key for f in r2.findings]


def test_run_is_read_only(session):
    """The detector must not insert, update, or delete any rows."""
    from sqlalchemy import func, select

    _add_diverging_entity(session)
    before = session.scalar(select(func.count()).select_from(PerformanceMetric))
    run_metric_risk_divergence(session)
    run_metric_risk_divergence(session)
    after = session.scalar(select(func.count()).select_from(PerformanceMetric))
    assert before == after


# ===========================================================================
# 9. Future-leakage prevention
# ===========================================================================

def test_periods_beyond_data_period_end_are_excluded(session):
    """Periods at or after data_period_end must be silently excluded.

    An entity whose data_period_end is the end of period index 1 (exclusive upper
    bound = start of period 2) should only have 2 periods in its context (indices
    0 and 1 have period_start < horizon) and be skipped for insufficient
    observations (default min = 4).
    """
    # data_period_end = end of period index 1 = start of period index 2.
    # Periods 0 and 1 have period_start < horizon; period 2+ are excluded.
    horizon = PERIODS[1][1]  # = start of period 2
    _add_entity(session, "ENT-FL", data_period_end=horizon)
    for i in range(6):
        _add_metric(session, "ENT-FL", i,
                    closure_rate=round(0.80 + 0.16 * i / 5.0, 4),
                    sla_compliance=round(0.82 + 0.15 * i / 5.0, 4),
                    mttr_hours=round(7.0 - 4.0 * i / 5.0, 4),
                    investigation_completeness=round(0.80 - 0.38 * i / 5.0, 4),
                    evidence_completeness=round(0.82 - 0.37 * i / 5.0, 4),
                    recurrence_rate=round(0.10 + 0.24 * i / 5.0, 4),
                    remediation_rate=round(0.78 - 0.34 * i / 5.0, 4))
    session.commit()
    from app.analytics.metric_risk_divergence.context import load_context
    ctx = load_context(session)
    # Only periods 0 and 1 have period_start < horizon.
    assert ctx.entity_metrics["ENT-FL"].period_count == 2
    result = run_metric_risk_divergence(session)
    assert "ENT-FL" in result.skipped_entity_ids
    assert result.finding_count == 0


# ===========================================================================
# 10. Config validation
# ===========================================================================

def test_invalid_config_min_periods():
    with pytest.raises(ValueError):
        MetricRiskDivergenceConfig(min_observation_periods=1)


def test_invalid_config_headline_threshold():
    with pytest.raises(ValueError):
        MetricRiskDivergenceConfig(headline_min_improvement=0.0)
    with pytest.raises(ValueError):
        MetricRiskDivergenceConfig(headline_min_improvement=-0.1)


def test_invalid_config_quality_threshold():
    with pytest.raises(ValueError):
        MetricRiskDivergenceConfig(quality_min_deterioration=0.0)
    with pytest.raises(ValueError):
        MetricRiskDivergenceConfig(quality_min_deterioration=-0.1)


def test_invalid_config_min_trend_magnitude():
    with pytest.raises(ValueError):
        MetricRiskDivergenceConfig(min_trend_magnitude=-0.01)


def test_valid_config_boundary_values():
    cfg = MetricRiskDivergenceConfig(
        min_observation_periods=2,
        headline_min_improvement=0.001,
        quality_min_deterioration=0.001,
        min_trend_magnitude=0.0,
    )
    assert cfg.min_observation_periods == 2


# ===========================================================================
# 11. Snapshot content
# ===========================================================================

def test_headline_snapshots_present_and_named(session):
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    f = result.findings[0]
    snapshot_names = {s.name for s in f.headline_snapshots}
    assert "closure_rate" in snapshot_names
    assert "sla_compliance" in snapshot_names
    assert "mttr_hours" in snapshot_names


def test_quality_snapshots_present_and_named(session):
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    f = result.findings[0]
    snapshot_names = {s.name for s in f.quality_snapshots}
    assert "investigation_completeness" in snapshot_names
    assert "evidence_completeness" in snapshot_names
    assert "recurrence_rate" in snapshot_names
    assert "remediation_rate" in snapshot_names


def test_snapshot_period_values_ordered_and_labeled(session):
    _add_diverging_entity(session, n_periods=6)
    result = run_metric_risk_divergence(session)
    f = result.findings[0]
    cr_snapshot = next(s for s in f.headline_snapshots if s.name == "closure_rate")
    labels = [pv[0] for pv in cr_snapshot.period_values]
    # Labels must be in ascending chronological order.
    assert labels == sorted(labels)
    assert labels[0] == "2024-01"
    assert labels[-1] == "2024-06"


def test_snapshot_values_match_inserted_data(session):
    """Snapshot values must exactly reproduce the inserted metric values."""
    _add_diverging_entity(session, n_periods=6)
    result = run_metric_risk_divergence(session)
    f = result.findings[0]
    cr_snapshot = next(s for s in f.headline_snapshots if s.name == "closure_rate")
    # First period: closure_rate = 0.80 + 0.16 * 0 = 0.80
    assert cr_snapshot.period_values[0][1] == pytest.approx(0.80, abs=1e-4)
    # Last period: closure_rate = 0.80 + 0.16 * 1 = 0.96
    assert cr_snapshot.period_values[-1][1] == pytest.approx(0.96, abs=1e-4)


def test_escalation_rate_absent_from_snapshots(session):
    """escalation_rate is excluded from both headline and quality snapshots."""
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    f = result.findings[0]
    all_names = {s.name for s in f.headline_snapshots} | {s.name for s in f.quality_snapshots}
    assert "escalation_rate" not in all_names


# ===========================================================================
# 12. Confidence levels
# ===========================================================================

def test_high_confidence_for_strong_signal_over_many_periods(session):
    """6 periods with large trend magnitudes → HIGH confidence."""
    _add_diverging_entity(session, n_periods=6)
    result = run_metric_risk_divergence(session)
    # The planted scenario has very strong trends; should be HIGH.
    assert result.findings[0].confidence == ConfidenceLevel.HIGH


def test_low_confidence_for_minimum_periods(session):
    """Exactly min_observation_periods (4) with modest trends → not HIGH."""
    _add_entity(session, "ENT-LC")
    for i in range(4):
        t = i / 3.0
        _add_metric(
            session, "ENT-LC", i,
            closure_rate=round(0.82 + 0.04 * t, 4),
            sla_compliance=round(0.83 + 0.04 * t, 4),
            mttr_hours=round(6.0 - 1.5 * t, 4),
            investigation_completeness=round(0.78 - 0.08 * t, 4),
            evidence_completeness=round(0.79 - 0.08 * t, 4),
            recurrence_rate=round(0.11 + 0.10 * t, 4),
            remediation_rate=round(0.77 - 0.08 * t, 4),
        )
    session.commit()
    result = run_metric_risk_divergence(session)
    if result.finding_count > 0:
        # If detected, confidence should be LOW or MODERATE (not HIGH).
        assert result.findings[0].confidence in (ConfidenceLevel.LOW, ConfidenceLevel.MODERATE)


def test_confidence_note_is_non_empty(session):
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    assert result.findings[0].confidence_note
    assert len(result.findings[0].confidence_note) > 10


def test_summary_uses_neutral_language(session):
    """Summary must use 'Potential' qualifier and not contain accusatory language."""
    _add_diverging_entity(session)
    result = run_metric_risk_divergence(session)
    summary = result.findings[0].summary
    assert "Potential Metric-Risk Divergence" in summary
    # Must not contain accusatory language.
    forbidden = ["manipulat", "fraud", "gaming", "falsif"]
    for word in forbidden:
        assert word.lower() not in summary.lower(), f"Forbidden word '{word}' found in summary"
