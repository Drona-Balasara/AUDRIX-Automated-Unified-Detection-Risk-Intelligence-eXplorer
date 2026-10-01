"""The writer emits typed CSV/JSON files matching the documented schema."""

from __future__ import annotations

import pandas as pd

from app.datagen.generator import Dataset
from app.datagen.schema import GROUND_TRUTH_SCHEMA, TABLE_NAMES, TABLE_SCHEMAS
from app.datagen.writer import build_frame

_EXPECTED_PANDAS_DTYPE = {
    "string": "string",
    "int": "int64",
    "Int64": "Int64",
    "float": "float64",
    "boolean": "boolean",
    "datetime": "datetime64[ns, UTC]",
}


def test_all_files_written(written_dataset_dir) -> None:
    for name in TABLE_NAMES:
        assert (written_dataset_dir / f"{name}.csv").exists()
        assert (written_dataset_dir / f"{name}.json").exists()
    assert (written_dataset_dir / "ground_truth.csv").exists()
    assert (written_dataset_dir / "ground_truth.json").exists()
    assert (written_dataset_dir / "manifest.json").exists()


def test_frames_have_declared_dtypes(generated_dataset: Dataset) -> None:
    for name, columns in TABLE_SCHEMAS.items():
        frame = build_frame(columns, generated_dataset.tables[name])
        assert list(frame.columns) == [c for c, _ in columns]
        for col, token in columns:
            assert str(frame[col].dtype) == _EXPECTED_PANDAS_DTYPE[token], (
                f"{name}.{col} expected {token}"
            )


def test_ground_truth_frame_typed(generated_dataset: Dataset) -> None:
    frame = build_frame(GROUND_TRUTH_SCHEMA, generated_dataset.ground_truth)
    assert list(frame.columns) == [c for c, _ in GROUND_TRUTH_SCHEMA]
    assert len(frame) == len(generated_dataset.ground_truth)


def test_csv_roundtrip_row_counts(written_dataset_dir, generated_dataset: Dataset) -> None:
    for name in TABLE_NAMES:
        df = pd.read_csv(written_dataset_dir / f"{name}.csv")
        assert len(df) == len(generated_dataset.tables[name])


def test_ground_truth_is_separate_from_tables(written_dataset_dir) -> None:
    # Ground truth (evaluation data) must not be one of the operational tables.
    assert "ground_truth" not in TABLE_NAMES
    assert (written_dataset_dir / "ground_truth.json").exists()
