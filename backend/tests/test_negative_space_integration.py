"""Integration tests for negative-space detection against the synthetic dataset.

These tests load the full deterministic synthetic dataset (default seed) into an
in-memory database and run the detector end to end. The ``ground_truth`` manifest
is used ONLY here, as an evaluation oracle, to confirm that planted scenarios are
detected and labelled negative controls are not. Production detection code never
reads ground truth.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.negative_space import run_negative_space_detection
from app.datagen.generator import Dataset
from app.datagen.scenarios import ScenarioType
from app.db.base import Base
from app.models import (
    Alert,
    Asset,
    Escalation,
    Investigation,
    InvestigationAction,
    PerformanceMetric,
    Remediation,
    SocEntity,
    TelemetryRecord,
)

_LOAD_ORDER = [
    ("entities", SocEntity),
    ("assets", Asset),
    ("alerts", Alert),
    ("investigations", Investigation),
    ("investigation_actions", InvestigationAction),
    ("escalations", Escalation),
    ("remediations", Remediation),
    ("telemetry", TelemetryRecord),
    ("performance_metrics", PerformanceMetric),
]


@pytest.fixture()
def loaded_session(generated_dataset: Dataset):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # pragma: no cover - trivial pragma
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = Session(engine)
    for table, model in _LOAD_ORDER:
        session.add_all(model(**row) for row in generated_dataset.tables[table])
        session.flush()
    session.commit()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _gt(dataset: Dataset, scenario_type: str) -> list[dict]:
    return [g for g in dataset.ground_truth if g["scenario_type"] == scenario_type]


def _flagged_asset_ids(result) -> set[str]:
    return {f.asset_id for f in result.findings if f.asset_id}


# ----- coverage: planted scenarios are detected ----------------------------


def test_missing_telemetry_scenarios_detected(loaded_session, generated_dataset) -> None:
    result = run_negative_space_detection(loaded_session)
    detected = {f.asset_id for f in result.findings if f.rule_id == "NS-001"}
    expected = {g["asset_id"] for g in _gt(generated_dataset, ScenarioType.MISSING_TELEMETRY_CRITICAL_ASSET)}
    assert expected, "fixture sanity: scenario should exist in ground truth"
    assert expected <= detected


def test_disappearance_scenarios_detected(loaded_session, generated_dataset) -> None:
    result = run_negative_space_detection(loaded_session)
    detected = {f.asset_id for f in result.findings if f.rule_id == "NS-002"}
    expected = {g["asset_id"] for g in _gt(generated_dataset, ScenarioType.TELEMETRY_DISAPPEARANCE)}
    assert expected
    assert expected <= detected


# ----- negative controls and no-flood --------------------------------------


def test_normal_baseline_controls_not_flagged(loaded_session, generated_dataset) -> None:
    result = run_negative_space_detection(loaded_session)
    flagged = _flagged_asset_ids(result)
    controls = {g["asset_id"] for g in _gt(generated_dataset, ScenarioType.NORMAL_BASELINE)}
    assert controls
    assert controls.isdisjoint(flagged)


def test_disappearance_not_classified_as_missing(loaded_session, generated_dataset) -> None:
    # Assets with a prior baseline then silence must be continuity gaps (NS-002),
    # never total monitoring gaps (NS-001) — and vice versa. Ownership is disjoint.
    result = run_negative_space_detection(loaded_session)
    ns001 = {f.asset_id for f in result.findings if f.rule_id == "NS-001"}
    ns002 = {f.asset_id for f in result.findings if f.rule_id == "NS-002"}
    assert ns001.isdisjoint(ns002)
    disappearance = {g["asset_id"] for g in _gt(generated_dataset, ScenarioType.TELEMETRY_DISAPPEARANCE)}
    assert disappearance.isdisjoint(ns001)


def test_no_baseline_assets_not_flagged_as_disappearance(loaded_session) -> None:
    # Every NS-002 finding must rest on a real prior baseline (>= 2 records); an
    # asset that was never observed is never called a disappearance.
    result = run_negative_space_detection(loaded_session)
    for f in result.findings:
        if f.rule_id == "NS-002":
            assert f.baseline_observation_count is not None
            assert f.baseline_observation_count >= 2
            assert len(f.related_telemetry_ids) == f.baseline_observation_count


def test_findings_are_not_a_flood(loaded_session, generated_dataset) -> None:
    result = run_negative_space_detection(loaded_session)
    asset_count = len(generated_dataset.tables["assets"])
    assert result.finding_count < asset_count * 0.10


# ----- determinism, wall-clock independence, read-only ---------------------


def test_detection_is_deterministic(loaded_session) -> None:
    first = run_negative_space_detection(loaded_session)
    second = run_negative_space_detection(loaded_session)
    assert first == second
    assert [f.finding_key for f in first.findings] == [f.finding_key for f in second.findings]


def test_windows_anchored_to_data_horizon_not_wall_clock(loaded_session, generated_dataset) -> None:
    # All observation windows come from entity data-period bounds / observed
    # telemetry, so results cannot shift with the system clock. Confirm every
    # finding's window end equals its entity's recorded data_period_end.
    entities = {e["entity_id"]: e for e in generated_dataset.tables["entities"]}
    result = run_negative_space_detection(loaded_session)
    assert result.findings
    for f in result.findings:
        horizon = entities[f.entity_id]["data_period_end"]
        assert f.observation_end == horizon


def test_detection_read_only(loaded_session) -> None:
    models = (SocEntity, Asset, TelemetryRecord, Alert)
    before = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}
    run_negative_space_detection(loaded_session)
    run_negative_space_detection(loaded_session)
    after = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}
    assert before == after


def test_findings_reference_real_records(loaded_session) -> None:
    result = run_negative_space_detection(loaded_session)
    for f in result.findings:
        if f.asset_id:
            assert loaded_session.get(Asset, f.asset_id) is not None
        for tlm_id in f.related_telemetry_ids:
            assert loaded_session.get(TelemetryRecord, tlm_id) is not None


# ----- coexistence with Phase 4 --------------------------------------------


def test_coexists_with_execution_gap_without_mutation(loaded_session) -> None:
    ns_before = run_negative_space_detection(loaded_session)
    eg = run_execution_gap_detection(loaded_session)
    ns_after = run_negative_space_detection(loaded_session)
    assert ns_before == ns_after
    # The two analytics report distinct asset/record scopes; neither alters the
    # other's result. Execution-gap findings exist independently.
    assert eg.finding_count >= 0
