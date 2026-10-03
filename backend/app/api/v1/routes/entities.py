"""Entity endpoints (Phase 11).

Expose the SOC entities stored in the database for use as scope/filter
inputs in the findings and queue endpoints.  Entities are read-only through
this API; they are loaded via the ingestion pipeline.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.organization import SocEntity
from app.schemas.analytics import EntityListResponse, EntityResponse

router = APIRouter(prefix="/entities", tags=["entities"])

_MAX_LIMIT = 200


@router.get("", response_model=EntityListResponse, summary="List SOC entities")
def list_entities(
    limit: int = Query(default=50, ge=1, le=_MAX_LIMIT, description="Page size (max 200)."),
    offset: int = Query(default=0, ge=0, description="Page start offset."),
    session: Session = Depends(get_db),
) -> EntityListResponse:
    """Return a paginated list of all SOC entities, ordered by entity_id."""
    total = session.scalar(select(func.count()).select_from(SocEntity)) or 0
    rows = list(
        session.scalars(
            select(SocEntity).order_by(SocEntity.entity_id).offset(offset).limit(limit)
        )
    )
    return EntityListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            EntityResponse(
                entity_id=e.entity_id,
                name=e.name,
                sector=str(e.sector),
                peer_group=e.peer_group,
                scale=str(e.scale),
                asset_count_estimate=e.asset_count_estimate,
                analyst_headcount=e.analyst_headcount,
                data_period_start=e.data_period_start,
                data_period_end=e.data_period_end,
            )
            for e in rows
        ],
    )


@router.get("/{entity_id}", response_model=EntityResponse, summary="Get one SOC entity")
def get_entity(entity_id: str, session: Session = Depends(get_db)) -> EntityResponse:
    """Return the entity with the given identifier."""
    row = session.scalars(
        select(SocEntity).where(SocEntity.entity_id == entity_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return EntityResponse(
        entity_id=row.entity_id,
        name=row.name,
        sector=str(row.sector),
        peer_group=row.peer_group,
        scale=str(row.scale),
        asset_count_estimate=row.asset_count_estimate,
        analyst_headcount=row.analyst_headcount,
        data_period_start=row.data_period_start,
        data_period_end=row.data_period_end,
    )
