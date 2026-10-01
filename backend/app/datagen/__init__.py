"""Synthetic SOC dataset generation (Phase 2).

Deterministic, seedable generation of a coherent multi-entity SOC dataset plus a
separate ground-truth file of planted scenarios. Everything is synthetic: no real
people, organizations, credentials, addresses, or payloads.

Run as a module::

    python -m app.datagen --seed 20240601
"""

from __future__ import annotations

from app.datagen.config import DEFAULT_SEED, GenerationConfig
from app.datagen.generator import Dataset, DatasetGenerator
from app.datagen.writer import write_dataset

__all__ = [
    "DEFAULT_SEED",
    "GenerationConfig",
    "Dataset",
    "DatasetGenerator",
    "write_dataset",
]
