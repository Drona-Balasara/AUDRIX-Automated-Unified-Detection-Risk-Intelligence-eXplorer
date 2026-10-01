"""Controlled vocabularies for the SAT-SA domain model.

These enumerations keep domain fields correct and comparable across the dataset
and later analytics. Values are stored as their string names in the database.
All enums derive from ``str`` so they serialize cleanly to CSV/JSON and compare
equal to their stored string form.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """String-valued enum whose ``value`` equals its member name."""

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return self.value


class Sector(StrEnum):
    """Peer-group sector classification for a SOC entity."""

    FINANCE = "FINANCE"
    HEALTHCARE = "HEALTHCARE"
    TECHNOLOGY = "TECHNOLOGY"
    GOVERNMENT = "GOVERNMENT"
    RETAIL = "RETAIL"
    ENERGY = "ENERGY"


class EntityScale(StrEnum):
    """Approximate operational scale tier of a SOC entity."""

    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"


class AssetCategory(StrEnum):
    SERVER = "SERVER"
    WORKSTATION = "WORKSTATION"
    NETWORK_DEVICE = "NETWORK_DEVICE"
    CLOUD_WORKLOAD = "CLOUD_WORKLOAD"
    DATABASE = "DATABASE"
    IDENTITY_SERVICE = "IDENTITY_SERVICE"
    APPLICATION = "APPLICATION"


class Criticality(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AlertStatus(StrEnum):
    NEW = "NEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_INVESTIGATION = "IN_INVESTIGATION"
    CLOSED = "CLOSED"
    SUPPRESSED = "SUPPRESSED"


class AlertCategory(StrEnum):
    MALWARE = "MALWARE"
    PHISHING = "PHISHING"
    UNAUTHORIZED_ACCESS = "UNAUTHORIZED_ACCESS"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    DATA_EXFILTRATION = "DATA_EXFILTRATION"
    RECONNAISSANCE = "RECONNAISSANCE"
    LATERAL_MOVEMENT = "LATERAL_MOVEMENT"
    MISCONFIGURATION = "MISCONFIGURATION"
    ANOMALOUS_BEHAVIOR = "ANOMALOUS_BEHAVIOR"


class DetectionSource(StrEnum):
    """Detection family that produced an alert (not a product name)."""

    ENDPOINT = "ENDPOINT"
    CORRELATION = "CORRELATION"
    NETWORK_IDS = "NETWORK_IDS"
    FIREWALL = "FIREWALL"
    EMAIL_GATEWAY = "EMAIL_GATEWAY"
    CLOUD_MONITOR = "CLOUD_MONITOR"
    THREAT_INTEL = "THREAT_INTEL"
    BEHAVIOR_ANALYTICS = "BEHAVIOR_ANALYTICS"


class InvestigationStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    CLOSED = "CLOSED"
    ABANDONED = "ABANDONED"


class ActionType(StrEnum):
    """Ordered investigation action vocabulary.

    Stored as individual records so later phases can reconstruct and compare
    investigation sequences rather than parsing a single workflow string.
    """

    OPEN = "OPEN"
    ASSET_LOOKUP = "ASSET_LOOKUP"
    EVENT_SEARCH = "EVENT_SEARCH"
    CORRELATION = "CORRELATION"
    CONTEXT_REVIEW = "CONTEXT_REVIEW"
    EVIDENCE_REVIEW = "EVIDENCE_REVIEW"
    ESCALATE = "ESCALATE"
    CONTAINMENT_REQUEST = "CONTAINMENT_REQUEST"
    REMEDIATION_REQUEST = "REMEDIATION_REQUEST"
    VALIDATE = "VALIDATE"
    CLOSE = "CLOSE"


class ActionOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    INCONCLUSIVE = "INCONCLUSIVE"
    FAILED = "FAILED"


class EscalationTarget(StrEnum):
    """Target role/category of an escalation (never a real person)."""

    TIER2 = "TIER2"
    TIER3 = "TIER3"
    INCIDENT_RESPONSE = "INCIDENT_RESPONSE"
    THREAT_HUNTING = "THREAT_HUNTING"
    MANAGEMENT = "MANAGEMENT"


class EscalationReason(StrEnum):
    SEVERITY = "SEVERITY"
    COMPLEXITY = "COMPLEXITY"
    CONFIRMED_INCIDENT = "CONFIRMED_INCIDENT"
    POLICY = "POLICY"
    SLA_RISK = "SLA_RISK"


class EscalationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    RESOLVED = "RESOLVED"


class RemediationType(StrEnum):
    ISOLATION = "ISOLATION"
    CREDENTIAL_RESET = "CREDENTIAL_RESET"
    PATCH = "PATCH"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    BLOCK_INDICATOR = "BLOCK_INDICATOR"
    USER_AWARENESS = "USER_AWARENESS"
    SYSTEM_REBUILD = "SYSTEM_REBUILD"


class RemediationStatus(StrEnum):
    REQUESTED = "REQUESTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TelemetryCategory(StrEnum):
    AUTHENTICATION = "AUTHENTICATION"
    NETWORK_FLOW = "NETWORK_FLOW"
    ENDPOINT_PROCESS = "ENDPOINT_PROCESS"
    CLOUD_AUDIT = "CLOUD_AUDIT"
    DNS = "DNS"
    EMAIL = "EMAIL"
    FILE_INTEGRITY = "FILE_INTEGRITY"


class TelemetrySourceStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    SILENT = "SILENT"
