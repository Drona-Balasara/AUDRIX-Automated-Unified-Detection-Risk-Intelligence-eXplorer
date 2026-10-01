"""Data-quality invariants for the synthetic dataset."""

from __future__ import annotations

from app.datagen.generator import Dataset
from app.datagen.scenarios import ScenarioType


def _ids(rows, key):
    return [r[key] for r in rows]


def test_entities_and_tables_populated(generated_dataset: Dataset) -> None:
    t = generated_dataset.tables
    assert len(t["entities"]) == 6
    for name in ("assets", "alerts", "investigations", "investigation_actions",
                 "escalations", "remediations", "telemetry", "performance_metrics"):
        assert len(t[name]) > 0, f"{name} should not be empty"


def test_primary_keys_unique(generated_dataset: Dataset) -> None:
    pk = {
        "entities": "entity_id",
        "assets": "asset_id",
        "alerts": "alert_id",
        "investigations": "investigation_id",
        "investigation_actions": "action_id",
        "escalations": "escalation_id",
        "remediations": "remediation_id",
        "telemetry": "telemetry_id",
        "performance_metrics": "metric_id",
    }
    for table, key in pk.items():
        ids = _ids(generated_dataset.tables[table], key)
        assert len(ids) == len(set(ids)), f"duplicate PKs in {table}"


def test_foreign_keys_valid(generated_dataset: Dataset) -> None:
    t = generated_dataset.tables
    entity_ids = set(_ids(t["entities"], "entity_id"))
    asset_ids = set(_ids(t["assets"], "asset_id"))
    alert_ids = set(_ids(t["alerts"], "alert_id"))
    inv_ids = set(_ids(t["investigations"], "investigation_id"))

    for a in t["assets"]:
        assert a["entity_id"] in entity_ids
    for a in t["alerts"]:
        assert a["entity_id"] in entity_ids and a["asset_id"] in asset_ids
    for i in t["investigations"]:
        assert i["alert_id"] in alert_ids and i["entity_id"] in entity_ids
    for act in t["investigation_actions"]:
        assert act["investigation_id"] in inv_ids
    for e in t["escalations"]:
        if e["alert_id"] is not None:
            assert e["alert_id"] in alert_ids
        if e["investigation_id"] is not None:
            assert e["investigation_id"] in inv_ids
    for r in t["remediations"]:
        if r["alert_id"] is not None:
            assert r["alert_id"] in alert_ids
        if r["investigation_id"] is not None:
            assert r["investigation_id"] in inv_ids
    for tel in t["telemetry"]:
        assert tel["entity_id"] in entity_ids and tel["asset_id"] in asset_ids
    for m in t["performance_metrics"]:
        assert m["entity_id"] in entity_ids


def test_investigation_alert_one_to_one(generated_dataset: Dataset) -> None:
    alert_ids = [i["alert_id"] for i in generated_dataset.tables["investigations"]]
    assert len(alert_ids) == len(set(alert_ids))


def test_action_sequences_contiguous_and_ordered(generated_dataset: Dataset) -> None:
    by_inv: dict[str, list] = {}
    for act in generated_dataset.tables["investigation_actions"]:
        by_inv.setdefault(act["investigation_id"], []).append(act)
    for inv_id, acts in by_inv.items():
        acts_sorted = sorted(acts, key=lambda a: a["sequence_number"])
        seqs = [a["sequence_number"] for a in acts_sorted]
        assert seqs == list(range(1, len(acts) + 1)), f"non-contiguous seq in {inv_id}"
        times = [a["occurred_at"] for a in acts_sorted]
        assert times == sorted(times), f"actions out of time order in {inv_id}"


def test_temporal_ordering(generated_dataset: Dataset) -> None:
    t = generated_dataset.tables
    for a in t["alerts"]:
        if a["acknowledged_at"] is not None:
            assert a["acknowledged_at"] >= a["created_at"]
        if a["closed_at"] is not None:
            assert a["closed_at"] >= a["created_at"]
    inv_by_id = {i["investigation_id"]: i for i in t["investigations"]}
    for i in t["investigations"]:
        if i["ended_at"] is not None:
            assert i["ended_at"] >= i["started_at"]
    for act in t["investigation_actions"]:
        inv = inv_by_id[act["investigation_id"]]
        assert act["occurred_at"] >= inv["started_at"]
        if inv["ended_at"] is not None:
            # Actions must fall within the investigation window.
            assert act["occurred_at"] <= inv["ended_at"]
    for e in t["escalations"]:
        if e["resolved_at"] is not None:
            assert e["resolved_at"] >= e["created_at"]
    for r in t["remediations"]:
        if r["completed_at"] is not None:
            assert r["completed_at"] >= r["created_at"]


def test_critical_assets_have_monitoring_metadata(generated_dataset: Dataset) -> None:
    for a in generated_dataset.tables["assets"]:
        if a["criticality"] in ("HIGH", "CRITICAL"):
            assert a["monitoring_expected"] is True
            assert a["expected_telemetry"] is not None


def test_telemetry_only_for_monitored_assets(generated_dataset: Dataset) -> None:
    monitored = {a["asset_id"] for a in generated_dataset.tables["assets"]
                 if a["monitoring_expected"]}
    for tel in generated_dataset.tables["telemetry"]:
        assert tel["asset_id"] in monitored


def test_normal_baseline_outnumbers_planted(generated_dataset: Dataset) -> None:
    planted_alerts = {
        g["alert_id"] for g in generated_dataset.ground_truth
        if g["scenario_type"] != ScenarioType.NORMAL_BASELINE and g["alert_id"]
    }
    total_alerts = len(generated_dataset.tables["alerts"])
    # Planted anomalous alerts must be a small minority of all alerts.
    assert len(planted_alerts) < total_alerts * 0.1
    assert total_alerts > 300
