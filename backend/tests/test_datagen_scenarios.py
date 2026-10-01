"""Each planted ground-truth scenario is backed by real record relationships."""

from __future__ import annotations

import pytest

from app.datagen.generator import Dataset
from app.datagen.scenarios import SCENARIO_DETECTION, DetectionCategory, ScenarioType


@pytest.fixture()
def idx(generated_dataset: Dataset):
    t = generated_dataset.tables
    return {
        "alerts": {a["alert_id"]: a for a in t["alerts"]},
        "assets": {a["asset_id"]: a for a in t["assets"]},
        "entities": {e["entity_id"]: e for e in t["entities"]},
        "inv": {i["investigation_id"]: i for i in t["investigations"]},
        "inv_by_alert": {i["alert_id"]: i for i in t["investigations"]},
        "actions_by_inv": _group(t["investigation_actions"], "investigation_id"),
        "esc": t["escalations"],
        "rem": t["remediations"],
        "tel_by_asset": _group(t["telemetry"], "asset_id"),
        "alerts_by_rkey": _group(t["alerts"], "recurrence_key"),
        "pm_by_entity": _group(t["performance_metrics"], "entity_id"),
    }


def _group(rows, key):
    out: dict = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return out


def test_all_scenario_types_present(generated_dataset: Dataset) -> None:
    present = {g["scenario_type"] for g in generated_dataset.ground_truth}
    for name in vars(ScenarioType):
        if name.isupper():
            assert getattr(ScenarioType, name) in present


def test_detection_category_matches_type(generated_dataset: Dataset) -> None:
    for g in generated_dataset.ground_truth:
        assert g["expected_detection_category"] == SCENARIO_DETECTION[g["scenario_type"]]


def test_ground_truth_references_exist(generated_dataset: Dataset, idx) -> None:
    for g in generated_dataset.ground_truth:
        if g["entity_id"]:
            assert g["entity_id"] in idx["entities"]
        if g["asset_id"]:
            assert g["asset_id"] in idx["assets"]
        if g["alert_id"]:
            assert g["alert_id"] in idx["alerts"]
        if g["investigation_id"]:
            assert g["investigation_id"] in idx["inv"]


def test_critical_without_escalation(generated_dataset: Dataset, idx) -> None:
    esc_alert_ids = {e["alert_id"] for e in idx["esc"]}
    esc_inv_ids = {e["investigation_id"] for e in idx["esc"]}
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.CRITICAL_ALERT_WITHOUT_ESCALATION]
    assert rows
    for g in rows:
        alert = idx["alerts"][g["alert_id"]]
        assert alert["severity"] == "CRITICAL"
        assert g["alert_id"] not in esc_alert_ids
        assert g["investigation_id"] not in esc_inv_ids


def test_acknowledged_without_investigation(generated_dataset: Dataset, idx) -> None:
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.ACKNOWLEDGED_WITHOUT_INVESTIGATION]
    assert rows
    for g in rows:
        alert = idx["alerts"][g["alert_id"]]
        assert alert["acknowledged_at"] is not None
        assert alert["status"] == "ACKNOWLEDGED"
        assert g["alert_id"] not in idx["inv_by_alert"]


def test_fast_investigation(generated_dataset: Dataset, idx) -> None:
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.SUSPICIOUSLY_FAST_INVESTIGATION]
    assert rows
    for g in rows:
        inv = idx["inv"][g["investigation_id"]]
        assert inv["status"] == "CLOSED"
        assert inv["duration_seconds"] is not None and inv["duration_seconds"] < 180
        assert inv["evidence_count"] <= 1


def test_recurring_without_remediation(generated_dataset: Dataset, idx) -> None:
    completed_alert = {r["alert_id"] for r in idx["rem"] if r["status"] == "COMPLETED"}
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.RECURRING_ALERTS_WITHOUT_REMEDIATION]
    assert rows
    for g in rows:
        group = idx["alerts_by_rkey"][g["recurrence_key"]]
        assert len(group) >= 3
        for alert in group:
            assert alert["alert_id"] not in completed_alert


def test_missing_telemetry(generated_dataset: Dataset, idx) -> None:
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.MISSING_TELEMETRY_CRITICAL_ASSET]
    assert rows
    for g in rows:
        asset = idx["assets"][g["asset_id"]]
        assert asset["monitoring_expected"] is True
        assert asset["criticality"] == "CRITICAL"
        assert g["asset_id"] not in idx["tel_by_asset"]


def test_telemetry_disappearance(generated_dataset: Dataset, idx) -> None:
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.TELEMETRY_DISAPPEARANCE]
    assert rows
    for g in rows:
        tel = idx["tel_by_asset"][g["asset_id"]]
        before = [r for r in tel if r["period_start"] < g["period_start"]]
        after = [r for r in tel if r["period_start"] >= g["period_start"]]
        assert before, "should have baseline telemetry before disappearance"
        assert not after, "should have no telemetry after disappearance point"


def test_repetitive_workflow(generated_dataset: Dataset, idx) -> None:
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.REPETITIVE_INVESTIGATION_WORKFLOW]
    assert len(rows) >= 2
    sequences = []
    for g in rows:
        acts = sorted(idx["actions_by_inv"][g["investigation_id"]],
                      key=lambda a: a["sequence_number"])
        sequences.append(tuple(a["action_type"] for a in acts))
    # Every planted investigation shares the identical action sequence.
    assert len(set(sequences)) == 1


def test_metric_divergence(generated_dataset: Dataset, idx) -> None:
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.METRIC_RISK_DIVERGENCE]
    assert rows
    for g in rows:
        metrics = sorted(idx["pm_by_entity"][g["entity_id"]], key=lambda m: m["period_start"])
        first, last = metrics[0], metrics[-1]
        # Headline KPIs improve.
        assert last["closure_rate"] > first["closure_rate"]
        assert last["sla_compliance"] > first["sla_compliance"]
        assert last["mttr_hours"] < first["mttr_hours"]
        # Operational-quality indicators deteriorate.
        assert last["evidence_completeness"] < first["evidence_completeness"]
        assert last["investigation_completeness"] < first["investigation_completeness"]
        assert last["recurrence_rate"] > first["recurrence_rate"]
        assert last["remediation_rate"] < first["remediation_rate"]


def test_normal_baseline_are_negative_controls(generated_dataset: Dataset, idx) -> None:
    esc_inv_ids = {e["investigation_id"] for e in idx["esc"]}
    rows = [g for g in generated_dataset.ground_truth
            if g["scenario_type"] == ScenarioType.NORMAL_BASELINE]
    assert rows
    for g in rows:
        assert g["expected_detection_category"] == DetectionCategory.NONE
        inv = idx["inv"][g["investigation_id"]]
        assert inv["status"] == "CLOSED"
        assert g["investigation_id"] in esc_inv_ids
