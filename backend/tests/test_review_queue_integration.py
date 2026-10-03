"""Integration tests for the Supervisory Review Queue (Phase 10).

Runs the complete Phase 4–8 + Phase 9 analytics pipeline against the full
deterministic synthetic dataset, then builds the review queue and verifies:

- Every finding has a corresponding ReviewQueueItem.
- Queue IDs are stable (same after repeated generation).
- Analytical findings and evidence are not mutated by queue operations.
- Priority and confidence are distinct and correctly assigned.
- Status lifecycle works end-to-end.
- Review state is preserved across regeneration.
- Neutral language in all titles.
- Ground truth is NOT used by production queue logic (only in this oracle check).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.analytics.anomaly import run_anomaly_detection
from app.analytics.evidence import (
    annotate_anomaly,
    annotate_execution_gap,
    annotate_fingerprint,
    annotate_metric_risk_divergence,
    annotate_negative_space,
    annotate_peer_benchmark,
)
from app.analytics.execution_gap import run_execution_gap_detection
from app.analytics.investigation_fingerprinting import run_investigation_fingerprinting
from app.analytics.metric_risk_divergence import run_metric_risk_divergence
from app.analytics.negative_space import run_negative_space_detection
from app.analytics.peer_benchmark import run_peer_benchmark
from app.analytics.review_queue import (
    FindingsBundle,
    QueuePriority,
    ReviewQueueItem,
    ReviewStatus,
    build_review_queue,
    fetch_queue_items,
    transition_status,
)
from app.datagen.generator import Dataset
from app.db.base import Base
from app.models import (
    Alert, Asset, Escalation, Investigation, InvestigationAction,
    PerformanceMetric, Remediation, SocEntity, TelemetryRecord,
)

_LOAD_ORDER = [
    ("entities", SocEntity), ("assets", Asset), ("alerts", Alert),
    ("investigations", Investigation),
    ("investigation_actions", InvestigationAction),
    ("escalations", Escalation), ("remediations", Remediation),
    ("telemetry", TelemetryRecord), ("performance_metrics", PerformanceMetric),
]


@pytest.fixture()
def loaded_session(generated_dataset: Dataset):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(c, _):  # pragma: no cover
        c.execute("PRAGMA foreign_keys=ON")

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


@pytest.fixture()
def full_bundle(loaded_session):
    """Build the complete annotated bundle for all Phase 4–8 analytics."""
    return FindingsBundle(
        execution_gap=annotate_execution_gap(run_execution_gap_detection(loaded_session)),
        negative_space=annotate_negative_space(run_negative_space_detection(loaded_session)),
        anomaly=annotate_anomaly(run_anomaly_detection(loaded_session)),
        peer_benchmark=annotate_peer_benchmark(run_peer_benchmark(loaded_session)),
        metric_risk_div=annotate_metric_risk_divergence(run_metric_risk_divergence(loaded_session)),
        fingerprint=annotate_fingerprint(run_investigation_fingerprinting(loaded_session)),
    )


def _total_finding_count(bundle: FindingsBundle) -> int:
    total = 0
    if bundle.execution_gap:
        total += bundle.execution_gap.finding_count
    if bundle.negative_space:
        total += bundle.negative_space.finding_count
    if bundle.anomaly:
        total += bundle.anomaly.finding_count
    if bundle.peer_benchmark:
        total += bundle.peer_benchmark.finding_count
    if bundle.metric_risk_div:
        total += bundle.metric_risk_div.finding_count
    if bundle.fingerprint:
        total += bundle.fingerprint.finding_count
    return total


# ---------------------------------------------------------------------------
# Basic queue construction
# ---------------------------------------------------------------------------

class TestQueueConstruction:

    def test_queue_item_count_equals_finding_count(self, loaded_session, full_bundle):
        summary = build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        total_findings = _total_finding_count(full_bundle)
        assert summary.total_items == total_findings

    def test_all_items_start_open(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        items = fetch_queue_items(loaded_session)
        assert all(i.status == ReviewStatus.OPEN for i in items)

    def test_summary_by_status_all_open(self, loaded_session, full_bundle):
        summary = build_review_queue(loaded_session, full_bundle)
        assert summary.by_status.get(ReviewStatus.OPEN, 0) == summary.total_items
        assert all(s == ReviewStatus.OPEN for s in summary.by_status)

    def test_summary_by_priority_covers_all_levels(self, loaded_session, full_bundle):
        summary = build_review_queue(loaded_session, full_bundle)
        assert sum(summary.by_priority.values()) == summary.total_items

    def test_summary_by_category_covers_all_analytics(self, loaded_session, full_bundle):
        summary = build_review_queue(loaded_session, full_bundle)
        assert sum(summary.by_category.values()) == summary.total_items


# ---------------------------------------------------------------------------
# Idempotency and determinism
# ---------------------------------------------------------------------------

class TestIdempotency:

    def test_repeated_generation_same_queue_ids(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        ids_first = {i.queue_id for i in fetch_queue_items(loaded_session, limit=1000)}

        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        ids_second = {i.queue_id for i in fetch_queue_items(loaded_session, limit=1000)}

        assert ids_first == ids_second

    def test_no_duplicates_on_double_generation(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        count_first = loaded_session.scalar(
            select(func.count()).select_from(ReviewQueueItem)
        )
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        count_second = loaded_session.scalar(
            select(func.count()).select_from(ReviewQueueItem)
        )
        assert count_first == count_second

    def test_review_state_preserved_on_regeneration(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()

        # Review one item.
        first_item = fetch_queue_items(loaded_session)[0]
        transition_status(loaded_session, first_item.queue_id, ReviewStatus.REVIEWED, review_note="Checked.")
        loaded_session.commit()

        # Regenerate with same bundle — should preserve reviewed state.
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()

        refreshed = loaded_session.get(ReviewQueueItem, first_item.queue_id)
        assert refreshed.status == ReviewStatus.REVIEWED
        assert refreshed.review_note == "Checked."


# ---------------------------------------------------------------------------
# Priority and confidence
# ---------------------------------------------------------------------------

class TestPriorityAndConfidence:

    def test_all_priorities_are_valid_enum_values(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        items = fetch_queue_items(loaded_session, limit=1000)
        for item in items:
            assert item.priority in QueuePriority

    def test_all_confidence_strings_non_empty(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        items = fetch_queue_items(loaded_session, limit=1000)
        for item in items:
            assert item.confidence  # non-empty string

    def test_critical_items_exist(self, loaded_session, full_bundle):
        """Some findings with CRITICAL severity alerts should yield CRITICAL priority."""
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        critical_items = fetch_queue_items(loaded_session, priority=QueuePriority.CRITICAL, limit=1000)
        # There should be at least one CRITICAL priority item (CRITICAL alerts → priority_up)
        assert len(critical_items) >= 1

    def test_low_priority_items_exist(self, loaded_session, full_bundle):
        """IF-DEV-002 and IF-REP-001 findings should yield LOW priority items."""
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        low_items = fetch_queue_items(loaded_session, priority=QueuePriority.LOW, limit=1000)
        assert len(low_items) >= 1


# ---------------------------------------------------------------------------
# No mutation of analytical findings or evidence
# ---------------------------------------------------------------------------

class TestReadOnly:

    def test_queue_generation_does_not_mutate_findings(self, loaded_session, full_bundle):
        # Record original finding count for one analytic.
        original_eg_findings = full_bundle.execution_gap.finding_count if full_bundle.execution_gap else 0

        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()

        # Run analytics again after queue generation.
        post_result = run_execution_gap_detection(loaded_session)
        assert post_result.finding_count == original_eg_findings

    def test_queue_generation_does_not_mutate_source_records(self, loaded_session, full_bundle):
        models = (SocEntity, Alert, Investigation, PerformanceMetric)
        before = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}

        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()

        after = {m.__name__: loaded_session.scalar(select(func.count()).select_from(m)) for m in models}
        assert before == after


# ---------------------------------------------------------------------------
# Status lifecycle end-to-end
# ---------------------------------------------------------------------------

class TestStatusLifecycle:

    def test_full_lifecycle_open_in_review_reviewed(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        item = fetch_queue_items(loaded_session)[0]
        transition_status(loaded_session, item.queue_id, ReviewStatus.IN_REVIEW, reviewer_ref="sup-1")
        transition_status(loaded_session, item.queue_id, ReviewStatus.REVIEWED, review_note="All clear.")
        loaded_session.commit()
        refreshed = loaded_session.get(ReviewQueueItem, item.queue_id)
        assert refreshed.status == ReviewStatus.REVIEWED
        assert refreshed.reviewer_ref == "sup-1"
        assert refreshed.review_note == "All clear."
        assert refreshed.reviewed_at is not None

    def test_status_filter_after_review(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        items = fetch_queue_items(loaded_session, limit=3)
        for item in items[:2]:
            transition_status(loaded_session, item.queue_id, ReviewStatus.REVIEWED)
        loaded_session.commit()
        open_items = fetch_queue_items(loaded_session, status=ReviewStatus.OPEN, limit=1000)
        reviewed_items = fetch_queue_items(loaded_session, status=ReviewStatus.REVIEWED, limit=1000)
        total = loaded_session.scalar(select(func.count()).select_from(ReviewQueueItem))
        assert len(open_items) + len(reviewed_items) == total


# ---------------------------------------------------------------------------
# Neutral language in titles
# ---------------------------------------------------------------------------

class TestNeutralLanguage:

    def test_all_queue_titles_neutral(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        items = fetch_queue_items(loaded_session, limit=1000)
        forbidden = ["negligence", "fraud", "misconduct", "blame", "manipulat", "guilty"]
        for item in items:
            for word in forbidden:
                assert word.lower() not in item.title.lower(), (
                    f"Forbidden word {word!r} in title {item.title!r}"
                )

    def test_all_queue_titles_contain_potential(self, loaded_session, full_bundle):
        build_review_queue(loaded_session, full_bundle)
        loaded_session.commit()
        items = fetch_queue_items(loaded_session, limit=1000)
        for item in items:
            assert "Potential" in item.title, (
                f"Title does not contain 'Potential': {item.title!r}"
            )


# ---------------------------------------------------------------------------
# Partial bundle (some analytics missing)
# ---------------------------------------------------------------------------

class TestPartialBundle:

    def test_bundle_with_only_execution_gap(self, loaded_session):
        eg_result = run_execution_gap_detection(loaded_session)
        annotated_eg = annotate_execution_gap(eg_result)
        bundle = FindingsBundle(execution_gap=annotated_eg)
        summary = build_review_queue(loaded_session, bundle)
        loaded_session.commit()
        assert summary.total_items == annotated_eg.finding_count

    def test_empty_bundle_produces_no_items(self, loaded_session):
        summary = build_review_queue(loaded_session, FindingsBundle())
        assert summary.total_items == 0
