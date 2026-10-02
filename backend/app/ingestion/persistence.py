"""Transactional persistence of a validated dataset.

Inserts are performed with SQLAlchemy 2.x core bulk statements for efficiency
and run inside a single transaction so an import is strictly all-or-nothing: if
anything fails, the caller rolls back and the database is left exactly as it was.
Foreign-key enforcement (enabled on the engine) guarantees that references are
sound and that REPLACE cannot silently orphan dependent rows.

This module also loads the identifier universes the validation engine needs
(existing primary keys and referenced keys) so relationship checks run against
real database state.
"""

from __future__ import annotations

import importlib

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.ingestion.schemas import get_schema
from app.ingestion.types import DatasetSchema
from app.models.enums import ImportMode


def _model_for(schema: DatasetSchema) -> type:
    """Resolve the ORM model class for a dataset schema (no client input)."""
    models = importlib.import_module("app.models")
    return getattr(models, schema.model_name)


def load_existing_ids(session: Session, schema: DatasetSchema) -> set[str]:
    """Return the set of primary identifiers already present for this type."""
    model = _model_for(schema)
    column = getattr(model, schema.primary_key)
    return set(session.scalars(select(column)).all())


def load_reference_ids(session: Session, schema: DatasetSchema) -> dict[str, set[str]]:
    """Return, per referenced dataset type, the identifiers that exist in the DB."""
    universe: dict[str, set[str]] = {}
    for fk in schema.foreign_keys:
        ref_schema = get_schema(fk.references)
        if ref_schema is None:  # pragma: no cover - registry is closed
            continue
        if fk.references in universe:
            continue
        ref_model = _model_for(ref_schema)
        ref_column = getattr(ref_model, ref_schema.primary_key)
        universe[fk.references] = set(session.scalars(select(ref_column)).all())
    return universe


def persist_rows(
    session: Session,
    schema: DatasetSchema,
    rows: list[dict[str, object]],
    mode: ImportMode,
) -> int:
    """Insert validated rows within the caller's transaction.

    For REPLACE, existing rows of this type are deleted first. Does not commit;
    the caller owns the transaction boundary (commit on success, rollback on any
    exception). Returns the number of rows inserted.
    """
    model = _model_for(schema)

    if mode is ImportMode.REPLACE:
        session.execute(delete(model))

    if rows:
        # Core bulk insert: parameterized, efficient, and never string-built.
        session.execute(insert(model), rows)

    return len(rows)
