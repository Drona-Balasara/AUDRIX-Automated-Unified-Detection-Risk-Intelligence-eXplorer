"""End-to-end multi-table import using the committed Phase 2 synthetic dataset.

Proves CSV and JSON drive the same import pipeline, that a full nine-table load
in dependency order commits every row, that per-type counts match the dataset
manifest, and that foreign-key relationships hold in the resulting database.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Alert, Asset, Investigation, InvestigationAction, SocEntity

_SYNTHETIC = Path(__file__).resolve().parents[2] / "data" / "synthetic"

# Dependency order: parents imported before children.
_IMPORT_ORDER = [
    "entities",
    "assets",
    "alerts",
    "investigations",
    "investigation_actions",
    "escalations",
    "remediations",
    "telemetry",
    "performance_metrics",
]


def _manifest() -> dict[str, int]:
    return json.loads((_SYNTHETIC / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("ext", ["csv", "json"])
def test_full_synthetic_import(client: TestClient, db_session: Session, ext: str) -> None:
    manifest = _manifest()
    for dataset_type in _IMPORT_ORDER:
        content = (_SYNTHETIC / f"{dataset_type}.{ext}").read_bytes()
        resp = client.post(
            "/api/v1/ingestion/import",
            files={"file": (f"{dataset_type}.{ext}", content, "application/octet-stream")},
            data={"dataset_type": dataset_type, "mode": "replace"},
        )
        assert resp.status_code == 200, (dataset_type, resp.text)
        body = resp.json()
        assert body["status"] == "COMPLETED", (dataset_type, body)
        assert body["accepted_count"] == manifest[dataset_type], dataset_type

    # Row counts landed in the tables.
    assert db_session.scalar(select(func.count()).select_from(SocEntity)) == manifest["entities"]
    assert db_session.scalar(select(func.count()).select_from(Alert)) == manifest["alerts"]
    assert (
        db_session.scalar(select(func.count()).select_from(InvestigationAction))
        == manifest["investigation_actions"]
    )

    # Foreign-key integrity: no alert references a missing entity or asset.
    entity_ids = set(db_session.scalars(select(SocEntity.entity_id)))
    asset_ids = set(db_session.scalars(select(Asset.asset_id)))
    for alert_entity, alert_asset in db_session.execute(select(Alert.entity_id, Alert.asset_id)):
        assert alert_entity in entity_ids
        assert alert_asset in asset_ids

    # Every investigation action points at a real investigation.
    inv_ids = set(db_session.scalars(select(Investigation.investigation_id)))
    for (ref,) in db_session.execute(select(InvestigationAction.investigation_id)):
        assert ref in inv_ids
