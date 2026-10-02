"""Unit tests for negative-space detection rules.

Each test builds a small, hand-constructed in-memory database so a single rule's
positive, negative, boundary, no-baseline, and threshold behaviours can be
exercised in isolation. The synthetic-dataset integration coverage lives in
``test_negative_space_integration.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.negative_space import (
    NegativeSpaceConfig,
    run_negative_space_detection,
)
from app.db.base import Base
from app.models import Asset, SocEntity, TelemetryRecord
from app.models.enums import (
    AssetCategory,
    Criticality,
    EntityScale,
    Sector,
    TelemetryCategory,
    TelemetrySourceStatus,
)

WINDOW_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
WINDOW_END = datetime(2024, 7, 1, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial pragma
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def add_entity(s, entity_id="ENT-01", start=WINDOW_START, end=WINDOW_END):
    s.add(
        SocEntity(
            entity_id=entity_id,
            name=f"Entity {entity_id}",
            sector=Sector.TECHNOLOGY.value,
            peer_group="tech-mid",
            scale=EntityScale.MEDIUM.value,
            asset_count_estimate=10,
            analyst_headcount=5,
            created_at=start,
            data_period_start=start,
            data_period_end=end,
        )
    )
    s.flush()


def add_asset(
    s,
    asset_id,
    entity_id="ENT-01",
    criticality=Criticality.CRITICAL,
    monitoring_expected=True,
    category=AssetCategory.DATABASE,
    expected_telemetry=TelemetryCategory.FILE_INTEGRITY,
):
    s.add(
        Asset(
            asset_id=asset_id,
            entity_id=entity_id,
            name=f"Asset {asset_id}",
            category=category.value,
            criticality=criticality.value,
            monitoring_expected=monitoring_expected,
            expected_telemetry=expected_telemetry.value if expected_telemetry else None,
            created_at=WINDOW_START,
        )
    )
    s.flush()


def add_telemetry(s, telemetry_id, asset_id, period_start, period_end, entity_id="ENT-01"):
    s.add(
        TelemetryRecord(
            telemetry_id=telemetry_id,
            entity_id=entity_id,
            asset_id=asset_id,
            period_start=period_start,
            period_end=period_end,
            category=TelemetryCategory.FILE_INTEGRITY.value,
            event_count=100,
            activity_level=0.5,
            source_status=TelemetrySourceStatus.HEALTHY.value,
            expected=True,
        )
    )
    s.flush()


def months(start, n):
    """Return (start, end) for the n-th ~monthly period from ``start``."""
    ps = start + timedelta(days=30 * n)
    return ps, ps + timedelta(days=30)


# ---------------------------------------------------------------------------
# NS-001 — critical asset without telemetry (monitoring coverage gap)
# ---------------------------------------------------------------------------


def test_ns001_positive_critical_no_telemetry(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.CRITICAL)
    session.commit()
    result = run_negative_space_detection(session)
    ns001 = [f for f in result.findings if f.rule_id == "NS-001"]
    assert {f.asset_id for f in ns001} == {"AST-01"}
    f = ns001[0]
    assert f.reason_code.value == "CRITICAL_ASSET_NO_TELEMETRY"
    assert f.observation_start == WINDOW_START and f.observation_end == WINDOW_END
    assert f.asset_criticality == Criticality.CRITICAL.value


def test_ns001_negative_asset_with_telemetry(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.CRITICAL)
    ps, pe = months(WINDOW_START, 0)
    add_telemetry(session, "TLM-01", "AST-01", ps, pe)
    session.commit()
    result = run_negative_space_detection(session)
    assert not [f for f in result.findings if f.rule_id == "NS-001"]


def test_ns001_negative_below_criticality_threshold(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.HIGH)
    session.commit()
    result = run_negative_space_detection(session)
    assert not [f for f in result.findings if f.rule_id == "NS-001"]


def test_ns001_negative_not_monitoring_expected(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.CRITICAL, monitoring_expected=False)
    session.commit()
    result = run_negative_space_detection(session)
    assert not [f for f in result.findings if f.rule_id == "NS-001"]


def test_ns001_window_too_short_not_flagged(session):
    short_end = WINDOW_START + timedelta(hours=1)
    add_entity(session, end=short_end)
    add_asset(session, "AST-01", criticality=Criticality.CRITICAL)
    session.commit()
    # Default monitoring_min_observation_hours = 24h; a 1h window is too short.
    result = run_negative_space_detection(session)
    assert not [f for f in result.findings if f.rule_id == "NS-001"]


def test_ns001_configurable_criticality_floor(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.HIGH)
    session.commit()
    cfg = NegativeSpaceConfig(monitoring_min_criticality=Criticality.HIGH)
    result = run_negative_space_detection(session, config=cfg)
    assert {f.asset_id for f in result.findings if f.rule_id == "NS-001"} == {"AST-01"}


# ---------------------------------------------------------------------------
# NS-002 — telemetry disappearance (continuity gap)
# ---------------------------------------------------------------------------


def test_ns002_positive_disappearance(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.HIGH)
    for i in range(3):  # periods 0,1,2 then silence to WINDOW_END
        ps, pe = months(WINDOW_START, i)
        add_telemetry(session, f"TLM-{i}", "AST-01", ps, pe)
    session.commit()
    result = run_negative_space_detection(session)
    ns002 = [f for f in result.findings if f.rule_id == "NS-002"]
    assert {f.asset_id for f in ns002} == {"AST-01"}
    f = ns002[0]
    assert f.reason_code.value == "TELEMETRY_CONTINUITY_GAP"
    assert f.baseline_observation_count == 3
    assert f.last_evidence_at == months(WINDOW_START, 2)[1]
    assert f.silence_hours is not None and f.silence_hours > 0
    assert set(f.related_telemetry_ids) == {"TLM-0", "TLM-1", "TLM-2"}


def test_ns002_negative_telemetry_through_window_end(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.HIGH)
    # Last period ends exactly at the window end -> zero trailing silence.
    add_telemetry(session, "TLM-0", "AST-01", WINDOW_START, WINDOW_START + timedelta(days=30))
    add_telemetry(session, "TLM-1", "AST-01", WINDOW_END - timedelta(days=30), WINDOW_END)
    session.commit()
    result = run_negative_space_detection(session)
    assert not [f for f in result.findings if f.rule_id == "NS-002"]


def test_ns002_no_baseline_not_disappearance(session):
    # Zero telemetry: a total monitoring gap (NS-001 territory), never NS-002.
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.HIGH)
    session.commit()
    result = run_negative_space_detection(session)
    assert not [f for f in result.findings if f.rule_id == "NS-002"]


def test_ns002_single_record_below_min_baseline(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.HIGH)
    ps, pe = months(WINDOW_START, 0)
    add_telemetry(session, "TLM-0", "AST-01", ps, pe)  # only 1 record < default 2
    session.commit()
    result = run_negative_space_detection(session)
    assert not [f for f in result.findings if f.rule_id == "NS-002"]


def test_ns002_threshold_boundary(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.HIGH)
    last_end = WINDOW_END - timedelta(hours=100)
    add_telemetry(session, "TLM-0", "AST-01", last_end - timedelta(days=60), last_end - timedelta(days=30))
    add_telemetry(session, "TLM-1", "AST-01", last_end - timedelta(days=30), last_end)
    session.commit()
    # Silence is exactly 100h.
    exact = NegativeSpaceConfig(telemetry_gap_threshold_hours=100.0)
    assert [f for f in run_negative_space_detection(session, config=exact).findings if f.rule_id == "NS-002"]
    just_above = NegativeSpaceConfig(telemetry_gap_threshold_hours=100.001)
    assert not [f for f in run_negative_space_detection(session, config=just_above).findings if f.rule_id == "NS-002"]
    just_below = NegativeSpaceConfig(telemetry_gap_threshold_hours=99.999)
    assert [f for f in run_negative_space_detection(session, config=just_below).findings if f.rule_id == "NS-002"]


# ---------------------------------------------------------------------------
# ownership, multi-asset/entity, determinism, read-only, config validation
# ---------------------------------------------------------------------------


def test_rules_are_disjoint_per_asset(session):
    # An asset either has zero telemetry (NS-001) or a baseline (NS-002), never
    # both. Build one of each and confirm no asset is double-reported.
    add_entity(session)
    add_asset(session, "AST-NONE", criticality=Criticality.CRITICAL)  # NS-001
    add_asset(session, "AST-GONE", criticality=Criticality.CRITICAL)  # NS-002
    for i in range(3):
        ps, pe = months(WINDOW_START, i)
        add_telemetry(session, f"TLM-{i}", "AST-GONE", ps, pe)
    session.commit()
    result = run_negative_space_detection(session)
    by_asset = {}
    for f in result.findings:
        by_asset.setdefault(f.asset_id, set()).add(f.rule_id)
    assert by_asset["AST-NONE"] == {"NS-001"}
    assert by_asset["AST-GONE"] == {"NS-002"}


def test_multiple_entities_scoped(session):
    add_entity(session, "ENT-01")
    add_entity(session, "ENT-02")
    add_asset(session, "AST-01", entity_id="ENT-01", criticality=Criticality.CRITICAL)
    add_asset(session, "AST-02", entity_id="ENT-02", criticality=Criticality.CRITICAL)
    session.commit()
    full = run_negative_space_detection(session)
    assert {f.asset_id for f in full.findings} == {"AST-01", "AST-02"}
    scoped = run_negative_space_detection(session, entity_ids=["ENT-01"])
    assert {f.asset_id for f in scoped.findings} == {"AST-01"}


def test_deterministic_and_unique_keys(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.CRITICAL)
    session.commit()
    first = run_negative_space_detection(session)
    second = run_negative_space_detection(session)
    assert first == second
    keys = [f.finding_key for f in first.findings]
    assert len(keys) == len(set(keys))


def test_read_only(session):
    add_entity(session)
    add_asset(session, "AST-01", criticality=Criticality.CRITICAL)
    for i in range(3):
        ps, pe = months(WINDOW_START, i)
        add_telemetry(session, f"TLM-{i}", "AST-01", ps, pe)
    session.commit()
    models = (SocEntity, Asset, TelemetryRecord)
    before = {m.__name__: session.scalar(select(func.count()).select_from(m)) for m in models}
    run_negative_space_detection(session)
    run_negative_space_detection(session)
    after = {m.__name__: session.scalar(select(func.count()).select_from(m)) for m in models}
    assert before == after


def test_empty_database_no_error(session):
    result = run_negative_space_detection(session)
    assert result.finding_count == 0


def test_config_validation():
    with pytest.raises(ValueError):
        NegativeSpaceConfig(telemetry_gap_threshold_hours=-1.0)
    with pytest.raises(ValueError):
        NegativeSpaceConfig(disappearance_min_baseline_periods=0)
    with pytest.raises(ValueError):
        NegativeSpaceConfig(monitoring_min_observation_hours=-1.0)
