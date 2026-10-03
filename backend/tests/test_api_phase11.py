"""Phase 11 API tests.

Tests use the existing ``client`` fixture from conftest.py, which wires
the FastAPI TestClient to an in-memory SQLite database with system metadata
seeded.  For tests that require domain data (entities, findings, queue items),
the full deterministic synthetic dataset is loaded into a separate in-memory
database, and the app's ``get_db`` dependency is overridden to point at it.

Categories
----------
1.  Existing endpoints — regression (health, ingestion dataset-types)
2.  Entities — list, get, not-found, pagination, empty DB
3.  Assessment — run on empty DB, run with data, response shape, queue populated
4.  Findings — list, filters, pagination, not-found, single detail, evidence
5.  Review queue — list, summary, get, valid transitions, invalid transitions,
    not-found, filter by status/priority/entity/category, review metadata
6.  Error handling — malformed inputs, invalid enum values, bad pagination,
    security-sensitive error content
7.  Determinism — repeated runs produce consistent results
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.analytics.review_queue.models import ReviewQueueItem
from app.datagen.config import DEFAULT_SEED, GenerationConfig
from app.datagen.generator import DatasetGenerator
from app.datagen.writer import write_dataset
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models import (
    Alert, Asset, Escalation, Investigation, InvestigationAction,
    PerformanceMetric, Remediation, SocEntity, TelemetryRecord,
)
from app.services.system_service import ensure_system_metadata

_LOAD_ORDER = [
    ("entities", SocEntity), ("assets", Asset), ("alerts", Alert),
    ("investigations", Investigation),
    ("investigation_actions", InvestigationAction),
    ("escalations", Escalation), ("remediations", Remediation),
    ("telemetry", TelemetryRecord), ("performance_metrics", PerformanceMetric),
]


# ---------------------------------------------------------------------------
# Fixture: client with full synthetic dataset loaded
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def _synthetic_dataset():
    return DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()


@pytest.fixture()
def data_client(_synthetic_dataset):
    """TestClient backed by an in-memory DB pre-loaded with the synthetic dataset."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk(c, _):  # pragma: no cover
        c.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    SML = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)
    session = SML()
    ensure_system_metadata(session)
    session.commit()

    for table, model in _LOAD_ORDER:
        session.add_all(model(**row) for row in _synthetic_dataset.tables[table])
        session.flush()
    session.commit()

    app = create_app()

    def _override():
        yield session

    app.dependency_overrides[get_db] = _override
    tc = TestClient(app, raise_server_exceptions=False)
    yield tc
    session.close()
    Base.metadata.drop_all(engine)
    engine.dispose()
    app.dependency_overrides.clear()


# ===========================================================================
# 1. Existing endpoints — regression
# ===========================================================================

def test_health_still_ok(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "sat-sa-api"


def test_dataset_types_still_ok(client: TestClient) -> None:
    resp = client.get("/api/v1/ingestion/dataset-types")
    assert resp.status_code == 200
    assert "supported_types" in resp.json()


# ===========================================================================
# 2. Entities
# ===========================================================================

def test_entities_empty_db_returns_zero(client: TestClient) -> None:
    resp = client.get("/api/v1/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_entities_list_with_data(data_client: TestClient) -> None:
    resp = data_client.get("/api/v1/entities")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 6
    assert len(body["items"]) == 6


def test_entities_item_shape(data_client: TestClient) -> None:
    body = data_client.get("/api/v1/entities").json()
    item = body["items"][0]
    required = {"entity_id", "name", "sector", "peer_group", "scale",
                "asset_count_estimate", "analyst_headcount",
                "data_period_start", "data_period_end"}
    assert required <= set(item)


def test_entities_ordered_by_entity_id(data_client: TestClient) -> None:
    items = data_client.get("/api/v1/entities").json()["items"]
    ids = [i["entity_id"] for i in items]
    assert ids == sorted(ids)


def test_entities_pagination_limit(data_client: TestClient) -> None:
    resp = data_client.get("/api/v1/entities?limit=2&offset=0")
    body = resp.json()
    assert resp.status_code == 200
    assert body["total"] == 6
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_entities_pagination_offset(data_client: TestClient) -> None:
    all_ids = data_client.get("/api/v1/entities").json()["items"]
    page2 = data_client.get("/api/v1/entities?limit=2&offset=2").json()["items"]
    assert page2[0]["entity_id"] == all_ids[2]["entity_id"]


def test_entity_get_by_id(data_client: TestClient) -> None:
    first_id = data_client.get("/api/v1/entities").json()["items"][0]["entity_id"]
    resp = data_client.get(f"/api/v1/entities/{first_id}")
    assert resp.status_code == 200
    assert resp.json()["entity_id"] == first_id


def test_entity_not_found(data_client: TestClient) -> None:
    resp = data_client.get("/api/v1/entities/NONEXISTENT-ID")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_entity_no_internal_leakage(data_client: TestClient) -> None:
    body_str = data_client.get("/api/v1/entities").text.lower()
    for leaked in ("sqlite", "traceback", "secret", "password", "sqlalchemy"):
        assert leaked not in body_str


# ===========================================================================
# 3. Assessment run
# ===========================================================================

def test_assessment_run_empty_db_returns_zero_findings(client: TestClient) -> None:
    resp = client.post("/api/v1/assessment/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_findings"] == 0
    assert body["entity_count"] == 0
    assert "analytics" in body
    assert "queue_items_total" in body
    assert "duration_ms" in body


def test_assessment_run_with_data_returns_findings(data_client: TestClient) -> None:
    resp = data_client.post("/api/v1/assessment/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_findings"] > 0
    assert body["entity_count"] == 6


def test_assessment_run_analytics_summary_shape(data_client: TestClient) -> None:
    body = data_client.post("/api/v1/assessment/run").json()
    analytic_ids = {a["analytic_id"] for a in body["analytics"]}
    assert {"EG-001", "NS-001", "AN-001", "PB-001", "MRD-001", "IF"} == analytic_ids
    for a in body["analytics"]:
        assert "finding_count" in a
        assert "status" in a


def test_assessment_run_populates_queue(data_client: TestClient) -> None:
    body = data_client.post("/api/v1/assessment/run").json()
    assert body["queue_items_total"] > 0
    assert body["queue_inserted"] + body["queue_unchanged"] == body["queue_items_total"]


def test_assessment_run_idempotent(data_client: TestClient) -> None:
    body1 = data_client.post("/api/v1/assessment/run").json()
    body2 = data_client.post("/api/v1/assessment/run").json()
    assert body1["total_findings"] == body2["total_findings"]
    # Second run: queue items should be unchanged (idempotent upsert)
    assert body2["queue_inserted"] == 0
    assert body2["queue_unchanged"] == body2["queue_items_total"]


def test_assessment_response_no_internal_leakage(data_client: TestClient) -> None:
    body_str = data_client.post("/api/v1/assessment/run").text.lower()
    for leaked in ("sqlite", "traceback", "secret", "sqlalchemy"):
        assert leaked not in body_str


# ===========================================================================
# 4. Findings
# ===========================================================================

def test_findings_empty_db_returns_zero(client: TestClient) -> None:
    body = client.get("/api/v1/findings").json()
    assert body["total"] == 0
    assert body["items"] == []


def test_findings_list_with_data(data_client: TestClient) -> None:
    body = data_client.get("/api/v1/findings").json()
    assert body["total"] > 0
    assert len(body["items"]) > 0


def test_findings_shape(data_client: TestClient) -> None:
    items = data_client.get("/api/v1/findings?limit=1").json()["items"]
    assert len(items) == 1
    item = items[0]
    required = {"finding_key", "analytic_id", "finding_type", "category",
                "entity_id", "period_label", "summary"}
    assert required <= set(item)


def test_findings_ordered_by_finding_key(data_client: TestClient) -> None:
    items = data_client.get("/api/v1/findings?limit=20").json()["items"]
    keys = [i["finding_key"] for i in items]
    assert keys == sorted(keys)


def test_findings_pagination(data_client: TestClient) -> None:
    total = data_client.get("/api/v1/findings").json()["total"]
    page1 = data_client.get("/api/v1/findings?limit=10&offset=0").json()
    page2 = data_client.get("/api/v1/findings?limit=10&offset=10").json()
    assert page1["total"] == total
    assert page2["total"] == total
    keys1 = {i["finding_key"] for i in page1["items"]}
    keys2 = {i["finding_key"] for i in page2["items"]}
    assert keys1.isdisjoint(keys2)  # no overlap between pages


def test_findings_filter_by_entity_id(data_client: TestClient) -> None:
    first_entity = data_client.get("/api/v1/entities").json()["items"][0]["entity_id"]
    body = data_client.get(f"/api/v1/findings?entity_id={first_entity}").json()
    assert all(i["entity_id"] == first_entity for i in body["items"])


def test_findings_filter_by_analytic_id(data_client: TestClient) -> None:
    body = data_client.get("/api/v1/findings?analytic_id=EG-001").json()
    assert all(i["analytic_id"] == "EG-001" for i in body["items"])


def test_findings_filter_by_category(data_client: TestClient) -> None:
    body = data_client.get("/api/v1/findings?category=EXECUTION_GAP").json()
    assert all(i["category"] == "EXECUTION_GAP" for i in body["items"])


def test_findings_with_evidence(data_client: TestClient) -> None:
    body = data_client.get("/api/v1/findings?include_evidence=true&limit=5").json()
    for item in body["items"]:
        assert item["confidence"] is not None
        assert item["evidence_count"] >= 0


def test_findings_without_evidence_no_large_payload(data_client: TestClient) -> None:
    # Without evidence=true, each item should be compact.
    body = data_client.get("/api/v1/findings?limit=10").json()
    for item in body["items"]:
        # No massive evidence_refs list in the item itself.
        assert "evidence_refs" not in item


def test_finding_detail_by_key(data_client: TestClient) -> None:
    finding_key = data_client.get("/api/v1/findings?limit=1").json()["items"][0]["finding_key"]
    # URL-encode the key to handle colons
    import urllib.parse
    encoded = urllib.parse.quote(finding_key, safe="")
    resp = data_client.get(f"/api/v1/findings/{encoded}")
    assert resp.status_code == 200
    body = resp.json()
    assert "finding" in body
    assert body["finding"]["finding_key"] == finding_key


def test_finding_detail_includes_evidence(data_client: TestClient) -> None:
    finding_key = data_client.get("/api/v1/findings?limit=1").json()["items"][0]["finding_key"]
    import urllib.parse
    encoded = urllib.parse.quote(finding_key, safe="")
    body = data_client.get(f"/api/v1/findings/{encoded}").json()
    ev = body.get("evidence")
    if ev is not None:
        assert "confidence" in ev
        assert "evidence_refs" in ev


def test_finding_detail_not_found(data_client: TestClient) -> None:
    resp = data_client.get("/api/v1/findings/NONEXISTENT:FAKE:KEY")
    assert resp.status_code == 404


def test_findings_no_internal_leakage(data_client: TestClient) -> None:
    body_str = data_client.get("/api/v1/findings?limit=5").text.lower()
    for leaked in ("sqlite", "traceback", "secret", "sqlalchemy"):
        assert leaked not in body_str


# ===========================================================================
# 5. Review queue
# ===========================================================================

@pytest.fixture()
def populated_queue_client(data_client: TestClient):
    """Run assessment first to populate the queue, then return the client."""
    data_client.post("/api/v1/assessment/run")
    return data_client


def test_queue_empty_db_returns_zero(client: TestClient) -> None:
    body = client.get("/api/v1/queue").json()
    assert body["total"] == 0
    assert body["items"] == []


def test_queue_summary_empty(client: TestClient) -> None:
    body = client.get("/api/v1/queue/summary").json()
    assert body["total"] == 0
    assert "by_status" in body
    assert "by_priority" in body
    assert "by_category" in body


def test_queue_list_with_data(populated_queue_client: TestClient) -> None:
    body = populated_queue_client.get("/api/v1/queue").json()
    assert body["total"] > 0
    assert len(body["items"]) > 0


def test_queue_item_shape(populated_queue_client: TestClient) -> None:
    item = populated_queue_client.get("/api/v1/queue?limit=1").json()["items"][0]
    required = {"queue_id", "finding_key", "analytic_id", "entity_id",
                "period_label", "title", "category", "priority",
                "confidence", "status", "created_at", "updated_at"}
    assert required <= set(item)


def test_queue_all_items_start_open(populated_queue_client: TestClient) -> None:
    # After fresh assessment, all items should be OPEN.
    body = populated_queue_client.get("/api/v1/queue?limit=200").json()
    assert all(i["status"] == "OPEN" for i in body["items"])


def test_queue_summary_totals_match(populated_queue_client: TestClient) -> None:
    summary = populated_queue_client.get("/api/v1/queue/summary").json()
    status_sum = sum(summary["by_status"].values())
    priority_sum = sum(summary["by_priority"].values())
    assert status_sum == summary["total"]
    assert priority_sum == summary["total"]


def test_queue_filter_by_status(populated_queue_client: TestClient) -> None:
    body = populated_queue_client.get("/api/v1/queue?status=OPEN").json()
    assert all(i["status"] == "OPEN" for i in body["items"])


def test_queue_filter_by_entity(populated_queue_client: TestClient) -> None:
    first_entity = populated_queue_client.get("/api/v1/entities").json()["items"][0]["entity_id"]
    body = populated_queue_client.get(f"/api/v1/queue?entity_id={first_entity}").json()
    assert all(i["entity_id"] == first_entity for i in body["items"])


def test_queue_filter_by_priority(populated_queue_client: TestClient) -> None:
    body = populated_queue_client.get("/api/v1/queue?priority=LOW").json()
    assert all(i["priority"] == "LOW" for i in body["items"])


def test_queue_filter_by_category(populated_queue_client: TestClient) -> None:
    body = populated_queue_client.get("/api/v1/queue?category=EXECUTION_GAP").json()
    assert all(i["category"] == "EXECUTION_GAP" for i in body["items"])


def test_queue_pagination(populated_queue_client: TestClient) -> None:
    total = populated_queue_client.get("/api/v1/queue").json()["total"]
    page1 = populated_queue_client.get("/api/v1/queue?limit=5&offset=0").json()
    page2 = populated_queue_client.get("/api/v1/queue?limit=5&offset=5").json()
    assert page1["total"] == total
    ids1 = {i["queue_id"] for i in page1["items"]}
    ids2 = {i["queue_id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)


def test_queue_get_by_id(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1").json()["items"][0]["queue_id"]
    resp = populated_queue_client.get(f"/api/v1/queue/{qid}")
    assert resp.status_code == 200
    assert resp.json()["queue_id"] == qid


def test_queue_not_found(populated_queue_client: TestClient) -> None:
    resp = populated_queue_client.get("/api/v1/queue/nonexistent000")
    assert resp.status_code == 404


def test_queue_title_contains_potential(populated_queue_client: TestClient) -> None:
    items = populated_queue_client.get("/api/v1/queue?limit=20").json()["items"]
    for item in items:
        assert "Potential" in item["title"]


def test_queue_transition_open_to_in_review(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1").json()["items"][0]["queue_id"]
    resp = populated_queue_client.patch(
        f"/api/v1/queue/{qid}/status",
        json={"new_status": "IN_REVIEW", "reviewer_ref": "supervisor-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "IN_REVIEW"
    assert body["reviewer_ref"] == "supervisor-1"


def test_queue_transition_to_reviewed_with_note(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1&offset=1").json()["items"][0]["queue_id"]
    resp = populated_queue_client.patch(
        f"/api/v1/queue/{qid}/status",
        json={"new_status": "REVIEWED", "review_note": "Confirmed as expected pattern."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "REVIEWED"
    assert body["review_note"] == "Confirmed as expected pattern."
    assert body["reviewed_at"] is not None


def test_queue_transition_to_dismissed(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1&offset=2").json()["items"][0]["queue_id"]
    resp = populated_queue_client.patch(
        f"/api/v1/queue/{qid}/status",
        json={"new_status": "DISMISSED"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "DISMISSED"


def test_queue_transition_invalid_returns_409(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1&offset=3").json()["items"][0]["queue_id"]
    # Transition OPEN → OPEN is invalid.
    resp = populated_queue_client.patch(
        f"/api/v1/queue/{qid}/status",
        json={"new_status": "OPEN"},
    )
    assert resp.status_code == 409


def test_queue_transition_not_found_returns_404(populated_queue_client: TestClient) -> None:
    resp = populated_queue_client.patch(
        "/api/v1/queue/nonexistent000/status",
        json={"new_status": "IN_REVIEW"},
    )
    assert resp.status_code == 404


def test_queue_reviewed_to_open_reopen(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1&offset=4").json()["items"][0]["queue_id"]
    populated_queue_client.patch(f"/api/v1/queue/{qid}/status", json={"new_status": "REVIEWED"})
    resp = populated_queue_client.patch(f"/api/v1/queue/{qid}/status", json={"new_status": "OPEN"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "OPEN"


def test_queue_status_filter_after_transitions(populated_queue_client: TestClient) -> None:
    # Review some items, then verify filter counts.
    items = populated_queue_client.get("/api/v1/queue?limit=3&offset=5").json()["items"]
    for item in items:
        populated_queue_client.patch(
            f"/api/v1/queue/{item['queue_id']}/status",
            json={"new_status": "REVIEWED"},
        )
    reviewed = populated_queue_client.get("/api/v1/queue?status=REVIEWED").json()
    assert reviewed["total"] >= 3


# ===========================================================================
# 6. Error handling
# ===========================================================================

def test_invalid_limit_returns_422(data_client: TestClient) -> None:
    resp = data_client.get("/api/v1/entities?limit=0")
    assert resp.status_code == 422


def test_limit_too_large_returns_422(data_client: TestClient) -> None:
    resp = data_client.get("/api/v1/findings?limit=9999")
    assert resp.status_code == 422


def test_negative_offset_returns_422(data_client: TestClient) -> None:
    resp = data_client.get("/api/v1/entities?offset=-1")
    assert resp.status_code == 422


def test_invalid_queue_status_filter_returns_400(populated_queue_client: TestClient) -> None:
    resp = populated_queue_client.get("/api/v1/queue?status=BOGUS")
    assert resp.status_code == 400


def test_invalid_queue_priority_filter_returns_400(populated_queue_client: TestClient) -> None:
    resp = populated_queue_client.get("/api/v1/queue?priority=URGENT")
    assert resp.status_code == 400


def test_invalid_category_filter_returns_400(populated_queue_client: TestClient) -> None:
    resp = populated_queue_client.get("/api/v1/queue?category=MADE_UP")
    assert resp.status_code == 400


def test_queue_transition_invalid_status_value_returns_400(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1").json()["items"][0]["queue_id"]
    resp = populated_queue_client.patch(
        f"/api/v1/queue/{qid}/status",
        json={"new_status": "TOTALLY_INVALID"},
    )
    assert resp.status_code == 400


def test_queue_transition_missing_body_returns_422(populated_queue_client: TestClient) -> None:
    qid = populated_queue_client.get("/api/v1/queue?limit=1").json()["items"][0]["queue_id"]
    resp = populated_queue_client.patch(f"/api/v1/queue/{qid}/status", json={})
    assert resp.status_code == 422


def test_no_endpoint_leaks_stack_trace(data_client: TestClient) -> None:
    """Unhandled errors must not expose tracebacks, paths, or SQL to clients."""
    endpoints = [
        "/api/v1/entities/DEFINITELY-NOT-THERE",
        "/api/v1/queue/badid",
        "/api/v1/findings/fake:key:that:does:not:exist",
    ]
    for url in endpoints:
        resp = data_client.get(url)
        raw = resp.text.lower()
        for leaked in ("traceback", "sqlalchemy", "sqlite", "file \"", "line ", "exception"):
            assert leaked not in raw, (
                f"Sensitive term {leaked!r} found in {url!r} response: {resp.text[:200]!r}"
            )


def test_no_unhandled_500_on_bad_transition(populated_queue_client: TestClient) -> None:
    """Invalid transitions must return 409, never 500."""
    qid = populated_queue_client.get("/api/v1/queue?limit=1").json()["items"][0]["queue_id"]
    # Force an already-invalid transition: OPEN → OPEN
    resp = populated_queue_client.patch(
        f"/api/v1/queue/{qid}/status",
        json={"new_status": "OPEN"},
    )
    assert resp.status_code in (400, 409)
    assert resp.status_code != 500


# ===========================================================================
# 7. Determinism
# ===========================================================================

def test_findings_list_deterministic(data_client: TestClient) -> None:
    keys1 = [i["finding_key"] for i in data_client.get("/api/v1/findings?limit=20").json()["items"]]
    keys2 = [i["finding_key"] for i in data_client.get("/api/v1/findings?limit=20").json()["items"]]
    assert keys1 == keys2


def test_queue_list_deterministic(populated_queue_client: TestClient) -> None:
    ids1 = [i["queue_id"] for i in populated_queue_client.get("/api/v1/queue?limit=20").json()["items"]]
    ids2 = [i["queue_id"] for i in populated_queue_client.get("/api/v1/queue?limit=20").json()["items"]]
    assert ids1 == ids2
