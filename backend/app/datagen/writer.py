"""Typed writer for the synthetic dataset.

Builds one pandas DataFrame per table using the explicit dtypes declared in
:mod:`app.datagen.schema` (never type inference) and emits both CSV and JSON.
Ground truth is written separately as evaluation data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.datagen.generator import Dataset
from app.datagen.schema import GROUND_TRUTH_SCHEMA, TABLE_SCHEMAS


def _coerce(df: pd.DataFrame, columns: list[tuple[str, str]]) -> pd.DataFrame:
    """Apply the declared dtype to each column, in declared order."""
    ordered = pd.DataFrame()
    for name, token in columns:
        series = df[name] if name in df.columns else pd.Series([None] * len(df))
        if token == "datetime":
            ordered[name] = pd.to_datetime(series, utc=True)
        elif token == "string":
            ordered[name] = series.astype("string")
        elif token == "int":
            ordered[name] = series.astype("int64")
        elif token == "Int64":
            ordered[name] = series.astype("Int64")
        elif token == "float":
            ordered[name] = series.astype("float64")
        elif token == "boolean":
            ordered[name] = series.astype("boolean")
        else:  # pragma: no cover - guarded by schema
            raise ValueError(f"Unknown dtype token: {token}")
    return ordered


def build_frame(columns: list[tuple[str, str]], rows: list[dict]) -> pd.DataFrame:
    """Return a typed DataFrame for ``rows`` following ``columns`` exactly."""
    col_names = [name for name, _ in columns]
    raw = pd.DataFrame(rows, columns=col_names) if rows else pd.DataFrame(
        {name: pd.Series(dtype="object") for name in col_names}
    )
    return _coerce(raw, columns)


def write_dataset(dataset: Dataset, out_dir: Path) -> dict[str, Path]:
    """Write every table (CSV + JSON) plus ground truth. Returns written paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    for name, columns in TABLE_SCHEMAS.items():
        frame = build_frame(columns, dataset.tables[name])
        csv_path = out_dir / f"{name}.csv"
        json_path = out_dir / f"{name}.json"
        frame.to_csv(csv_path, index=False)
        frame.to_json(json_path, orient="records", date_format="iso", indent=2)
        written[f"{name}.csv"] = csv_path
        written[f"{name}.json"] = json_path

    gt_frame = build_frame(GROUND_TRUTH_SCHEMA, dataset.ground_truth)
    gt_csv = out_dir / "ground_truth.csv"
    gt_json = out_dir / "ground_truth.json"
    gt_frame.to_csv(gt_csv, index=False)
    gt_frame.to_json(gt_json, orient="records", date_format="iso", indent=2)
    written["ground_truth.csv"] = gt_csv
    written["ground_truth.json"] = gt_json

    # A small manifest documents row counts for quick inspection.
    manifest = {name: len(dataset.tables[name]) for name in TABLE_SCHEMAS}
    manifest["ground_truth"] = len(dataset.ground_truth)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written["manifest.json"] = manifest_path

    return written
