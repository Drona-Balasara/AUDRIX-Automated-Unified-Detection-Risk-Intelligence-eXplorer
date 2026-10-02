"""Ingestion API routes (Phase 3).

Thin HTTP layer: it handles upload intake and shaping of responses, then
delegates all validation, normalization, and persistence to the ingestion
service. The client must name the dataset type explicitly (an allowlisted
value); the server never infers it from file contents or column names.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.ingestion import import_dataset, validate
from app.ingestion.errors import ValidationReport
from app.ingestion.schemas import DATASET_SCHEMAS, SUPPORTED_DATASET_TYPES
from app.ingestion.service import ImportOutcome
from app.ingestion.upload import UploadError, receive_upload
from app.models.enums import ImportMode, ImportStatus
from app.models.ingestion import ImportRecord
from app.schemas.ingestion import (
    DatasetTypeInfo,
    DatasetTypesResponse,
    ImportRecordResponse,
    ImportResultResponse,
    ValidationIssueModel,
    ValidationReportResponse,
)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
logger = get_logger(__name__)


def _parse_mode(mode: str) -> ImportMode:
    try:
        return ImportMode(mode.strip().upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode '{mode}'; expected one of: append, replace",
        ) from None


def _issues_to_models(report: ValidationReport, cap: int) -> list[ValidationIssueModel]:
    return [ValidationIssueModel(**issue.as_dict()) for issue in report.issues[:cap]]


def _report_response(
    report: ValidationReport, filename: str | None = None
) -> ValidationReportResponse:
    cap = get_settings().max_reported_errors
    issues = _issues_to_models(report, cap)
    return ValidationReportResponse(
        dataset_type=report.dataset_type,
        detected_format=report.detected_format,
        is_valid=report.is_valid,
        row_count=report.row_count,
        accepted_count=report.accepted_count,
        rejected_count=report.rejected_count,
        structural_error_count=len(report.structural_errors),
        semantic_error_count=len(report.semantic_errors),
        total_error_count=len(report.issues),
        returned_error_count=len(issues),
        errors=issues,
        filename=filename,
    )


def _import_response(outcome: ImportOutcome) -> ImportResultResponse:
    cap = get_settings().max_reported_errors
    issues = _issues_to_models(outcome.report, cap)
    return ImportResultResponse(
        import_id=outcome.import_id,
        dataset_type=outcome.report.dataset_type,
        mode=outcome.mode.value,
        status=outcome.status.value,
        committed=outcome.committed,
        row_count=outcome.report.row_count,
        accepted_count=outcome.report.accepted_count if outcome.committed else 0,
        rejected_count=0 if outcome.committed else outcome.report.row_count,
        error_count=len(outcome.report.issues),
        returned_error_count=len(issues),
        received_at=outcome.received_at,
        completed_at=outcome.completed_at,
        summary=outcome.summary,
        errors=issues,
    )


@router.get("/dataset-types", response_model=DatasetTypesResponse, summary="List supported dataset types")
def list_dataset_types() -> DatasetTypesResponse:
    """Return the allowlist of supported dataset types and their columns."""
    infos: list[DatasetTypeInfo] = []
    for dataset_type in SUPPORTED_DATASET_TYPES:
        schema = DATASET_SCHEMAS[dataset_type]
        required = [f.name for f in schema.fields if f.required]
        optional = [f.name for f in schema.fields if not f.required]
        infos.append(
            DatasetTypeInfo(
                dataset_type=dataset_type,
                required_columns=required,
                optional_columns=optional,
            )
        )
    return DatasetTypesResponse(supported_types=infos)


@router.post("/validate", response_model=ValidationReportResponse, summary="Validate a dataset")
async def validate_endpoint(
    file: UploadFile = File(..., description="CSV or JSON dataset file."),
    dataset_type: str = Form(..., description="Explicit dataset type (allowlisted)."),
    mode: str = Form("append", description="Intended import mode: append or replace."),
    assume_naive_utc: bool = Form(
        False, description="Treat naive timestamps as UTC (off by default)."
    ),
    session: Session = Depends(get_db),
) -> ValidationReportResponse:
    """Validate an uploaded dataset and return a structured report. No writes."""
    import_mode = _parse_mode(mode)
    if dataset_type not in SUPPORTED_DATASET_TYPES:
        raise HTTPException(status_code=400, detail="unsupported dataset type")

    try:
        content, fmt, filename = await receive_upload(file, get_settings().max_upload_bytes)
    except UploadError as exc:
        logger.info("Upload rejected during validation (code=%s).", exc.code)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    report = validate(
        session, dataset_type, content, fmt, mode=import_mode, assume_naive_utc=assume_naive_utc
    )
    return _report_response(report, filename=filename)


@router.post("/import", response_model=ImportResultResponse, summary="Import a dataset transactionally")
async def import_endpoint(
    response: Response,
    file: UploadFile = File(..., description="CSV or JSON dataset file."),
    dataset_type: str = Form(..., description="Explicit dataset type (allowlisted)."),
    mode: str = Form("append", description="Import mode: append (default) or replace."),
    assume_naive_utc: bool = Form(False, description="Treat naive timestamps as UTC."),
    session: Session = Depends(get_db),
) -> ImportResultResponse:
    """Re-validate and transactionally import a dataset (all-or-nothing)."""
    import_mode = _parse_mode(mode)
    if dataset_type not in SUPPORTED_DATASET_TYPES:
        raise HTTPException(status_code=400, detail="unsupported dataset type")

    try:
        content, fmt, _filename = await receive_upload(file, get_settings().max_upload_bytes)
    except UploadError as exc:
        logger.info("Upload rejected during import (code=%s).", exc.code)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from None

    outcome = import_dataset(
        session, dataset_type, content, fmt, mode=import_mode, assume_naive_utc=assume_naive_utc
    )

    # Reflect outcome in the HTTP status while still returning the full body.
    if outcome.status is ImportStatus.REJECTED:
        response.status_code = 422
    elif outcome.status is ImportStatus.ROLLED_BACK:
        response.status_code = 500
    return _import_response(outcome)


@router.get(
    "/imports/{import_id}",
    response_model=ImportRecordResponse,
    summary="Retrieve an ingestion audit record",
)
def get_import_record(import_id: str, session: Session = Depends(get_db)) -> ImportRecordResponse:
    """Return a previously recorded ingestion operation by its identifier."""
    record = session.scalars(
        select(ImportRecord).where(ImportRecord.import_id == import_id)
    ).first()
    if record is None:
        raise HTTPException(status_code=404, detail="import record not found")
    return ImportRecordResponse(
        import_id=record.import_id,
        dataset_type=record.dataset_type,
        mode=record.mode.value if isinstance(record.mode, ImportMode) else str(record.mode),
        status=record.status.value if isinstance(record.status, ImportStatus) else str(record.status),
        received_at=record.received_at,
        completed_at=record.completed_at,
        row_count=record.row_count,
        accepted_count=record.accepted_count,
        rejected_count=record.rejected_count,
        error_count=record.error_count,
        summary=record.summary,
    )
