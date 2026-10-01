"""Synthetic SOC dataset generator.

A single :class:`DatasetGenerator` run is fully determined by a
:class:`~app.datagen.config.GenerationConfig` plus its seed. The generator first
produces a realistic *normal baseline* (entities, assets, alerts and their
investigations, actions, escalations, remediations, telemetry) using controlled
probability distributions with genuine relationships, derives reported
performance metrics from those records, and only then plants a small number of
ground-truth scenarios as real record relationships. The normal baseline
substantially outnumbers the planted scenarios.

No real people, organizations, credentials, addresses, or payloads are produced;
every identifier is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from app.datagen.config import (
    SCALE_ALERTS_PER_PERIOD,
    SCALE_ANALYSTS,
    SCALE_ASSETS,
    SLA_ACK_MINUTES,
    GenerationConfig,
    build_periods,
)
from app.datagen.scenarios import SCENARIO_DETECTION, DetectionCategory, ScenarioType
from app.models.enums import (
    ActionOutcome,
    ActionType,
    AlertCategory,
    AlertSeverity,
    AlertStatus,
    AssetCategory,
    Criticality,
    DetectionSource,
    EscalationReason,
    EscalationStatus,
    EscalationTarget,
    InvestigationStatus,
    RemediationStatus,
    RemediationType,
    TelemetryCategory,
    TelemetrySourceStatus,
)
from app.datagen.schema import TABLE_NAMES

Row = dict[str, Any]


@dataclass
class Dataset:
    """In-memory result of a generation run."""

    tables: dict[str, list[Row]] = field(
        default_factory=lambda: {name: [] for name in TABLE_NAMES}
    )
    ground_truth: list[Row] = field(default_factory=list)

    def add(self, table: str, row: Row) -> Row:
        self.tables[table].append(row)
        return row


class IdAllocator:
    """Deterministic, monotonically increasing, zero-padded ID allocator."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, prefix: str, width: int) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{n:0{width}d}"


# --- Distribution tables -----------------------------------------------------

_SEVERITIES = (AlertSeverity.LOW, AlertSeverity.MEDIUM, AlertSeverity.HIGH, AlertSeverity.CRITICAL)
_SEVERITY_WEIGHTS = (0.45, 0.33, 0.17, 0.05)

_ACK_PROB = {"LOW": 0.80, "MEDIUM": 0.90, "HIGH": 0.97, "CRITICAL": 0.99}
_INVESTIGATE_PROB = {"LOW": 0.45, "MEDIUM": 0.72, "HIGH": 0.92, "CRITICAL": 0.98}
_TP_PROB = {"LOW": 0.12, "MEDIUM": 0.25, "HIGH": 0.50, "CRITICAL": 0.70}
_ESCALATE_PROB = {"LOW": 0.03, "MEDIUM": 0.12, "HIGH": 0.45, "CRITICAL": 0.75}

# Investigation duration medians (seconds) by severity; lognormal spread.
_DURATION_MEDIAN = {"LOW": 900, "MEDIUM": 2400, "HIGH": 5400, "CRITICAL": 10800}
# Typical evidence volume by severity (drives evidence_count and completeness).
_EXPECTED_EVIDENCE = {"LOW": 2, "MEDIUM": 4, "HIGH": 6, "CRITICAL": 8}
# Count of middle (non OPEN/CLOSE) investigation actions by severity.
_MIDDLE_ACTIONS = {"LOW": 2, "MEDIUM": 3, "HIGH": 5, "CRITICAL": 6}

_MIDDLE_ACTION_TYPES = (
    ActionType.ASSET_LOOKUP,
    ActionType.EVENT_SEARCH,
    ActionType.CORRELATION,
    ActionType.CONTEXT_REVIEW,
    ActionType.EVIDENCE_REVIEW,
)

# Expected telemetry category for each asset category.
_TELEMETRY_FOR_ASSET = {
    AssetCategory.SERVER: TelemetryCategory.ENDPOINT_PROCESS,
    AssetCategory.WORKSTATION: TelemetryCategory.ENDPOINT_PROCESS,
    AssetCategory.NETWORK_DEVICE: TelemetryCategory.NETWORK_FLOW,
    AssetCategory.CLOUD_WORKLOAD: TelemetryCategory.CLOUD_AUDIT,
    AssetCategory.DATABASE: TelemetryCategory.FILE_INTEGRITY,
    AssetCategory.IDENTITY_SERVICE: TelemetryCategory.AUTHENTICATION,
    AssetCategory.APPLICATION: TelemetryCategory.CLOUD_AUDIT,
}

# Remediation type by alert category (operational response, never a payload).
_REMEDIATION_FOR_CATEGORY = {
    AlertCategory.MALWARE: RemediationType.ISOLATION,
    AlertCategory.PHISHING: RemediationType.USER_AWARENESS,
    AlertCategory.UNAUTHORIZED_ACCESS: RemediationType.CREDENTIAL_RESET,
    AlertCategory.POLICY_VIOLATION: RemediationType.CONFIG_CHANGE,
    AlertCategory.DATA_EXFILTRATION: RemediationType.BLOCK_INDICATOR,
    AlertCategory.RECONNAISSANCE: RemediationType.BLOCK_INDICATOR,
    AlertCategory.LATERAL_MOVEMENT: RemediationType.ISOLATION,
    AlertCategory.MISCONFIGURATION: RemediationType.CONFIG_CHANGE,
    AlertCategory.ANOMALOUS_BEHAVIOR: RemediationType.PATCH,
}

# Baseline telemetry event volume by telemetry category (per period, scaled).
_TELEMETRY_BASE_EVENTS = {
    TelemetryCategory.AUTHENTICATION: 12000,
    TelemetryCategory.NETWORK_FLOW: 45000,
    TelemetryCategory.ENDPOINT_PROCESS: 30000,
    TelemetryCategory.CLOUD_AUDIT: 18000,
    TelemetryCategory.DNS: 60000,
    TelemetryCategory.EMAIL: 8000,
    TelemetryCategory.FILE_INTEGRITY: 5000,
}

_CRITICALITY_EVENT_FACTOR = {"LOW": 0.6, "MEDIUM": 1.0, "HIGH": 1.6, "CRITICAL": 2.4}

class DatasetGenerator:
    """Builds a :class:`Dataset` deterministically from a config + seed."""

    def __init__(self, config: GenerationConfig | None = None) -> None:
        self.config = config or GenerationConfig()
        self.rng = np.random.default_rng(self.config.seed)
        self.periods = build_periods(self.config.base_start, self.config.num_periods)
        self.ds = Dataset()
        # Index maps (hold references to the mutable row dicts).
        self.alert_by_id: dict[str, Row] = {}
        self.inv_by_id: dict[str, Row] = {}
        self.actions_by_inv: dict[str, list[Row]] = {}
        # Runtime context per entity (not all fields are persisted).
        self.entity_ctx: list[dict[str, Any]] = []
        self._ids = IdAllocator()
        self._analyst_counter = 0

    # --- small rng helpers ---------------------------------------------------
    def _rand(self) -> float:
        return float(self.rng.random())

    def _chance(self, p: float) -> bool:
        return self._rand() < p

    def _choice(self, options: tuple, weights: tuple | None = None):
        if weights is None:
            idx = int(self.rng.integers(0, len(options)))
            return options[idx]
        w = np.asarray(weights, dtype=float)
        w = w / w.sum()
        idx = int(self.rng.choice(len(options), p=w))
        return options[idx]

    def _lognormal(self, median: float, sigma: float) -> float:
        return float(median) * float(np.exp(self.rng.normal(0.0, sigma)))

    def _randint(self, lo: int, hi: int) -> int:
        return int(self.rng.integers(lo, hi + 1))

    def _scatter(self, start: datetime, span_seconds: float) -> datetime:
        return start + timedelta(seconds=self._rand() * span_seconds)

    def _period_index(self, dt: datetime) -> int:
        for i, (s, e) in enumerate(self.periods):
            if s <= dt < e:
                return i
        return len(self.periods) - 1

    # --- top-level orchestration --------------------------------------------
    def generate(self) -> Dataset:
        self._generate_entities_and_assets()
        for ctx in self.entity_ctx:
            for period_idx in range(self.config.num_periods):
                self._generate_period_alerts(ctx, period_idx)
        self._generate_telemetry()
        self._compute_performance_metrics()
        self._plant_scenarios()
        return self.ds

    # --- entities & assets ---------------------------------------------------
    def _generate_entities_and_assets(self) -> None:
        data_start = self.periods[0][0]
        data_end = self.periods[-1][1]
        created = self.config.base_start - timedelta(days=120)
        for spec in self.config.entity_specs:
            entity_id = self._ids.next("ENT", 2)
            n_assets = max(3, round(self._lognormal(SCALE_ASSETS[spec.scale], 0.12)))
            headcount = max(2, round(self._lognormal(SCALE_ANALYSTS[spec.scale], 0.10)))
            analyst_pool = []
            for _ in range(headcount):
                self._analyst_counter += 1
                analyst_pool.append(f"ANALYST-{self._analyst_counter:03d}")
            self.ds.add(
                "entities",
                {
                    "entity_id": entity_id,
                    "name": f"SOC Entity {entity_id[-2:]}",
                    "sector": spec.sector.value,
                    "peer_group": spec.sector.value,
                    "scale": spec.scale.value,
                    "asset_count_estimate": n_assets,
                    "analyst_headcount": headcount,
                    "created_at": created,
                    "data_period_start": data_start,
                    "data_period_end": data_end,
                },
            )
            ctx = {
                "entity_id": entity_id,
                "spec": spec,
                "analyst_pool": analyst_pool,
                "assets": [],
                "asset_weights": [],
            }
            self._generate_assets(ctx, n_assets, created)
            self.entity_ctx.append(ctx)

    _ASSET_CATEGORIES = (
        AssetCategory.SERVER,
        AssetCategory.WORKSTATION,
        AssetCategory.NETWORK_DEVICE,
        AssetCategory.CLOUD_WORKLOAD,
        AssetCategory.DATABASE,
        AssetCategory.IDENTITY_SERVICE,
        AssetCategory.APPLICATION,
    )
    _ASSET_CATEGORY_WEIGHTS = (0.26, 0.30, 0.10, 0.12, 0.08, 0.06, 0.08)
    _CRITICALITIES = (Criticality.LOW, Criticality.MEDIUM, Criticality.HIGH, Criticality.CRITICAL)
    _CRITICALITY_WEIGHTS = (0.40, 0.35, 0.18, 0.07)
    _ALERT_WEIGHT_BY_CRIT = {"LOW": 1.0, "MEDIUM": 1.8, "HIGH": 3.2, "CRITICAL": 5.0}

    def _generate_assets(self, ctx: dict, n_assets: int, created: datetime) -> None:
        for _ in range(n_assets):
            asset_id = self._ids.next("AST", 5)
            category = self._choice(self._ASSET_CATEGORIES, self._ASSET_CATEGORY_WEIGHTS)
            criticality = self._choice(self._CRITICALITIES, self._CRITICALITY_WEIGHTS)
            # Higher-criticality assets are nearly always monitored; low ones sometimes not.
            if criticality in (Criticality.HIGH, Criticality.CRITICAL):
                monitoring_expected = True
            elif criticality is Criticality.MEDIUM:
                monitoring_expected = self._chance(0.9)
            else:
                monitoring_expected = self._chance(0.7)
            expected_tel = _TELEMETRY_FOR_ASSET[category].value if monitoring_expected else None
            row = self.ds.add(
                "assets",
                {
                    "asset_id": asset_id,
                    "entity_id": ctx["entity_id"],
                    "name": f"{category.value}-{asset_id[-5:]}",
                    "category": category.value,
                    "criticality": criticality.value,
                    "monitoring_expected": monitoring_expected,
                    "expected_telemetry": expected_tel,
                    "created_at": created,
                },
            )
            ctx["assets"].append(row)
            ctx["asset_weights"].append(self._ALERT_WEIGHT_BY_CRIT[criticality.value])

    _ALERT_CATEGORIES = tuple(AlertCategory)
    _ALERT_CATEGORY_WEIGHTS = (0.16, 0.16, 0.14, 0.12, 0.06, 0.10, 0.07, 0.11, 0.08)
    _DETECTION_SOURCES = tuple(DetectionSource)
    _DETECTION_WEIGHTS = (0.22, 0.18, 0.14, 0.12, 0.12, 0.10, 0.05, 0.07)

    def _pick_asset(self, ctx: dict) -> Row:
        weights = np.asarray(ctx["asset_weights"], dtype=float)
        weights = weights / weights.sum()
        idx = int(self.rng.choice(len(ctx["assets"]), p=weights))
        return ctx["assets"][idx]

    def _draw_severity(self, criticality: str) -> AlertSeverity:
        w = list(_SEVERITY_WEIGHTS)
        # Critical/high assets skew their alerts toward higher severities.
        if criticality == "CRITICAL":
            w = [0.20, 0.28, 0.30, 0.22]
        elif criticality == "HIGH":
            w = [0.32, 0.33, 0.24, 0.11]
        return self._choice(_SEVERITIES, tuple(w))

    def _generate_period_alerts(self, ctx: dict, period_idx: int) -> None:
        spec = ctx["spec"]
        start, end = self.periods[period_idx]
        span = (end - start).total_seconds()
        seasonal = self.config.seasonal_factors[period_idx % len(self.config.seasonal_factors)]
        base = SCALE_ALERTS_PER_PERIOD[spec.scale] * seasonal
        n_alerts = int(self.rng.poisson(max(1.0, base)))
        for _ in range(n_alerts):
            asset = self._pick_asset(ctx)
            severity = self._draw_severity(asset["criticality"])
            category = self._choice(self._ALERT_CATEGORIES, self._ALERT_CATEGORY_WEIGHTS)
            created_at = self._scatter(start, span)
            # A minority of alerts belong to a recurring group (same asset+category).
            recurrence_key = None
            if self._chance(0.18):
                recurrence_key = f"RK-{asset['asset_id']}-{category.value}"
            self._build_alert_lifecycle(
                ctx, asset, severity, category, created_at, recurrence_key
            )

    def _build_alert_lifecycle(
        self,
        ctx: dict,
        asset: Row,
        severity: AlertSeverity,
        category: AlertCategory,
        created_at: datetime,
        recurrence_key: str | None,
    ) -> Row:
        sev = severity.value
        alert_id = self._ids.next("ALR", 6)
        alert: Row = {
            "alert_id": alert_id,
            "entity_id": ctx["entity_id"],
            "asset_id": asset["asset_id"],
            "severity": sev,
            "category": category.value,
            "detection_source": self._choice(
                self._DETECTION_SOURCES, self._DETECTION_WEIGHTS
            ).value,
            "status": AlertStatus.NEW.value,
            "created_at": created_at,
            "acknowledged_at": None,
            "closed_at": None,
            "recurrence_key": recurrence_key,
            "is_true_positive": None,
        }
        self.ds.add("alerts", alert)
        self.alert_by_id[alert_id] = alert

        if not self._chance(_ACK_PROB[sev]):
            # Unacknowledged: low-severity noise is sometimes suppressed outright.
            if severity is AlertSeverity.LOW and self._chance(0.4):
                alert["status"] = AlertStatus.SUPPRESSED.value
            return alert

        sla = SLA_ACK_MINUTES[sev]
        ack_delay_min = self._lognormal(sla * 0.45, 0.6)
        alert["acknowledged_at"] = created_at + timedelta(minutes=ack_delay_min)
        alert["status"] = AlertStatus.ACKNOWLEDGED.value

        if not self._chance(_INVESTIGATE_PROB[sev]):
            return alert

        self._build_investigation(ctx, alert, asset, severity, category)
        return alert

    def _build_investigation(
        self,
        ctx: dict,
        alert: Row,
        asset: Row,
        severity: AlertSeverity,
        category: AlertCategory,
    ) -> Row:
        sev = severity.value
        inv_id = self._ids.next("INV", 6)
        analyst = ctx["analyst_pool"][int(self.rng.integers(0, len(ctx["analyst_pool"])))]
        started_at = alert["acknowledged_at"] + timedelta(minutes=self._lognormal(8, 0.5))
        status = self._choice(
            (InvestigationStatus.CLOSED, InvestigationStatus.IN_PROGRESS, InvestigationStatus.ABANDONED),
            (0.88, 0.08, 0.04),
        )
        duration = max(300, int(self._lognormal(_DURATION_MEDIAN[sev], 0.5)))
        inv: Row = {
            "investigation_id": inv_id,
            "alert_id": alert["alert_id"],
            "entity_id": ctx["entity_id"],
            "analyst_id": analyst,
            "status": status.value,
            "started_at": started_at,
            "ended_at": None,
            "duration_seconds": None,
            "evidence_count": 0,
        }
        self.ds.add("investigations", inv)
        self.inv_by_id[inv_id] = inv

        exp_ev = _EXPECTED_EVIDENCE[sev]
        if status is InvestigationStatus.CLOSED:
            inv["duration_seconds"] = duration
            inv["ended_at"] = started_at + timedelta(seconds=duration)
            inv["evidence_count"] = max(1, int(self.rng.poisson(exp_ev * 0.95)))
            alert["closed_at"] = inv["ended_at"]
            alert["status"] = AlertStatus.CLOSED.value
            alert["is_true_positive"] = self._chance(_TP_PROB[sev])
        elif status is InvestigationStatus.IN_PROGRESS:
            inv["evidence_count"] = max(0, int(self.rng.poisson(exp_ev * 0.4)))
            alert["status"] = AlertStatus.IN_INVESTIGATION.value
        else:  # ABANDONED
            short = max(300, int(duration * 0.4))
            inv["duration_seconds"] = short
            inv["ended_at"] = started_at + timedelta(seconds=short)
            inv["evidence_count"] = max(0, int(self.rng.poisson(exp_ev * 0.3)))

        escalated = self._maybe_escalate(ctx, alert, inv, severity)
        remediated = self._maybe_remediate(alert, inv, severity, category)
        self._build_actions(inv, severity, escalated, remediated)
        return inv

    def _inv_span(self, inv: Row) -> float:
        if inv["duration_seconds"]:
            return float(inv["duration_seconds"])
        return 3600.0

    def _maybe_escalate(
        self, ctx: dict, alert: Row, inv: Row, severity: AlertSeverity
    ) -> bool:
        sev = severity.value
        prob = _ESCALATE_PROB[sev]
        if alert["is_true_positive"] and severity in (AlertSeverity.HIGH, AlertSeverity.CRITICAL):
            prob = min(0.98, prob + 0.15)
        if not self._chance(prob):
            return False
        created_at = inv["started_at"] + timedelta(seconds=self._rand() * self._inv_span(inv) * 0.6)
        if severity is AlertSeverity.CRITICAL:
            target = self._choice(
                (EscalationTarget.INCIDENT_RESPONSE, EscalationTarget.TIER3, EscalationTarget.MANAGEMENT),
                (0.6, 0.3, 0.1),
            )
            reason = EscalationReason.CONFIRMED_INCIDENT if alert["is_true_positive"] else EscalationReason.SEVERITY
        else:
            target = self._choice(
                (EscalationTarget.TIER2, EscalationTarget.TIER3, EscalationTarget.THREAT_HUNTING),
                (0.6, 0.3, 0.1),
            )
            reason = self._choice(
                (EscalationReason.SEVERITY, EscalationReason.COMPLEXITY, EscalationReason.SLA_RISK, EscalationReason.POLICY),
                (0.4, 0.3, 0.2, 0.1),
            )
        status = self._choice(
            (EscalationStatus.RESOLVED, EscalationStatus.ACCEPTED, EscalationStatus.PENDING, EscalationStatus.REJECTED),
            (0.7, 0.15, 0.1, 0.05),
        )
        resolved_at = None
        if status is EscalationStatus.RESOLVED:
            resolved_at = created_at + timedelta(seconds=self._lognormal(3600, 0.6))
        self.ds.add(
            "escalations",
            {
                "escalation_id": self._ids.next("ESC", 5),
                "alert_id": alert["alert_id"],
                "investigation_id": inv["investigation_id"],
                "created_at": created_at,
                "target": target.value,
                "reason": reason.value,
                "status": status.value,
                "resolved_at": resolved_at,
            },
        )
        return True

    _REMEDIATE_TP_PROB = {"LOW": 0.30, "MEDIUM": 0.50, "HIGH": 0.75, "CRITICAL": 0.90}

    def _maybe_remediate(
        self, alert: Row, inv: Row, severity: AlertSeverity, category: AlertCategory
    ) -> bool:
        sev = severity.value
        if alert["is_true_positive"]:
            prob = self._REMEDIATE_TP_PROB[sev]
            if alert["recurrence_key"]:
                prob = min(0.98, prob + 0.10)
        else:
            prob = 0.03
        if not self._chance(prob):
            return False
        base_time = inv["ended_at"] or inv["started_at"] + timedelta(seconds=self._inv_span(inv))
        created_at = base_time + timedelta(minutes=self._lognormal(20, 0.5))
        status = self._choice(
            (RemediationStatus.COMPLETED, RemediationStatus.IN_PROGRESS, RemediationStatus.REQUESTED,
             RemediationStatus.FAILED, RemediationStatus.CANCELLED),
            (0.80, 0.10, 0.05, 0.03, 0.02),
        )
        completed_at = None
        successful = None
        if status is RemediationStatus.COMPLETED:
            completed_at = created_at + timedelta(seconds=self._lognormal(7200, 0.6))
            successful = self._chance(0.92)
        elif status is RemediationStatus.FAILED:
            completed_at = created_at + timedelta(seconds=self._lognormal(5400, 0.6))
            successful = False
        self.ds.add(
            "remediations",
            {
                "remediation_id": self._ids.next("REM", 5),
                "alert_id": alert["alert_id"],
                "investigation_id": inv["investigation_id"],
                "created_at": created_at,
                "completed_at": completed_at,
                "remediation_type": _REMEDIATION_FOR_CATEGORY[category].value,
                "status": status.value,
                "successful": successful,
            },
        )
        return True

    def _build_actions(
        self, inv: Row, severity: AlertSeverity, escalated: bool, remediated: bool
    ) -> None:
        sev = severity.value
        status = inv["status"]
        closed = status == InvestigationStatus.CLOSED.value
        abandoned = status == InvestigationStatus.ABANDONED.value

        action_types: list[ActionType] = [ActionType.OPEN]
        n_middle = max(1, _MIDDLE_ACTIONS[sev] + self._randint(-1, 1))
        if abandoned:
            n_middle = max(1, n_middle - 2)
        action_types.append(ActionType.ASSET_LOOKUP)
        for _ in range(n_middle - 1):
            action_types.append(
                self._choice(
                    _MIDDLE_ACTION_TYPES,
                    (0.15, 0.30, 0.22, 0.18, 0.15),
                )
            )
        if escalated:
            action_types.append(ActionType.ESCALATE)
        if remediated:
            if self._chance(0.5):
                action_types.append(ActionType.CONTAINMENT_REQUEST)
            action_types.append(ActionType.REMEDIATION_REQUEST)
        if closed and (inv["evidence_count"] >= _EXPECTED_EVIDENCE[sev] * 0.6
                       or severity in (AlertSeverity.HIGH, AlertSeverity.CRITICAL)):
            action_types.append(ActionType.VALIDATE)
        if closed:
            action_types.append(ActionType.CLOSE)
        self._write_actions(inv, action_types)

    def _write_actions(self, inv: Row, action_types: list[ActionType]) -> None:
        n = len(action_types)
        if inv["ended_at"] is not None:
            total_span = (inv["ended_at"] - inv["started_at"]).total_seconds()
        else:
            total_span = self._inv_span(inv) * 0.6
        total_span = max(float(n), total_span)
        props = self.rng.dirichlet(np.ones(n))
        cum = 0.0
        rows: list[Row] = []
        for seq, atype in enumerate(action_types, start=1):
            occurred_at = inv["started_at"] + timedelta(seconds=total_span * cum)
            seg = float(props[seq - 1])
            cum += seg
            if atype in (ActionType.OPEN, ActionType.CLOSE):
                outcome = None
            elif atype in (ActionType.ESCALATE, ActionType.CONTAINMENT_REQUEST, ActionType.REMEDIATION_REQUEST):
                outcome = ActionOutcome.SUCCESS
            else:
                outcome = self._choice(
                    (ActionOutcome.SUCCESS, ActionOutcome.INCONCLUSIVE, ActionOutcome.FAILED),
                    (0.72, 0.22, 0.06),
                )
            row = self.ds.add(
                "investigation_actions",
                {
                    "action_id": self._ids.next("ACT", 7),
                    "investigation_id": inv["investigation_id"],
                    "sequence_number": seq,
                    "action_type": atype.value,
                    "occurred_at": occurred_at,
                    "duration_seconds": max(1, int(total_span * seg)),
                    "outcome": outcome.value if outcome else None,
                },
            )
            rows.append(row)
        self.actions_by_inv[inv["investigation_id"]] = rows

    # --- telemetry -----------------------------------------------------------
    def _generate_telemetry(self) -> None:
        for ctx in self.entity_ctx:
            for asset in ctx["assets"]:
                if not asset["monitoring_expected"]:
                    continue
                category = TelemetryCategory(asset["expected_telemetry"])
                base = _TELEMETRY_BASE_EVENTS[category]
                crit_factor = _CRITICALITY_EVENT_FACTOR[asset["criticality"]]
                for period_idx, (start, end) in enumerate(self.periods):
                    seasonal = self.config.seasonal_factors[
                        period_idx % len(self.config.seasonal_factors)
                    ]
                    noise = self._lognormal(1.0, 0.15)
                    event_count = max(1, int(base * crit_factor * seasonal * noise))
                    activity_level = float(np.clip(self.rng.normal(1.0, 0.15), 0.05, 3.0))
                    source_status = self._choice(
                        (TelemetrySourceStatus.HEALTHY, TelemetrySourceStatus.DEGRADED),
                        (0.93, 0.07),
                    )
                    self.ds.add(
                        "telemetry",
                        {
                            "telemetry_id": self._ids.next("TLM", 6),
                            "entity_id": ctx["entity_id"],
                            "asset_id": asset["asset_id"],
                            "period_start": start,
                            "period_end": end,
                            "category": category.value,
                            "event_count": event_count,
                            "activity_level": round(activity_level, 4),
                            "source_status": source_status.value,
                            "expected": True,
                        },
                    )

    # --- derived performance metrics ----------------------------------------
    @staticmethod
    def _safe_div(a: float, b: float, default: float) -> float:
        return a / b if b else default

    def _compute_performance_metrics(self) -> None:
        esc_by_inv: dict[str, list[Row]] = {}
        for e in self.ds.tables["escalations"]:
            esc_by_inv.setdefault(e["investigation_id"], []).append(e)
        rem_by_inv: dict[str, list[Row]] = {}
        for r in self.ds.tables["remediations"]:
            rem_by_inv.setdefault(r["investigation_id"], []).append(r)
        invs_by_alert: dict[str, Row] = {i["alert_id"]: i for i in self.ds.tables["investigations"]}

        for ctx in self.entity_ctx:
            eid = ctx["entity_id"]
            entity_alerts = [a for a in self.ds.tables["alerts"] if a["entity_id"] == eid]
            for start, end in self.periods:
                alerts_p = [a for a in entity_alerts if start <= a["created_at"] < end]
                invs_p = [invs_by_alert[a["alert_id"]] for a in alerts_p if a["alert_id"] in invs_by_alert]
                closed = [i for i in invs_p if i["status"] == InvestigationStatus.CLOSED.value]
                self._write_metric_row(eid, start, end, alerts_p, invs_p, closed, esc_by_inv, rem_by_inv)

    def _write_metric_row(self, eid, start, end, alerts_p, invs_p, closed, esc_by_inv, rem_by_inv) -> None:
        acked = [a for a in alerts_p if a["acknowledged_at"] is not None]
        within_sla = sum(
            1 for a in acked
            if (a["acknowledged_at"] - a["created_at"]).total_seconds() / 60.0
            <= SLA_ACK_MINUTES[a["severity"]]
        )
        mttr_hours = self._safe_div(
            sum(i["duration_seconds"] for i in closed) / 3600.0, len(closed), 0.0
        )
        n_escalated = sum(1 for i in invs_p if esc_by_inv.get(i["investigation_id"]))
        tp_alerts = [a for a in alerts_p if a["is_true_positive"]]
        inv_by_alert_p = {i["alert_id"]: i for i in invs_p}
        remediated_tp = 0
        for a in tp_alerts:
            inv = inv_by_alert_p.get(a["alert_id"])
            if inv and any(
                r["status"] == RemediationStatus.COMPLETED.value
                for r in rem_by_inv.get(inv["investigation_id"], [])
            ):
                remediated_tp += 1
        full_wf = sum(
            1 for i in closed
            if self._has_full_workflow(i["investigation_id"])
        )
        ev_scores = [
            min(1.0, i["evidence_count"] / _EXPECTED_EVIDENCE[self.alert_by_id[i["alert_id"]]["severity"]])
            for i in closed
        ]
        self.ds.add(
            "performance_metrics",
            {
                "metric_id": self._ids.next("PMET", 4),
                "entity_id": eid,
                "period_start": start,
                "period_end": end,
                "mttr_hours": round(mttr_hours, 4),
                "closure_rate": round(self._safe_div(len(closed), len(invs_p), 1.0), 4),
                "sla_compliance": round(self._safe_div(within_sla, len(acked), 1.0), 4),
                "escalation_rate": round(self._safe_div(n_escalated, len(invs_p), 0.0), 4),
                "investigation_completeness": round(self._safe_div(full_wf, len(closed), 1.0), 4),
                "recurrence_rate": round(
                    self._safe_div(sum(1 for a in alerts_p if a["recurrence_key"]), len(alerts_p), 0.0), 4
                ),
                "remediation_rate": round(self._safe_div(remediated_tp, len(tp_alerts), 1.0), 4),
                "evidence_completeness": round(float(np.mean(ev_scores)) if ev_scores else 1.0, 4),
            },
        )

    def _has_full_workflow(self, inv_id: str) -> bool:
        types = {a["action_type"] for a in self.actions_by_inv.get(inv_id, [])}
        return ActionType.VALIDATE.value in types and ActionType.CLOSE.value in types

    # --- ground-truth scenario planting -------------------------------------
    def _add_ground_truth(
        self,
        scenario_type: str,
        reason: str,
        *,
        entity_id: str | None = None,
        asset_id: str | None = None,
        alert_id: str | None = None,
        investigation_id: str | None = None,
        recurrence_key: str | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
    ) -> None:
        self.ds.ground_truth.append(
            {
                "scenario_id": self._ids.next("SCN", 4),
                "scenario_type": scenario_type,
                "entity_id": entity_id,
                "asset_id": asset_id,
                "alert_id": alert_id,
                "investigation_id": investigation_id,
                "recurrence_key": recurrence_key,
                "period_start": period_start,
                "period_end": period_end,
                "expected_detection_category": SCENARIO_DETECTION[scenario_type],
                "reason": reason,
            }
        )

    def _new_asset(self, ctx: dict, category: AssetCategory, criticality: Criticality,
                   monitoring_expected: bool) -> Row:
        asset_id = self._ids.next("AST", 5)
        expected_tel = _TELEMETRY_FOR_ASSET[category].value if monitoring_expected else None
        row = self.ds.add(
            "assets",
            {
                "asset_id": asset_id,
                "entity_id": ctx["entity_id"],
                "name": f"{category.value}-{asset_id[-5:]}",
                "category": category.value,
                "criticality": criticality.value,
                "monitoring_expected": monitoring_expected,
                "expected_telemetry": expected_tel,
                "created_at": self.config.base_start - timedelta(days=120),
            },
        )
        ctx["assets"].append(row)
        ctx["asset_weights"].append(self._ALERT_WEIGHT_BY_CRIT[criticality.value])
        return row

    def _plant_scenarios(self) -> None:
        self._plant_normal_baseline_controls()
        self._plant_critical_without_escalation()
        self._plant_acknowledged_without_investigation()
        self._plant_fast_investigation()
        self._plant_recurring_without_remediation()
        self._plant_missing_telemetry()
        self._plant_telemetry_disappearance()
        self._plant_repetitive_workflow()
        self._plant_metric_divergence()

    def _plant_alert(self, ctx: dict, asset: Row, severity: AlertSeverity,
                     category: AlertCategory, created_at: datetime, *, status: AlertStatus,
                     acknowledged_at: datetime | None = None, closed_at: datetime | None = None,
                     is_tp: bool | None = None, recurrence_key: str | None = None) -> Row:
        alert_id = self._ids.next("ALR", 6)
        alert: Row = {
            "alert_id": alert_id,
            "entity_id": ctx["entity_id"],
            "asset_id": asset["asset_id"],
            "severity": severity.value,
            "category": category.value,
            "detection_source": self._choice(self._DETECTION_SOURCES, self._DETECTION_WEIGHTS).value,
            "status": status.value,
            "created_at": created_at,
            "acknowledged_at": acknowledged_at,
            "closed_at": closed_at,
            "recurrence_key": recurrence_key,
            "is_true_positive": is_tp,
        }
        self.ds.add("alerts", alert)
        self.alert_by_id[alert_id] = alert
        return alert

    def _plant_investigation(self, ctx: dict, alert: Row, started_at: datetime,
                             duration: int | None, status: InvestigationStatus,
                             evidence_count: int, action_types: list[ActionType],
                             analyst: str | None = None) -> Row:
        inv_id = self._ids.next("INV", 6)
        ended_at = started_at + timedelta(seconds=duration) if duration is not None else None
        inv: Row = {
            "investigation_id": inv_id,
            "alert_id": alert["alert_id"],
            "entity_id": ctx["entity_id"],
            "analyst_id": analyst or ctx["analyst_pool"][0],
            "status": status.value,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": duration,
            "evidence_count": evidence_count,
        }
        self.ds.add("investigations", inv)
        self.inv_by_id[inv_id] = inv
        self._write_actions(inv, action_types)
        return inv

    def _plant_normal_baseline_controls(self) -> None:
        # Negative controls: label a handful of unambiguously healthy baseline
        # investigations (critical/high, closed, confirmed, escalated, full
        # workflow) as NORMAL so later analytics must NOT flag them.
        escalated_invs = {e["investigation_id"] for e in self.ds.tables["escalations"]}
        ctx = self.entity_ctx[0]
        labelled = 0
        for inv in self.ds.tables["investigations"]:
            if labelled >= 6:
                break
            if inv["entity_id"] != ctx["entity_id"]:
                continue
            if inv["status"] != InvestigationStatus.CLOSED.value:
                continue
            alert = self.alert_by_id[inv["alert_id"]]
            if alert["severity"] not in (AlertSeverity.HIGH.value, AlertSeverity.CRITICAL.value):
                continue
            if not alert["is_true_positive"]:
                continue
            if inv["investigation_id"] not in escalated_invs:
                continue
            if not self._has_full_workflow(inv["investigation_id"]):
                continue
            self._add_ground_truth(
                ScenarioType.NORMAL_BASELINE,
                "Confirmed high/critical alert fully investigated, escalated, and closed with complete evidence.",
                entity_id=alert["entity_id"],
                asset_id=alert["asset_id"],
                alert_id=alert["alert_id"],
                investigation_id=inv["investigation_id"],
                period_start=self.periods[self._period_index(alert["created_at"])][0],
                period_end=self.periods[self._period_index(alert["created_at"])][1],
            )
            labelled += 1

    def _pick_critical_asset(self, ctx: dict) -> Row:
        criticals = [a for a in ctx["assets"] if a["criticality"] == Criticality.CRITICAL.value]
        if criticals:
            return criticals[int(self.rng.integers(0, len(criticals)))]
        return self._new_asset(ctx, AssetCategory.SERVER, Criticality.CRITICAL, True)

    def _period_time(self, period_idx: int, fraction: float) -> datetime:
        start, end = self.periods[period_idx]
        return start + timedelta(seconds=(end - start).total_seconds() * fraction)

    _FULL_WORKFLOW = [
        ActionType.OPEN, ActionType.ASSET_LOOKUP, ActionType.EVENT_SEARCH,
        ActionType.CORRELATION, ActionType.CONTEXT_REVIEW, ActionType.EVIDENCE_REVIEW,
        ActionType.VALIDATE, ActionType.CLOSE,
    ]

    def _plant_critical_without_escalation(self) -> None:
        for i, period_idx in ((0, 1), (2, 2), (4, 3)):
            ctx = self.entity_ctx[i]
            asset = self._pick_critical_asset(ctx)
            created = self._period_time(period_idx, 0.3)
            ack = created + timedelta(minutes=self._lognormal(6, 0.4))
            alert = self._plant_alert(
                ctx, asset, AlertSeverity.CRITICAL, AlertCategory.UNAUTHORIZED_ACCESS, created,
                status=AlertStatus.CLOSED, acknowledged_at=ack, is_tp=True,
            )
            started = ack + timedelta(minutes=self._lognormal(8, 0.3))
            duration = max(300, int(self._lognormal(_DURATION_MEDIAN["CRITICAL"], 0.3)))
            alert["closed_at"] = started + timedelta(seconds=duration)
            inv = self._plant_investigation(
                ctx, alert, started, duration, InvestigationStatus.CLOSED,
                evidence_count=_EXPECTED_EVIDENCE["CRITICAL"], action_types=list(self._FULL_WORKFLOW),
            )
            self._add_ground_truth(
                ScenarioType.CRITICAL_ALERT_WITHOUT_ESCALATION,
                "Confirmed CRITICAL alert fully investigated and closed but never escalated.",
                entity_id=ctx["entity_id"], asset_id=asset["asset_id"],
                alert_id=alert["alert_id"], investigation_id=inv["investigation_id"],
                period_start=self.periods[period_idx][0], period_end=self.periods[period_idx][1],
            )

    def _plant_acknowledged_without_investigation(self) -> None:
        for i, period_idx, sev in ((0, 2, AlertSeverity.CRITICAL), (1, 1, AlertSeverity.HIGH), (3, 3, AlertSeverity.HIGH)):
            ctx = self.entity_ctx[i]
            asset = self._pick_critical_asset(ctx)
            created = self._period_time(period_idx, 0.5)
            ack = created + timedelta(minutes=self._lognormal(5, 0.3))
            alert = self._plant_alert(
                ctx, asset, sev, AlertCategory.MALWARE, created,
                status=AlertStatus.ACKNOWLEDGED, acknowledged_at=ack, is_tp=None,
            )
            self._add_ground_truth(
                ScenarioType.ACKNOWLEDGED_WITHOUT_INVESTIGATION,
                f"{sev.value} alert acknowledged within SLA but no investigation was ever opened.",
                entity_id=ctx["entity_id"], asset_id=asset["asset_id"], alert_id=alert["alert_id"],
                period_start=self.periods[period_idx][0], period_end=self.periods[period_idx][1],
            )

    def _plant_fast_investigation(self) -> None:
        # Critical/high alert "investigated" in under two minutes with only
        # open/close actions and no evidence — a quality anomaly.
        for i, period_idx, sev in ((2, 1, AlertSeverity.CRITICAL), (4, 2, AlertSeverity.HIGH), (5, 3, AlertSeverity.CRITICAL)):
            ctx = self.entity_ctx[i]
            asset = self._pick_critical_asset(ctx)
            created = self._period_time(period_idx, 0.6)
            ack = created + timedelta(minutes=self._lognormal(4, 0.3))
            started = ack + timedelta(minutes=self._lognormal(3, 0.3))
            duration = self._randint(45, 115)
            alert = self._plant_alert(
                ctx, asset, sev, AlertCategory.DATA_EXFILTRATION, created,
                status=AlertStatus.CLOSED, acknowledged_at=ack,
                closed_at=started + timedelta(seconds=duration), is_tp=False,
            )
            inv = self._plant_investigation(
                ctx, alert, started, duration, InvestigationStatus.CLOSED,
                evidence_count=self._randint(0, 1), action_types=[ActionType.OPEN, ActionType.CLOSE],
            )
            self._add_ground_truth(
                ScenarioType.SUSPICIOUSLY_FAST_INVESTIGATION,
                f"{sev.value} alert closed after a {duration}s investigation with no evidence gathered.",
                entity_id=ctx["entity_id"], asset_id=asset["asset_id"],
                alert_id=alert["alert_id"], investigation_id=inv["investigation_id"],
                period_start=self.periods[period_idx][0], period_end=self.periods[period_idx][1],
            )

    def _plant_recurring_without_remediation(self) -> None:
        for i, category in ((1, AlertCategory.PHISHING), (3, AlertCategory.MISCONFIGURATION)):
            ctx = self.entity_ctx[i]
            asset = self._new_asset(ctx, AssetCategory.APPLICATION, Criticality.HIGH, True)
            rkey = f"RK-{asset['asset_id']}-{category.value}"
            n = 5
            for period_idx in range(n):
                created = self._period_time(period_idx, 0.4)
                ack = created + timedelta(minutes=self._lognormal(10, 0.4))
                started = ack + timedelta(minutes=self._lognormal(8, 0.3))
                duration = max(600, int(self._lognormal(_DURATION_MEDIAN["HIGH"], 0.4)))
                alert = self._plant_alert(
                    ctx, asset, AlertSeverity.HIGH, category, created,
                    status=AlertStatus.CLOSED, acknowledged_at=ack,
                    closed_at=started + timedelta(seconds=duration), is_tp=True, recurrence_key=rkey,
                )
                # Investigated and closed, but deliberately NO remediation record.
                self._plant_investigation(
                    ctx, alert, started, duration, InvestigationStatus.CLOSED,
                    evidence_count=_EXPECTED_EVIDENCE["HIGH"],
                    action_types=[ActionType.OPEN, ActionType.ASSET_LOOKUP, ActionType.EVENT_SEARCH,
                                  ActionType.CORRELATION, ActionType.VALIDATE, ActionType.CLOSE],
                )
            self._add_ground_truth(
                ScenarioType.RECURRING_ALERTS_WITHOUT_REMEDIATION,
                f"{n} confirmed recurring alerts on one asset over consecutive periods with no remediation performed.",
                entity_id=ctx["entity_id"], asset_id=asset["asset_id"], recurrence_key=rkey,
                period_start=self.periods[0][0], period_end=self.periods[n - 1][1],
            )

    def _emit_telemetry(self, ctx: dict, asset: Row, period_idx: int,
                        source_status: TelemetrySourceStatus = TelemetrySourceStatus.HEALTHY) -> None:
        start, end = self.periods[period_idx]
        category = TelemetryCategory(asset["expected_telemetry"])
        base = _TELEMETRY_BASE_EVENTS[category] * _CRITICALITY_EVENT_FACTOR[asset["criticality"]]
        seasonal = self.config.seasonal_factors[period_idx % len(self.config.seasonal_factors)]
        event_count = max(1, int(base * seasonal * self._lognormal(1.0, 0.15)))
        self.ds.add(
            "telemetry",
            {
                "telemetry_id": self._ids.next("TLM", 6),
                "entity_id": ctx["entity_id"],
                "asset_id": asset["asset_id"],
                "period_start": start,
                "period_end": end,
                "category": category.value,
                "event_count": event_count,
                "activity_level": round(float(np.clip(self.rng.normal(1.0, 0.15), 0.05, 3.0)), 4),
                "source_status": source_status.value,
                "expected": True,
            },
        )

    def _plant_missing_telemetry(self) -> None:
        for i in (0, 2):
            ctx = self.entity_ctx[i]
            # Critical asset where monitoring is expected but NO telemetry is ever produced.
            asset = self._new_asset(ctx, AssetCategory.DATABASE, Criticality.CRITICAL, True)
            self._add_ground_truth(
                ScenarioType.MISSING_TELEMETRY_CRITICAL_ASSET,
                "Critical asset flagged as monitored produces no telemetry in any period.",
                entity_id=ctx["entity_id"], asset_id=asset["asset_id"],
                period_start=self.periods[0][0], period_end=self.periods[-1][1],
            )

    def _plant_telemetry_disappearance(self) -> None:
        cutoff = 3  # normal telemetry through period index 2, then silence.
        for i in (3, 4):
            ctx = self.entity_ctx[i]
            asset = self._new_asset(ctx, AssetCategory.IDENTITY_SERVICE, Criticality.HIGH, True)
            for period_idx in range(cutoff):
                status = TelemetrySourceStatus.DEGRADED if period_idx == cutoff - 1 else TelemetrySourceStatus.HEALTHY
                self._emit_telemetry(ctx, asset, period_idx, status)
            # Periods cutoff..end: no telemetry emitted at all (disappearance).
            self._add_ground_truth(
                ScenarioType.TELEMETRY_DISAPPEARANCE,
                "Asset reported normal telemetry for three periods then went silent for the remainder.",
                entity_id=ctx["entity_id"], asset_id=asset["asset_id"],
                period_start=self.periods[cutoff][0], period_end=self.periods[-1][1],
            )

    def _plant_repetitive_workflow(self) -> None:
        ctx = self.entity_ctx[2]
        asset = self._new_asset(ctx, AssetCategory.WORKSTATION, Criticality.MEDIUM, True)
        analyst = ctx["analyst_pool"][0]
        pattern = [ActionType.OPEN, ActionType.EVENT_SEARCH, ActionType.EVENT_SEARCH,
                   ActionType.EVENT_SEARCH, ActionType.CLOSE]
        period_idx = 2
        for k in range(4):
            created = self._period_time(period_idx, 0.2 + 0.15 * k)
            ack = created + timedelta(minutes=self._lognormal(7, 0.3))
            started = ack + timedelta(minutes=self._lognormal(6, 0.3))
            duration = 1800
            alert = self._plant_alert(
                ctx, asset, AlertSeverity.MEDIUM, AlertCategory.ANOMALOUS_BEHAVIOR, created,
                status=AlertStatus.CLOSED, acknowledged_at=ack,
                closed_at=started + timedelta(seconds=duration), is_tp=False,
            )
            inv = self._plant_investigation(
                ctx, alert, started, duration, InvestigationStatus.CLOSED,
                evidence_count=3, action_types=list(pattern), analyst=analyst,
            )
            self._add_ground_truth(
                ScenarioType.REPETITIVE_INVESTIGATION_WORKFLOW,
                "One analyst closes multiple alerts with an identical, mechanical action sequence.",
                entity_id=ctx["entity_id"], asset_id=asset["asset_id"],
                alert_id=alert["alert_id"], investigation_id=inv["investigation_id"],
                period_start=self.periods[period_idx][0], period_end=self.periods[period_idx][1],
            )

    def _plant_metric_divergence(self) -> None:
        ctx = self.entity_ctx[3]
        eid = ctx["entity_id"]
        n = self.config.num_periods

        # 1) Genuine operational deterioration in the later periods: strip evidence
        # from this entity's closed investigations started in the back half.
        half = n // 2
        for inv in self.ds.tables["investigations"]:
            if inv["entity_id"] != eid or inv["status"] != InvestigationStatus.CLOSED.value:
                continue
            if self._period_index(inv["started_at"]) >= half:
                inv["evidence_count"] = 1

        # 2) Recurring, un-remediated alerts in the later periods (operational risk).
        asset = self._new_asset(ctx, AssetCategory.CLOUD_WORKLOAD, Criticality.HIGH, True)
        rkey = f"RK-{asset['asset_id']}-{AlertCategory.LATERAL_MOVEMENT.value}"
        for period_idx in range(half, n):
            created = self._period_time(period_idx, 0.35)
            ack = created + timedelta(minutes=self._lognormal(12, 0.4))
            started = ack + timedelta(minutes=self._lognormal(9, 0.3))
            duration = max(600, int(self._lognormal(_DURATION_MEDIAN["HIGH"], 0.4)))
            alert = self._plant_alert(
                ctx, asset, AlertSeverity.HIGH, AlertCategory.LATERAL_MOVEMENT, created,
                status=AlertStatus.CLOSED, acknowledged_at=ack,
                closed_at=started + timedelta(seconds=duration), is_tp=True, recurrence_key=rkey,
            )
            self._plant_investigation(
                ctx, alert, started, duration, InvestigationStatus.CLOSED, evidence_count=1,
                action_types=[ActionType.OPEN, ActionType.EVENT_SEARCH, ActionType.CLOSE],
            )

        # 3) Override reported metrics so headline KPIs improve while
        # operational-quality indicators deteriorate over the periods.
        rows = sorted(
            (m for m in self.ds.tables["performance_metrics"] if m["entity_id"] == eid),
            key=lambda m: m["period_start"],
        )
        for idx, m in enumerate(rows):
            t = idx / max(1, len(rows) - 1)
            m["closure_rate"] = round(0.80 + 0.16 * t, 4)
            m["sla_compliance"] = round(0.82 + 0.15 * t, 4)
            m["mttr_hours"] = round(7.0 - 4.0 * t, 4)
            m["escalation_rate"] = round(0.30 - 0.08 * t, 4)
            m["investigation_completeness"] = round(0.80 - 0.38 * t, 4)
            m["evidence_completeness"] = round(0.82 - 0.37 * t, 4)
            m["recurrence_rate"] = round(0.10 + 0.24 * t, 4)
            m["remediation_rate"] = round(0.78 - 0.34 * t, 4)

        self._add_ground_truth(
            ScenarioType.METRIC_RISK_DIVERGENCE,
            "Reported headline KPIs improve across periods while evidence/remediation quality and recurrence deteriorate.",
            entity_id=eid, period_start=self.periods[0][0], period_end=self.periods[-1][1],
        )

