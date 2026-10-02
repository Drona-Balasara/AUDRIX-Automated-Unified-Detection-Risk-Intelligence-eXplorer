"""Ingestion orchestration, independent of the HTTP layer.

Ties together parsing, validation, normalization, and transactional persistence,
and records a compact audit row for each import. The same functions are used by
the API routes and by tests, so there is a single code path for ingestion
behavior. All logging here is safe: it records counts, dataset type, and the
server-generated import identifier — never file contents, rows, paths, or
secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion.errors import ErrorCode, ErrorSeverity, ValidationReport
from app.ingestion.persistence import load_existing_ids, load_reference_ids, persist_rows
from app.ingestion.schemas import SUPPORTED_DATASET_TYPES, get_schema
from app.ingestion.validation import ValidationContext, ValidationResult, validate_dataset
from app.models.enums import ImportMode, ImportStatus
from app.models.ingestion import ImportRecord

logger = get_logger(__name__)


class ImportOutcome:
    """Result of an import attempt (report + audit fields)."""

    def __init__(
        self,
        import_id: str,
        status: ImportStatus,
        report: ValidationReport,
        mode: ImportMode,
        received_at: datetime,
        completed_at: datetime,
        inserted: int,
        summary: str,
    ) -> None:
        self.import_id = import_id
        self.status = status
        self.report = report
        self.mode = mode
        self.received_at = received_at
        self.completed_at = completed_at
        self.inserted = inserted
        self.summary = summary

    @property
    def committed(self) -> bool:
        return self.status is ImportStatus.COMPLETED


def _new_import_id() -> str:
    return f"IMP-{uuid4().hex[:16]}"


def _unsupported_report(dataset_type: str) -> ValidationReport:
    report = ValidationReport(dataset_type=dataset_type)
    report.add_code(
        ErrorCode.UNSUPPORTED_DATASET_TYPE,
        "dataset type is not supported; see the supported-types allowlist",
    )
    return report


def _build_context(
    session: Session, schema, mode: ImportMode, assume_naive_utc: bool
) -> ValidationContext:
    return ValidationContext(
        mode=mode,
        assume_naive_utc=assume_naive_utc,
        existing_ids=load_existing_ids(session, schema),
        reference_ids=load_reference_ids(session, schema),
    )


def validate(
    session: Session,
    dataset_type: str,
    content: bytes,
    fmt: str,
    *,
    mode: ImportMode = ImportMode.APPEND,
    assume_naive_utc: bool = False,
) -> ValidationReport:
    """Validate a dataset without persisting anything (no database writes)."""
    schema = get_schema(dataset_type)
    if schema is None:
        logger.info("Ingestion validation rejected: unsupported dataset type.")
        return _unsupported_report(dataset_type)

    logger.info("Ingestion validation started (type=%s, format=%s).", dataset_type, fmt)
    ctx = _build_context(session, schema, mode, assume_naive_utc)
    result = validate_dataset(schema, content, fmt, ctx)
    report = result.report
    if report.is_valid:
        logger.info(
            "Ingestion validation completed (type=%s, rows=%d, accepted=%d).",
            dataset_type,
            report.row_count,
            report.accepted_count,
        )
    else:
        logger.info(
            "Ingestion validation rejected (type=%s, errors=%d).",
            dataset_type,
            len(report.issues),
        )
    return report


def import_dataset(
    session: Session,
    dataset_type: str,
    content: bytes,
    fmt: str,
    *,
    mode: ImportMode = ImportMode.APPEND,
    assume_naive_utc: bool = False,
) -> ImportOutcome:
    """Validate, then transactionally import a dataset (all-or-nothing)."""
    import_id = _new_import_id()
    received_at = datetime.now(timezone.utc)
    schema = get_schema(dataset_type)

    if schema is None:
        report = _unsupported_report(dataset_type)
        return _finish(session, import_id, ImportStatus.REJECTED, report, mode, received_at, 0)

    logger.info("Import started (import_id=%s, type=%s, mode=%s).", import_id, dataset_type, mode.value)
    ctx = _build_context(session, schema, mode, assume_naive_utc)
    result: ValidationResult = validate_dataset(schema, content, fmt, ctx)
    report = result.report

    # The import endpoint always re-validates; it never trusts a prior call.
    if not report.is_valid:
        logger.info(
            "Import rejected before persistence (import_id=%s, errors=%d).",
            import_id,
            len(report.issues),
        )
        return _finish(session, import_id, ImportStatus.REJECTED, report, mode, received_at, 0)

    try:
        inserted = persist_rows(session, schema, result.normalized_rows, mode)
        session.commit()
    except Exception:  # noqa: BLE001 - convert any failure into a clean rollback
        session.rollback()
        logger.exception("Import rolled back (import_id=%s, type=%s).", import_id, dataset_type)
        report.add(
            ErrorCode.INVALID_FIELD_TYPE,
            ErrorSeverity.STRUCTURAL,
            "import failed during persistence and was rolled back; database unchanged",
        )
        return _finish(session, import_id, ImportStatus.ROLLED_BACK, report, mode, received_at, 0)

    logger.info(
        "Import completed (import_id=%s, type=%s, inserted=%d, mode=%s).",
        import_id,
        dataset_type,
        inserted,
        mode.value,
    )
    return _finish(session, import_id, ImportStatus.COMPLETED, report, mode, received_at, inserted)


def _finish(
    session: Session,
    import_id: str,
    status: ImportStatus,
    report: ValidationReport,
    mode: ImportMode,
    received_at: datetime,
    inserted: int,
) -> ImportOutcome:
    """Record a compact, safe audit row and build the outcome object."""
    completed_at = datetime.now(timezone.utc)
    summary = _summarize(status, report, mode, inserted)

    record = ImportRecord(
        import_id=import_id,
        dataset_type=report.dataset_type,
        mode=mode,
        status=status,
        received_at=received_at,
        completed_at=completed_at,
        row_count=report.row_count,
        accepted_count=report.accepted_count if status is ImportStatus.COMPLETED else 0,
        rejected_count=0 if status is ImportStatus.COMPLETED else report.row_count,
        error_count=len(report.issues),
        summary=summary,
    )
    # The audit row is written in its own transaction so it survives even when
    # the data import rolled back (giving an auditable record of the failure).
    session.add(record)
    session.commit()

    return ImportOutcome(
        import_id=import_id,
        status=status,
        report=report,
        mode=mode,
        received_at=received_at,
        completed_at=completed_at,
        inserted=inserted,
        summary=summary,
    )


def _summarize(status: ImportStatus, report: ValidationReport, mode: ImportMode, inserted: int) -> str:
    if status is ImportStatus.COMPLETED:
        return f"Imported {inserted} row(s) into {report.dataset_type} ({mode.value.lower()} mode)."
    if status is ImportStatus.ROLLED_BACK:
        return f"Import of {report.dataset_type} failed during persistence; rolled back."
    return f"Rejected {report.dataset_type}: {len(report.issues)} validation error(s)."


__all__ = ["validate", "import_dataset", "ImportOutcome", "SUPPORTED_DATASET_TYPES"]
