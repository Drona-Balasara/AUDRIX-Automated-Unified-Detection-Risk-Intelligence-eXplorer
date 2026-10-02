"""Unit tests for the execution-gap detection rules and engine.

Each rule is exercised for positive detection, negative (control) cases,
boundary conditions, incomplete-but-valid data, deduplication, read-only
behaviour, and deterministic output. Records are built by hand so each case
isolates exactly one condition; no ground-truth labels are consulted here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analytics.execution_gap import (
    ExecutionGapConfig,
    run_execution_gap_detection,
)
from app.models import (
    Alert,
    Asset,
    Escalation,
    Investigation,
    Remediation,
    SocEntity,
)

PERIOD_END = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)
PERIOD_START = PERIOD_END - timedelta(days=30)

def _entity(session: Session, entity_id: str = "ENT-01") -> SocEntity:
    entity = SocEntity(
        entity_id=entity_id,
        name=f"Entity {entity_id}",
        sector="FINANCE",
        peer_group="FINANCE",
        scale="MEDIUM",
        asset_count_estimate=10,
        analyst_headcount=5,
        created_at=PERIOD_START,
        data_period_start=PERIOD_START,
        data_period_end=PERIOD_END,
    )
    session.add(entity)
    session.add(
        Asset(
            asset_id="AST-00001",
            entity_id=entity_id,
            name="Asset",
            category="SERVER",
            criticality="HIGH",
            monitoring_expected=True,
            expected_telemetry="AUTHENTICATION",
            created_at=PERIOD_START,
        )
    )
    session.flush()
    return entity


def _alert(session: Session, alert_id: str, **kw) -> Alert:
    params = dict(
        alert_id=alert_id,
        entity_id="ENT-01",
        asset_id="AST-00001",
        severity="CRITICAL",
        category="MALWARE",
        detection_source="ENDPOINT",
        status="CLOSED",
        created_at=PERIOD_START,
    )
    params.update(kw)
    alert = Alert(**params)
    session.add(alert)
    session.flush()
    return alert


def _investigation(session: Session, inv_id: str, alert_id: str, **kw) -> Investigation:
    params = dict(
        investigation_id=inv_id,
        alert_id=alert_id,
        entity_id="ENT-01",
        analyst_id="ANALYST-001",
        status="CLOSED",
        started_at=PERIOD_START,
        ended_at=PERIOD_START + timedelta(hours=2),
    )
    params.update(kw)
    inv = Investigation(**params)
    session.add(inv)
    session.flush()
    return inv


def _escalation(session: Session, esc_id: str, **kw) -> Escalation:
    params = dict(
        escalation_id=esc_id,
        created_at=PERIOD_START,
        target="TIER2",
        reason="SEVERITY",
        status="RESOLVED",
    )
    params.update(kw)
    esc = Escalation(**params)
    session.add(esc)
    session.flush()
    return esc


def _remediation(session: Session, rem_id: str, **kw) -> Remediation:
    params = dict(
        remediation_id=rem_id,
        created_at=PERIOD_START,
        remediation_type="PATCH",
        status="COMPLETED",
    )
    params.update(kw)
    rem = Remediation(**params)
    session.add(rem)
    session.flush()
    return rem


def _rule_findings(result, rule_id: str) -> list:
    return [f for f in result.findings if f.rule_id == rule_id]


# ----- EG-001: critical alert without escalation ---------------------------


def test_escalation_gap_positive(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True, closed_at=PERIOD_START + timedelta(hours=2))
    _investigation(db_session, "INV-1", "ALR-1")
    db_session.commit()

    result = run_execution_gap_detection(db_session)
    findings = _rule_findings(result, "EG-001")
    assert len(findings) == 1
    assert findings[0].alert_id == "ALR-1"
    assert findings[0].investigation_id == "INV-1"
    assert findings[0].gap_type.value == "ESCALATION_GAP"
    assert findings[0].observed_at == PERIOD_START + timedelta(hours=2)


def test_escalation_gap_negative_when_escalated_via_alert(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True)
    _investigation(db_session, "INV-1", "ALR-1")
    _escalation(db_session, "ESC-1", alert_id="ALR-1")
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-001") == []


def test_escalation_gap_negative_when_escalated_via_investigation(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True)
    _investigation(db_session, "INV-1", "ALR-1")
    _escalation(db_session, "ESC-1", investigation_id="INV-1")
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-001") == []


def test_escalation_gap_negative_not_true_positive(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=False)
    _investigation(db_session, "INV-1", "ALR-1")
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-001") == []


def test_escalation_gap_negative_not_closed(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True, status="IN_INVESTIGATION")
    _investigation(db_session, "INV-1", "ALR-1", status="IN_PROGRESS")
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-001") == []


def test_escalation_gap_negative_below_severity(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", severity="HIGH", is_true_positive=True)
    _investigation(db_session, "INV-1", "ALR-1")
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-001") == []


def test_escalation_gap_negative_no_investigation(db_session: Session) -> None:
    # Incomplete-but-valid: a closed critical alert with no investigation record
    # is a different concern and must not be reported by EG-001.
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True)
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-001") == []


# ----- EG-002: acknowledged alert without investigation --------------------


def _acked(hours_before_end: float) -> datetime:
    return PERIOD_END - timedelta(hours=hours_before_end)


def test_investigation_gap_positive(db_session: Session) -> None:
    _entity(db_session)
    _alert(
        db_session, "ALR-1", severity="HIGH", status="ACKNOWLEDGED",
        acknowledged_at=_acked(48),
    )
    db_session.commit()

    findings = _rule_findings(run_execution_gap_detection(db_session), "EG-002")
    assert len(findings) == 1
    assert findings[0].alert_id == "ALR-1"
    assert findings[0].gap_type.value == "INVESTIGATION_GAP"
    assert findings[0].window_end == PERIOD_END


def test_investigation_gap_boundary_exactly_at_threshold(db_session: Session) -> None:
    # Elapsed == threshold (24h) fires; just inside the window (23.5h) does not.
    _entity(db_session)
    _alert(
        db_session, "ALR-1", severity="HIGH", status="ACKNOWLEDGED",
        acknowledged_at=_acked(24),
    )
    _alert(
        db_session, "ALR-2", severity="HIGH", status="ACKNOWLEDGED",
        acknowledged_at=_acked(23.5),
    )
    db_session.commit()

    findings = _rule_findings(run_execution_gap_detection(db_session), "EG-002")
    assert {f.alert_id for f in findings} == {"ALR-1"}


def test_investigation_gap_negative_recent_acknowledgement(db_session: Session) -> None:
    _entity(db_session)
    _alert(
        db_session, "ALR-1", severity="HIGH", status="ACKNOWLEDGED",
        acknowledged_at=_acked(1),
    )
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-002") == []


def test_investigation_gap_negative_has_investigation(db_session: Session) -> None:
    _entity(db_session)
    _alert(
        db_session, "ALR-1", severity="HIGH", status="ACKNOWLEDGED",
        acknowledged_at=_acked(48),
    )
    _investigation(db_session, "INV-1", "ALR-1", status="IN_PROGRESS")
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-002") == []


def test_investigation_gap_negative_wrong_status(db_session: Session) -> None:
    _entity(db_session)
    _alert(
        db_session, "ALR-1", severity="HIGH", status="NEW",
        acknowledged_at=_acked(48),
    )
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-002") == []


def test_investigation_gap_negative_below_severity(db_session: Session) -> None:
    _entity(db_session)
    _alert(
        db_session, "ALR-1", severity="MEDIUM", status="ACKNOWLEDGED",
        acknowledged_at=_acked(48),
    )
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-002") == []


# ----- EG-003: recurring confirmed alerts without remediation --------------


def _recurring(
    session: Session, n: int, key: str = "RK-1", true_positive=True, id_prefix: str | None = None
) -> None:
    prefix = id_prefix or key
    for i in range(n):
        _alert(
            session, f"ALR-{prefix}-{i}", severity="HIGH", status="CLOSED",
            recurrence_key=key, is_true_positive=true_positive,
        )


def test_remediation_gap_positive(db_session: Session) -> None:
    _entity(db_session)
    _recurring(db_session, 3)
    db_session.commit()

    findings = _rule_findings(run_execution_gap_detection(db_session), "EG-003")
    assert len(findings) == 1
    assert findings[0].recurrence_key == "RK-1"
    assert len(findings[0].related_alert_ids) == 3
    assert findings[0].gap_type.value == "REMEDIATION_GAP"


def test_remediation_gap_negative_below_count(db_session: Session) -> None:
    _entity(db_session)
    _recurring(db_session, 2)
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-003") == []


def test_remediation_gap_negative_completed_remediation(db_session: Session) -> None:
    _entity(db_session)
    _recurring(db_session, 3)
    _remediation(db_session, "REM-1", alert_id="ALR-RK-1-0", status="COMPLETED")
    db_session.commit()

    assert _rule_findings(run_execution_gap_detection(db_session), "EG-003") == []


def test_remediation_gap_fires_when_remediation_not_completed(db_session: Session) -> None:
    # A FAILED remediation is not a completed one; the gap still holds.
    _entity(db_session)
    _recurring(db_session, 3)
    _remediation(db_session, "REM-1", alert_id="ALR-RK-1-0", status="FAILED")
    db_session.commit()

    assert len(_rule_findings(run_execution_gap_detection(db_session), "EG-003")) == 1


def test_remediation_gap_only_counts_true_positives(db_session: Session) -> None:
    _entity(db_session)
    _recurring(db_session, 2, true_positive=True)
    _recurring(db_session, 2, key="RK-1", true_positive=False, id_prefix="FP")  # same key, not TP
    db_session.commit()

    # Only 2 confirmed TP in the group -> below the min-3 threshold.
    assert _rule_findings(run_execution_gap_detection(db_session), "EG-003") == []


# ----- engine: determinism, dedup, read-only -------------------------------


def test_detection_is_deterministic_across_runs(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True)
    _investigation(db_session, "INV-1", "ALR-1")
    db_session.commit()

    first = run_execution_gap_detection(db_session)
    second = run_execution_gap_detection(db_session)
    assert first == second
    assert [f.finding_key for f in first.findings] == [f.finding_key for f in second.findings]


def test_findings_have_unique_keys(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True)
    _investigation(db_session, "INV-1", "ALR-1")
    _recurring(db_session, 3)
    db_session.commit()

    result = run_execution_gap_detection(db_session)
    keys = [f.finding_key for f in result.findings]
    assert len(keys) == len(set(keys))


def test_detection_does_not_mutate_database(db_session: Session) -> None:
    _entity(db_session)
    _alert(db_session, "ALR-1", is_true_positive=True)
    _investigation(db_session, "INV-1", "ALR-1")
    db_session.commit()

    before = {
        model.__name__: db_session.scalar(select(func.count()).select_from(model))
        for model in (Alert, Investigation, Escalation, Remediation, SocEntity, Asset)
    }
    run_execution_gap_detection(db_session)
    run_execution_gap_detection(db_session)
    after = {
        model.__name__: db_session.scalar(select(func.count()).select_from(model))
        for model in (Alert, Investigation, Escalation, Remediation, SocEntity, Asset)
    }
    assert before == after


def test_custom_config_changes_severity_threshold(db_session: Session) -> None:
    from app.models.enums import AlertSeverity

    _entity(db_session)
    _alert(db_session, "ALR-1", severity="HIGH", is_true_positive=True)
    _investigation(db_session, "INV-1", "ALR-1")
    db_session.commit()

    default = run_execution_gap_detection(db_session)
    assert _rule_findings(default, "EG-001") == []

    relaxed = run_execution_gap_detection(
        db_session,
        config=ExecutionGapConfig(escalation_min_severity=AlertSeverity.HIGH),
    )
    assert len(_rule_findings(relaxed, "EG-001")) == 1


def test_empty_database_yields_no_findings(db_session: Session) -> None:
    result = run_execution_gap_detection(db_session)
    assert result.findings == ()
    assert result.finding_count == 0




