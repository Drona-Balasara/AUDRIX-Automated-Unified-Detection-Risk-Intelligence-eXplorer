"""Command-line entry point for synthetic dataset generation.

Example::

    python -m app.datagen --seed 20240601
    python -m app.datagen --seed 7 --output /tmp/satsa-data
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import REPO_ROOT
from app.datagen.config import DEFAULT_SEED, GenerationConfig
from app.datagen.generator import DatasetGenerator
from app.datagen.writer import write_dataset

DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "synthetic"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.datagen",
        description="Generate the reproducible synthetic SOC dataset.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"Random seed controlling the entire run (default: {DEFAULT_SEED}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for dataset files (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--periods",
        type=int,
        default=None,
        help="Override the number of monthly reporting periods.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config_kwargs: dict = {"seed": args.seed}
    if args.periods is not None:
        config_kwargs["num_periods"] = args.periods
    config = GenerationConfig(**config_kwargs)

    dataset = DatasetGenerator(config).generate()
    written = write_dataset(dataset, args.output)

    manifest = json.loads((args.output / "manifest.json").read_text(encoding="utf-8"))
    print(f"Generated synthetic dataset (seed={args.seed}) -> {args.output}")
    for name in sorted(manifest):
        print(f"  {name:28s} {manifest[name]:>7d} rows")
    print(f"Wrote {len(written)} files.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
