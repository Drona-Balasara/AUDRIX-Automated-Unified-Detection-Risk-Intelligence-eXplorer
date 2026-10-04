"""Phase 13 — Focused integration/validation tests.

These tests exercise scenario-level detection coverage, cross-system behavior
(analytics → evidence → queue), determinism, queue lifecycle, API boundary
safety, and review-state preservation.

No ground_truth is used in production logic; it is used here only as a test
oracle (as permitted by the project design).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from collections import defaultdict

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.datagen.config import GenerationConfig, DEFAULT_SEED
from app.datagen.generator import DatasetGenerator
from app.datagen.scenarios import ScenarioType, DetectionCategory
from app.db.base import Base
from app.models import (
    Alert, Asset, Escalation, Investigation, InvestigationAction,
    PerformanceMetric, Remediation, SocEntity, TelemetryRecord,
)
from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.negative_space import run_negative_space_detection
from app.analytics.anomaly import run_anomaly_detection
from app.analytics.peer_benchmark import run_peer_benchmark
from app.analytics.metric_risk_divergence import run_metric_risk_divergence
from app.analytics.investigation_fingerprinting import run_investigation_fingerprinting
from app.analytics.evidence import (
    annotate_execution_gap, annotate_negative_space,
    annotate_anomaly, annotate_peer_benchmark,
    annotate_metric_risk_divergence, annotate_fingerprint,
)
from app.analytics.evidence.model import EvidenceSourceType
from app.analytics.review_queue import (
    FindingsBundle, build_review_queue, fetch_queue_items,
    ReviewStatus, QueuePriority, transition_status,
    InvalidTransitionError, QueueItemNotFoundError,
)
from app.analytics.review_queue.models import ReviewQueueItem

_LOAD_ORDER = [
    ("entities", SocEntity), ("assets", Asset), ("alerts", Alert),
    ("investigations", Investigation),
    ("investigation_actions", InvestigationAction),
    ("escalations", Escalation), ("remediations", Remediation),
    ("telemetry", TelemetryRecord), ("performance_metrics", PerformanceMetric),
]


# ---------------------------------------------------------------------------
# Module-scoped fixtures — generate the deterministic dataset once
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def syn_dataset():
    return DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()


@pytest.fixture(scope="module")
def loaded_session(syn_dataset):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(c, _):  # pragma: no cover
        c.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    s = Session(engine)
    for table, model in _LOAD_ORDER:
        s.add_all(model(**row) for row in syn_dataset.tables[table])
        s.flush()
    s.commit()
    yield s
    s.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="module")
def all_results(loaded_session):
    """Run all analytics once and cache results."""
    eg  = run_execution_gap_detection(loaded_session)
    ns  = run_negative_space_detection(loaded_session)
    an  = run_anomaly_detection(loaded_session)
    pb  = run_peer_benchmark(loaded_session)
    mrd = run_metric_risk_divergence(loaded_session)
    fp  = run_investigation_fingerprinting(loaded_session)
    return dict(eg=eg, ns=ns, an=an, pb=pb, mrd=mrd, fp=fp)


@pytest.fixture(scope="module")
def annotated_results(all_results):
    return dict(
        eg=annotate_execution_gap(all_results["eg"]),
        ns=annotate_negative_space(all_results["ns"]),
        an=annotate_anomaly(all_results["an"]),
        pb=annotate_peer_benchmark(all_results["pb"]),
        mrd=annotate_metric_risk_divergence(all_results["mrd"]),
        fp=annotate_fingerprint(all_results["fp"]),
    )


@pytest.fixture(scope="module")
def populated_queue(loaded_session, annotated_results):
    """Populate the review queue once per module."""
    bundle = FindingsBundle(
        execution_gap=annotated_results["eg"],
        negative_space=annotated_results["ns"],
        anomaly=annotated_results["an"],
        peer_benchmark=annotated_results["pb"],
        metric_risk_div=annotated_results["mrd"],
        fingerprint=annotated_results["fp"],
    )
    qs = build_review_queue(loaded_session, bundle)
    loaded_session.commit()
    return qs


# ---------------------------------------------------------------------------
# Helper: build entity→findings index
# ---------------------------------------------------------------------------

def _entity_findings(all_results):
    idx: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for f in all_results["eg"].findings:
        idx[f.entity_id].append(("EG-001", str(f.reason_code), f.finding_key))
    for f in all_results["ns"].findings:
        idx[f.entity_id].append(("NS-001", str(f.reason_code), f.finding_key))
    for f in all_results["an"].findings:
        idx[f.entity_id].append(("AN-001", str(f.decision), f.finding_key))
    for f in all_results["pb"].findings:
        idx[f.entity_id].append(("PB-001", "PEER_DEVIATION", f.finding_key))
    for f in all_results["mrd"].findings:
        idx[f.entity_id].append(("MRD-001", "METRIC_RISK_DIVERGENCE", f.finding_key))
    for f in all_results["fp"].all_findings:
        idx[f.entity_id].append((f.analytic_id, getattr(f, "finding_type", ""), f.finding_key))
    return idx


# ===========================================================================
# 1. Ground-truth scenario coverage
# ===========================================================================

class TestScenarioCoverage:

    def _affected_entities(self, syn_dataset, scenario_type: str) -> set[str]:
        return {g["entity_id"] for g in syn_dataset.ground_truth
                if g["scenario_type"] == scenario_type and g.get("entity_id")}

    def test_critical_alert_no_escalation_detected(self, syn_dataset, all_results):
        """EG-001 must fire CRITICAL_ALERT_NO_ESCALATION for affected entities."""
        affected = self._affected_entities(syn_dataset, ScenarioType.CRITICAL_ALERT_WITHOUT_ESCALATION)
        flagged = {f.entity_id for f in all_results["eg"].findings
                   if str(f.reason_code) == "CRITICAL_ALERT_NO_ESCALATION"}
        assert affected & flagged, (
            f"CRITICAL_ALERT_NO_ESCALATION not detected. Affected={affected} Flagged={flagged}"
        )

    def test_acknowledged_no_investigation_detected(self, syn_dataset, all_results):
        affected = self._affected_entities(syn_dataset, ScenarioType.ACKNOWLEDGED_WITHOUT_INVESTIGATION)
        flagged = {f.entity_id for f in all_results["eg"].findings
                   if str(f.reason_code) == "ACKNOWLEDGED_ALERT_NO_INVESTIGATION"}
        assert affected & flagged

    def test_recurring_no_remediation_detected(self, syn_dataset, all_results):
        affected = self._affected_entities(syn_dataset, ScenarioType.RECURRING_ALERTS_WITHOUT_REMEDIATION)
        flagged = {f.entity_id for f in all_results["eg"].findings
                   if str(f.reason_code) == "RECURRING_CONFIRMED_ALERTS_NO_REMEDIATION"}
        assert affected & flagged

    def test_missing_telemetry_detected(self, syn_dataset, all_results):
        affected = self._affected_entities(syn_dataset, ScenarioType.MISSING_TELEMETRY_CRITICAL_ASSET)
        flagged = {f.entity_id for f in all_results["ns"].findings
                   if str(f.reason_code) == "CRITICAL_ASSET_NO_TELEMETRY"}
        assert affected & flagged

    def test_telemetry_disappearance_detected(self, syn_dataset, all_results):
        affected = self._affected_entities(syn_dataset, ScenarioType.TELEMETRY_DISAPPEARANCE)
        flagged = {f.entity_id for f in all_results["ns"].findings
                   if str(f.reason_code) == "TELEMETRY_CONTINUITY_GAP"}
        assert affected & flagged

    def test_metric_risk_divergence_detected(self, syn_dataset, all_results):
        gt = next(g for g in syn_dataset.ground_truth
                  if g["scenario_type"] == ScenarioType.METRIC_RISK_DIVERGENCE)
        flagged = {f.entity_id for f in all_results["mrd"].findings}
        assert gt["entity_id"] in flagged

    def test_repetitive_workflow_detected(self, syn_dataset, all_results):
        gt_entity = next(g["entity_id"] for g in syn_dataset.ground_truth
                         if g["scenario_type"] == ScenarioType.REPETITIVE_INVESTIGATION_WORKFLOW)
        flagged = {f.entity_id for f in all_results["fp"].repetitive_findings}
        assert gt_entity in flagged

    def test_normal_baseline_is_negative_control(self, syn_dataset, all_results):
        """Normal-baseline investigations must NOT generate repetitive workflow findings."""
        neg_entities = {g["entity_id"] for g in syn_dataset.ground_truth
                        if g["scenario_type"] == ScenarioType.NORMAL_BASELINE}
        # Verify that normal-baseline entities are not all flagged as repetitive
        rep_entities = {f.entity_id for f in all_results["fp"].repetitive_findings}
        # At minimum they can appear in other analytics — just not ONLY because of baseline
        # The normal baseline is a negative control specifically for workflow quality, not all analytics
        # Verify that the repetitive finding is not on a normal-baseline-only entity
        baseline_only_entities = neg_entities - {
            g["entity_id"] for g in syn_dataset.ground_truth
            if g["scenario_type"] != ScenarioType.NORMAL_BASELINE
        }
        # If any purely-baseline entity got a REP finding, that's a false positive
        fp_in_baseline_only = baseline_only_entities & rep_entities
        assert not fp_in_baseline_only, (
            f"False positive REP findings on baseline-only entities: {fp_in_baseline_only}"
        )

    def test_all_detectable_scenarios_covered(self, syn_dataset, all_results):
        """All 8 detectable scenarios (non-NONE expected category) must be detected
        in the affected entity set."""
        idx = _entity_findings(all_results)
        gt_by_type = defaultdict(list)
        for g in syn_dataset.ground_truth:
            gt_by_type[g["scenario_type"]].append(g)

        missed = []
        for stype, rows in gt_by_type.items():
            expected = rows[0]["expected_detection_category"]
            if expected == DetectionCategory.NONE:
                continue
            affected = {r["entity_id"] for r in rows if r.get("entity_id")}
            found = any(
                True for eid in affected
                for analytic, ftype, fkey in idx.get(eid, [])
            )
            if not found:
                missed.append(stype)
        assert not missed, f"Undetected scenarios: {missed}"


# ===========================================================================
# 2. Determinism
# ===========================================================================

class TestDeterminism:

    def _fkey_hash(self, findings) -> str:
        return hashlib.sha256(
            "|".join(sorted(f.finding_key for f in findings)).encode()
        ).hexdigest()[:16]

    def test_finding_keys_stable_across_runs(self, syn_dataset):
        """Running analytics twice on the same data must produce identical finding keys."""
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(engine)
        s = Session(engine)
        for table, model in _LOAD_ORDER:
            s.add_all(model(**row) for row in syn_dataset.tables[table])
            s.flush()
        s.commit()

        eg1 = run_execution_gap_detection(s)
        eg2 = run_execution_gap_detection(s)
        assert self._fkey_hash(eg1.findings) == self._fkey_hash(eg2.findings)

        mrd1 = run_metric_risk_divergence(s)
        mrd2 = run_metric_risk_divergence(s)
        assert self._fkey_hash(mrd1.findings) == self._fkey_hash(mrd2.findings)

        fp1 = run_investigation_fingerprinting(s)
        fp2 = run_investigation_fingerprinting(s)
        assert self._fkey_hash(fp1.repetitive_findings) == self._fkey_hash(fp2.repetitive_findings)
        assert self._fkey_hash(fp1.deviation_findings) == self._fkey_hash(fp2.deviation_findings)

        s.close()
        Base.metadata.drop_all(engine)
        engine.dispose()

    def test_dataset_regeneration_produces_identical_counts(self):
        ds1 = DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()
        ds2 = DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()
        assert len(ds1.tables["alerts"]) == len(ds2.tables["alerts"])
        assert len(ds1.tables["investigations"]) == len(ds2.tables["investigations"])
        assert len(ds1.ground_truth) == len(ds2.ground_truth)

    def test_different_seed_produces_different_dataset(self):
        ds1 = DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()
        ds2 = DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED + 1)).generate()
        # Alert counts will differ with different seeds
        assert len(ds1.tables["alerts"]) != len(ds2.tables["alerts"])


# ===========================================================================
# 3. Evidence no-fabrication
# ===========================================================================

class TestEvidenceIntegrity:

    def test_eg_evidence_refs_match_real_records(self, loaded_session, all_results, annotated_results):
        real_alerts = set(loaded_session.scalars(select(Alert.alert_id)).all())
        real_invs   = set(loaded_session.scalars(select(Investigation.investigation_id)).all())
        fabricated = []
        for f in all_results["eg"].findings:
            ev = annotated_results["eg"].evidence_for(f.finding_key)
            if ev:
                for ref in ev.evidence_refs:
                    if ref.source_type == EvidenceSourceType.ALERT:
                        if ref.source_id not in real_alerts:
                            fabricated.append(f"ALERT {ref.source_id}")
                    if ref.source_type == EvidenceSourceType.INVESTIGATION:
                        if ref.source_id not in real_invs:
                            fabricated.append(f"INV {ref.source_id}")
        assert not fabricated, f"Fabricated evidence refs: {fabricated[:5]}"

    def test_ns_evidence_refs_match_real_assets(self, loaded_session, all_results, annotated_results):
        real_assets = set(loaded_session.scalars(select(Asset.asset_id)).all())
        fabricated = []
        for f in all_results["ns"].findings:
            ev = annotated_results["ns"].evidence_for(f.finding_key)
            if ev:
                for ref in ev.evidence_refs:
                    if ref.source_type == EvidenceSourceType.ASSET:
                        if ref.source_id not in real_assets:
                            fabricated.append(f"ASSET {ref.source_id}")
        assert not fabricated, f"Fabricated NS asset refs: {fabricated[:5]}"

    def test_every_finding_has_evidence_entry(self, all_results, annotated_results):
        """Every finding must have a FindingEvidence entry."""
        for f in all_results["eg"].findings:
            assert annotated_results["eg"].evidence_for(f.finding_key) is not None
        for f in all_results["ns"].findings:
            assert annotated_results["ns"].evidence_for(f.finding_key) is not None
        for f in all_results["an"].findings:
            assert annotated_results["an"].evidence_for(f.finding_key) is not None
        for f in all_results["fp"].all_findings:
            assert annotated_results["fp"].evidence_for(f.finding_key) is not None

    def test_confidence_separate_from_priority(self, loaded_session, populated_queue):
        """LOW-confidence findings must exist at multiple priority levels
        (confidence != priority)."""
        all_items = fetch_queue_items(loaded_session, limit=1000)
        low_conf_priorities = {i.priority for i in all_items if i.confidence == "LOW"}
        # With 163 IF-DEV-002 (LOW confidence, LOW priority) plus ~6 MEA (LOW-conf, MEDIUM)
        assert len(low_conf_priorities) >= 2, (
            f"LOW-confidence found at only priorities: {low_conf_priorities}"
        )


# ===========================================================================
# 4. Queue lifecycle
# ===========================================================================

class TestQueueLifecycle:

    def test_queue_idempotency(self, loaded_session, annotated_results, populated_queue):
        """Re-running build_review_queue must not insert duplicates."""
        bundle = FindingsBundle(
            execution_gap=annotated_results["eg"],
            negative_space=annotated_results["ns"],
            anomaly=annotated_results["an"],
            peer_benchmark=annotated_results["pb"],
            metric_risk_div=annotated_results["mrd"],
            fingerprint=annotated_results["fp"],
        )
        qs2 = build_review_queue(loaded_session, bundle)
        loaded_session.commit()
        assert qs2.inserted == 0, f"Second run inserted {qs2.inserted} items (expected 0)"
        assert qs2.unchanged == populated_queue.total_items

    def test_valid_status_transitions(self, loaded_session, populated_queue):
        """OPEN → IN_REVIEW → REVIEWED lifecycle must succeed."""
        item = fetch_queue_items(loaded_session, limit=1)[0]
        transition_status(loaded_session, item.queue_id, ReviewStatus.IN_REVIEW)
        loaded_session.flush()
        refreshed = loaded_session.get(ReviewQueueItem, item.queue_id)
        assert refreshed is not None
        assert refreshed.status == ReviewStatus.IN_REVIEW

        transition_status(loaded_session, item.queue_id, ReviewStatus.REVIEWED,
                          review_note="Validated.")
        loaded_session.flush()
        refreshed2 = loaded_session.get(ReviewQueueItem, item.queue_id)
        assert refreshed2 is not None
        assert refreshed2.status == ReviewStatus.REVIEWED
        assert refreshed2.review_note == "Validated."

    def test_invalid_transition_raises(self, loaded_session):
        """OPEN → OPEN must raise InvalidTransitionError."""
        items = fetch_queue_items(loaded_session, status=ReviewStatus.OPEN, limit=1)
        if not items:
            pytest.skip("No OPEN items available for this test")
        with pytest.raises(InvalidTransitionError):
            transition_status(loaded_session, items[0].queue_id, ReviewStatus.OPEN)

    def test_review_state_preserved_across_rebuild(self, loaded_session, annotated_results):
        """After reviewing an item, rebuild must NOT reset its status."""
        items = fetch_queue_items(loaded_session, status=ReviewStatus.OPEN, limit=1)
        if not items:
            pytest.skip("No OPEN items for preservation test")
        item = items[0]
        transition_status(loaded_session, item.queue_id, ReviewStatus.REVIEWED,
                          review_note="Phase13 test.")
        loaded_session.commit()

        bundle = FindingsBundle(
            execution_gap=annotated_results["eg"],
            negative_space=annotated_results["ns"],
            anomaly=annotated_results["an"],
            peer_benchmark=annotated_results["pb"],
            metric_risk_div=annotated_results["mrd"],
            fingerprint=annotated_results["fp"],
        )
        build_review_queue(loaded_session, bundle)
        loaded_session.commit()

        preserved = loaded_session.get(ReviewQueueItem, item.queue_id)
        assert preserved is not None
        assert preserved.status == ReviewStatus.REVIEWED
        assert preserved.review_note == "Phase13 test."

    def test_review_notes_separate_from_evidence(self, loaded_session, annotated_results):
        """Review notes on queue items must not appear in evidence refs."""
        items = fetch_queue_items(loaded_session, status=ReviewStatus.REVIEWED, limit=5)
        for qi in items:
            if qi.review_note:
                ev = annotated_results["eg"].evidence_for(qi.finding_key)
                if ev:
                    for ref in ev.evidence_refs:
                        assert qi.review_note not in ref.reason, (
                            "Review note leaked into evidence ref"
                        )

    def test_not_found_raises(self, loaded_session):
        with pytest.raises(QueueItemNotFoundError):
            transition_status(loaded_session, "nonexistent000", ReviewStatus.IN_REVIEW)


# ===========================================================================
# 5. IF-DEV-002 volume — documented limitation
# ===========================================================================

class TestIFDEV002Volume:

    def test_dev_findings_not_high_priority(self, loaded_session, populated_queue):
        """All IF-DEV-002 queue items should be LOW priority (fragile baseline)."""
        high_dev = loaded_session.scalar(
            select(func.count()).select_from(ReviewQueueItem).where(
                ReviewQueueItem.analytic_id == "IF-DEV-002",
                ReviewQueueItem.priority.in_(["CRITICAL", "HIGH"]),
            )
        )
        assert high_dev == 0, (
            f"{high_dev} IF-DEV-002 findings incorrectly at CRITICAL/HIGH priority"
        )

    def test_dev_findings_visible_not_suppressed(self, loaded_session, populated_queue, all_results):
        """IF-DEV-002 findings should be in the queue (not hidden), just LOW priority."""
        dev_in_queue = loaded_session.scalar(
            select(func.count()).select_from(ReviewQueueItem).where(
                ReviewQueueItem.analytic_id == "IF-DEV-002"
            )
        )
        assert dev_in_queue == len(all_results["fp"].deviation_findings), (
            f"IF-DEV-002 queue items ({dev_in_queue}) != findings ({len(all_results['fp'].deviation_findings)})"
        )


# ===========================================================================
# 6. API safety — no internal detail leakage
# ===========================================================================

class TestAPIBoundaries:

    @pytest.fixture()
    def api_client(self, loaded_session):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from app.db.session import get_db
        app = create_app()
        app.dependency_overrides[get_db] = lambda: loaded_session
        return TestClient(app, raise_server_exceptions=False)

    def test_health_exposes_no_db_url(self, api_client):
        resp = api_client.get("/api/v1/health")
        assert resp.status_code == 200
        raw = resp.text.lower()
        for leak in ("sqlite", "database_url", "/data/", "traceback", "secret"):
            assert leak not in raw, f"Health response leaks {leak!r}"

    def test_not_found_returns_clean_error(self, api_client):
        resp = api_client.get("/api/v1/entities/DOES_NOT_EXIST")
        assert resp.status_code == 404
        raw = resp.text.lower()
        for leak in ("traceback", "sqlalchemy", "sqlite", "file \""):
            assert leak not in raw, f"404 response leaks {leak!r}"

    def test_invalid_queue_transition_returns_409_not_500(self, api_client, populated_queue):
        items = api_client.get("/api/v1/queue?status=OPEN&limit=1").json()
        if items["total"] == 0:
            pytest.skip("No OPEN items")
        qid = items["items"][0]["queue_id"]
        resp = api_client.patch(f"/api/v1/queue/{qid}/status",
                                json={"new_status": "OPEN"})
        assert resp.status_code == 409, f"Expected 409 for invalid transition, got {resp.status_code}"
        assert resp.status_code != 500

    def test_malformed_pagination_returns_422(self, api_client):
        resp = api_client.get("/api/v1/findings?limit=abc")
        assert resp.status_code == 422

    def test_invalid_queue_filter_returns_400(self, api_client):
        resp = api_client.get("/api/v1/queue?status=BOGUS_STATUS")
        assert resp.status_code == 400

    def test_findings_response_no_traceback(self, api_client):
        resp = api_client.get("/api/v1/findings?limit=5")
        assert resp.status_code == 200
        raw = resp.text.lower()
        for leak in ("traceback", "sqlalchemy", "file \""):
            assert leak not in raw

    def test_assessment_run_returns_valid_schema(self, api_client):
        resp = api_client.post("/api/v1/assessment/run")
        assert resp.status_code == 200
        body = resp.json()
        required_keys = {"entity_count", "total_findings", "analytics",
                         "queue_items_total", "queue_inserted", "duration_ms"}
        assert required_keys <= set(body.keys())
        assert isinstance(body["total_findings"], int)
        assert isinstance(body["analytics"], list)

    def test_queue_summary_correct_totals(self, api_client, populated_queue):
        resp = api_client.get("/api/v1/queue/summary")
        assert resp.status_code == 200
        body = resp.json()
        status_sum = sum(body["by_status"].values())
        priority_sum = sum(body["by_priority"].values())
        assert status_sum == body["total"]
        assert priority_sum == body["total"]
