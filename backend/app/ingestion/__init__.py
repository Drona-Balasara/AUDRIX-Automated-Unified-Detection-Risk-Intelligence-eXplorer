"""SAT-SA ingestion pipeline (Phase 3).

Accepts externally supplied SOC datasets (CSV/JSON) matching the Phase 2 domain
model, validates them structurally and semantically, normalizes them into the
canonical representation, and imports valid datasets transactionally. See
``docs/ingestion.md`` for the full contract.
"""

from __future__ import annotations

from app.ingestion.schemas import SUPPORTED_DATASET_TYPES, get_schema
from app.ingestion.service import ImportOutcome, import_dataset, validate

__all__ = [
    "SUPPORTED_DATASET_TYPES",
    "get_schema",
    "validate",
    "import_dataset",
    "ImportOutcome",
]
