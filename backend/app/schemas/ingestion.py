"""Pydantic v2 response models for the ingestion API.

These describe the safe, structured shapes returned to clients. They never carry
raw row contents, filesystem paths, stack traces, or other internals. Error
detail is capped by the caller; the full counts are always reported so a client
knows how many issues exist even when only a sample is shown.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ValidationIssueModel(BaseModel):
    """A single structured validation issue."""

    code: str = Field(description="Stable machine-readable error code.")
    severity: str = Field(description="'structural' or 'semantic'.")
    message: str = Field(description="Concise, safe description of the problem.")
    row: int | None = Field(default=None, description="1-based record index, if row-specific.")
    field: str | None = Field(default=None, description="Column name, if field-specific.")


class ValidationReportResponse(BaseModel):
    """Structured result of validating a dataset without persisting it."""

    dataset_type: str
    detected_format: str | None
    is_valid: bool
    row_count: int
    accepted_count: int
    rejected_count: int
    structural_error_count: int
    semantic_error_count: int
    total_error_count: int
    returned_error_count: int = Field(
        description="Number of issues included below (may be capped)."
    )
    errors: list[ValidationIssueModel]
    filename: str | None = Field(default=None, description="Sanitized upload filename.")


class ImportResultResponse(BaseModel):
    """Outcome of an import attempt."""

    import_id: str
    dataset_type: str
    mode: str
    status: str
    committed: bool = Field(description="True only when rows were committed.")
    row_count: int
    accepted_count: int
    rejected_count: int
    error_count: int
    returned_error_count: int
    received_at: datetime
    completed_at: datetime
    summary: str
    errors: list[ValidationIssueModel]


class DatasetTypeInfo(BaseModel):
    dataset_type: str
    required_columns: list[str]
    optional_columns: list[str]


class DatasetTypesResponse(BaseModel):
    supported_types: list[DatasetTypeInfo]


class ImportRecordResponse(BaseModel):
    """Audit record for a past ingestion operation."""

    import_id: str
    dataset_type: str
    mode: str
    status: str
    received_at: datetime
    completed_at: datetime | None
    row_count: int
    accepted_count: int
    rejected_count: int
    error_count: int
    summary: str
