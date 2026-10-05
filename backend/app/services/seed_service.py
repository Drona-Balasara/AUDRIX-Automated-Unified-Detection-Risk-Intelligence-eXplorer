"""Automatic and manual seeding of the local synthetic dataset."""
from __future__ import annotations

from pathlib import Path
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, REPO_ROOT
from app.core.logging import get_logger
from app.ingestion.service import import_dataset
from app.models import SocEntity
from app.models.enums import ImportMode

logger = get_logger(__name__)

IMPORT_ORDER = [
    "entities",
    "assets",
    "alerts",
    "investigations",
    "investigation_actions",
    "escalations",
    "remediations",
    "telemetry",
    "performance_metrics",
]


def find_synthetic_dir() -> Path | None:
    """Find the synthetic dataset directory across local and cloud layouts."""
    candidates = [
        REPO_ROOT / "data" / "synthetic",
        BACKEND_DIR / "data" / "synthetic",
        Path("data/synthetic").resolve(),
        Path("../data/synthetic").resolve(),
    ]
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "entities.csv").exists():
            return candidate
    return None


def seed_synthetic_data(session: Session, force: bool = False) -> dict[str, object]:
    """Import all local synthetic datasets and trigger an initial assessment."""
    count = session.scalar(select(func.count()).select_from(SocEntity)) or 0
    if count > 0 and not force:
        logger.info("Database already contains %d entities; skipping auto-seed.", count)
        return {"status": "skipped", "message": f"Database already has {count} entities"}

    synthetic_dir = find_synthetic_dir()
    if not synthetic_dir:
        logger.warning("Could not find synthetic data directory; skipping seed.")
        return {"status": "failed", "message": "Synthetic data directory not found"}

    logger.info("Seeding synthetic data from %s (force=%s)...", synthetic_dir, force)
    imported_counts: dict[str, int] = {}
    mode = ImportMode.REPLACE if force else ImportMode.APPEND

    for dataset_type in IMPORT_ORDER:
        csv_file = synthetic_dir / f"{dataset_type}.csv"
        if not csv_file.exists():
            continue
        content = csv_file.read_bytes()
        outcome = import_dataset(
            session=session,
            dataset_type=dataset_type,
            content=content,
            fmt="csv",
            mode=mode,
            assume_naive_utc=True,
        )
        imported_counts[dataset_type] = outcome.inserted
        logger.info("Imported %s: %d rows", dataset_type, outcome.inserted)

    # Run the analytical assessment to generate findings and populate the review queue
    from app.api.v1.routes.assessment import run_assessment

    assessment_res = run_assessment(session)
    logger.info(
        "Initial assessment complete: %d findings, %d queued items across %d entities.",
        assessment_res.total_findings,
        assessment_res.queue_inserted,
        assessment_res.entity_count,
    )

    return {
        "status": "completed",
        "imported": imported_counts,
        "total_findings": assessment_res.total_findings,
        "queue_items": assessment_res.queue_inserted,
        "entity_count": assessment_res.entity_count,
    }
