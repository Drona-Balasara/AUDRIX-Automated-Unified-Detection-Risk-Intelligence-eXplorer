"""Reproducibility and determinism of the generator."""

from __future__ import annotations

from app.datagen.config import DEFAULT_SEED, GenerationConfig
from app.datagen.generator import DatasetGenerator


def _signature(ds):
    return (
        {name: len(rows) for name, rows in ds.tables.items()},
        len(ds.ground_truth),
    )


def test_same_seed_is_identical() -> None:
    a = DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()
    b = DatasetGenerator(GenerationConfig(seed=DEFAULT_SEED)).generate()
    assert a.tables == b.tables
    assert a.ground_truth == b.ground_truth


def test_different_seed_differs() -> None:
    a = DatasetGenerator(GenerationConfig(seed=1)).generate()
    b = DatasetGenerator(GenerationConfig(seed=2)).generate()
    # Row-level content must differ (counts may coincide, alert rows must not).
    assert a.tables["alerts"] != b.tables["alerts"]


def test_structure_stable_across_seeds() -> None:
    a = DatasetGenerator(GenerationConfig(seed=1)).generate()
    b = DatasetGenerator(GenerationConfig(seed=2)).generate()
    # Same tables and same entity roster regardless of seed.
    assert set(a.tables) == set(b.tables)
    assert len(a.tables["entities"]) == len(b.tables["entities"]) == 6


def test_default_seed_is_documented() -> None:
    assert GenerationConfig().seed == DEFAULT_SEED
