"""Ingestion API tests via the FastAPI TestClient.

Covers upload security boundaries (extension/size/content), request shaping,
HTTP status mapping, safe error responses, validation-only requests leaving the
database untouched, transactional import, and the audit record.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Alert, SocEntity
from tests.conftest import make_csv, make_json

_ENTITY_COLUMNS = [
    "entity_id",
    "name",
    "sector",
    "peer_group",
    "scale",
    "asset_count_estimate",
    "analyst_headcount",
    "created_at",
    "data_period_start",
    "data_period_end",
]


def _entity(**overrides) -> dict[str, object]:
    row = {
        "entity_id": "ENT-01",
        "name": "Example SOC",
        "sector": "FINANCE",
        "peer_group": "FINANCE",
        "scale": "MEDIUM",
        "asset_count_estimate": 10,
        "analyst_headcount": 5,
        "created_at": "2024-01-01T00:00:00+00:00",
        "data_period_start": "2024-01-01T00:00:00+00:00",
        "data_period_end": "2024-06-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _post(client, path, content, filename, dataset_type, **data):
    return client.post(
        path,
        files={"file": (filename, content, "application/octet-stream")},
        data={"dataset_type": dataset_type, **data},
    )


def test_dataset_types_endpoint(client: TestClient) -> None:
    body = client.get("/api/v1/ingestion/dataset-types").json()
    types = {t["dataset_type"] for t in body["supported_types"]}
    assert {"entities", "assets", "alerts", "performance_metrics"} <= types


def test_validate_csv_valid(client: TestClient) -> None:
    content = make_csv([_entity()], _ENTITY_COLUMNS)
    resp = _post(client, "/api/v1/ingestion/validate", content, "entities.csv", "entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_valid"] is True
    assert body["detected_format"] == "csv"
    assert body["row_count"] == 1


def test_validate_json_valid(client: TestClient) -> None:
    content = make_json([_entity()])
    resp = _post(client, "/api/v1/ingestion/validate", content, "entities.json", "entities")
    assert resp.status_code == 200
    assert resp.json()["is_valid"] is True


def test_validate_does_not_write(client: TestClient, db_session: Session) -> None:
    content = make_json([_entity()])
    _post(client, "/api/v1/ingestion/validate", content, "entities.json", "entities")
    assert db_session.scalar(select(func.count()).select_from(SocEntity)) == 0


def test_unsupported_extension_rejected(client: TestClient) -> None:
    resp = _post(client, "/api/v1/ingestion/validate", b"x", "data.txt", "entities")
    assert resp.status_code == 415


def test_unsupported_dataset_type_rejected(client: TestClient) -> None:
    content = make_json([_entity()])
    resp = _post(client, "/api/v1/ingestion/validate", content, "x.json", "secret_table")
    assert resp.status_code == 400


def test_content_format_mismatch(client: TestClient) -> None:
    # JSON content uploaded with a .csv extension.
    resp = _post(client, "/api/v1/ingestion/validate", make_json([_entity()]), "x.csv", "entities")
    assert resp.status_code == 200
    assert resp.json()["is_valid"] is False
    assert any(e["code"] == "content_format_mismatch" for e in resp.json()["errors"])


def test_malformed_json(client: TestClient) -> None:
    resp = _post(client, "/api/v1/ingestion/validate", b"{not valid", "x.json", "entities")
    assert resp.status_code == 200
    assert any(e["code"] == "malformed_json" for e in resp.json()["errors"])


def test_oversized_upload_rejected(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 16)
    big = make_csv([_entity(), _entity(entity_id="ENT-02")], _ENTITY_COLUMNS)
    resp = _post(client, "/api/v1/ingestion/validate", big, "entities.csv", "entities")
    assert resp.status_code == 413


def test_import_commits_and_records_audit(client: TestClient, db_session: Session) -> None:
    content = make_csv([_entity()], _ENTITY_COLUMNS)
    resp = _post(client, "/api/v1/ingestion/import", content, "entities.csv", "entities", mode="append")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["committed"] is True
    assert db_session.scalar(select(func.count()).select_from(SocEntity)) == 1

    record = client.get(f"/api/v1/ingestion/imports/{body['import_id']}")
    assert record.status_code == 200
    assert record.json()["dataset_type"] == "entities"


def test_import_invalid_leaves_db_unchanged(client: TestClient, db_session: Session) -> None:
    bad = make_json([_entity(sector="BANKING")])
    resp = _post(client, "/api/v1/ingestion/import", bad, "entities.json", "entities")
    assert resp.status_code == 422
    assert resp.json()["status"] == "REJECTED"
    assert resp.json()["committed"] is False
    assert db_session.scalar(select(func.count()).select_from(SocEntity)) == 0


def test_repeated_append_rejects_duplicates(client: TestClient, db_session: Session) -> None:
    content = make_json([_entity()])
    first = _post(client, "/api/v1/ingestion/import", content, "e.json", "entities", mode="append")
    assert first.json()["status"] == "COMPLETED"
    second = _post(client, "/api/v1/ingestion/import", content, "e.json", "entities", mode="append")
    assert second.json()["status"] == "REJECTED"
    assert db_session.scalar(select(func.count()).select_from(SocEntity)) == 1


def test_replace_mode_overwrites(client: TestClient, db_session: Session) -> None:
    _post(client, "/api/v1/ingestion/import", make_json([_entity()]), "e.json", "entities", mode="append")
    replaced = _post(
        client,
        "/api/v1/ingestion/import",
        make_json([_entity(name="Renamed")]),
        "e.json",
        "entities",
        mode="replace",
    )
    assert replaced.json()["status"] == "COMPLETED"
    assert db_session.scalar(select(func.count()).select_from(SocEntity)) == 1
    entity = db_session.scalars(select(SocEntity)).one()
    assert entity.name == "Renamed"


def test_import_rollback_on_db_constraint(client: TestClient, db_session: Session, seed_entity) -> None:
    """Two actions sharing (investigation_id, sequence_number) pass semantic
    validation but violate the DB unique constraint, forcing a full rollback."""
    now = datetime(2024, 1, 2, tzinfo=timezone.utc).isoformat()
    # Create the alert + investigation the actions reference.
    alert = {
        "alert_id": "ALR-000001",
        "entity_id": "ENT-01",
        "asset_id": "AST-00001",
        "severity": "HIGH",
        "category": "MALWARE",
        "detection_source": "ENDPOINT",
        "status": "CLOSED",
        "created_at": now,
        "acknowledged_at": None,
        "closed_at": None,
        "recurrence_key": None,
        "is_true_positive": True,
    }
    _post(client, "/api/v1/ingestion/import", make_json([alert]), "a.json", "alerts")
    inv = {
        "investigation_id": "INV-000001",
        "alert_id": "ALR-000001",
        "entity_id": "ENT-01",
        "analyst_id": "ANALYST-001",
        "status": "CLOSED",
        "started_at": now,
        "ended_at": None,
        "duration_seconds": None,
        "evidence_count": 1,
    }
    _post(client, "/api/v1/ingestion/import", make_json([inv]), "i.json", "investigations")

    actions = [
        {
            "action_id": "ACT-0000001",
            "investigation_id": "INV-000001",
            "sequence_number": 1,
            "action_type": "OPEN",
            "occurred_at": now,
            "duration_seconds": None,
            "outcome": None,
        },
        {
            "action_id": "ACT-0000002",
            "investigation_id": "INV-000001",
            "sequence_number": 1,  # duplicate sequence -> DB unique violation
            "action_type": "CLOSE",
            "occurred_at": now,
            "duration_seconds": None,
            "outcome": None,
        },
    ]
    resp = _post(
        client, "/api/v1/ingestion/import", make_json(actions), "ac.json", "investigation_actions"
    )
    assert resp.status_code == 500
    assert resp.json()["status"] == "ROLLED_BACK"
    from app.models import InvestigationAction

    assert db_session.scalar(select(func.count()).select_from(InvestigationAction)) == 0


def test_error_responses_are_safe(client: TestClient) -> None:
    resp = _post(client, "/api/v1/ingestion/validate", b"{bad", "x.json", "entities")
    raw = resp.text.lower()
    for leaked in ("traceback", "sqlite:///", "c:\\", "/users/", "database_url"):
        assert leaked not in raw


def test_error_detail_is_capped(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "max_reported_errors", 5)
    rows = [_entity(entity_id=f"BAD-{i}") for i in range(20)]
    resp = _post(client, "/api/v1/ingestion/validate", make_json(rows), "e.json", "entities")
    body = resp.json()
    assert body["returned_error_count"] <= 5
    assert body["total_error_count"] >= 20
