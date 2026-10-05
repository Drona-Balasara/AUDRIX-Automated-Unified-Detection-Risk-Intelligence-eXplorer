"""Seed your live Render backend with the local synthetic dataset.

Usage:
    python seed_remote.py [BACKEND_URL]

Example:
    python seed_remote.py https://audrix.onrender.com
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.parse
from pathlib import Path

DEFAULT_URL = "https://audrix.onrender.com"

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


def post_multipart(url: str, fields: dict[str, str], file_field: str, file_path: Path) -> dict:
    boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
    body = bytearray()

    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    filename = file_path.name
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(b"Content-Type: text/csv\r\n\r\n")
    body.extend(file_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))

    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "AUDRIX-Seed-Script/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    base_url = base_url.rstrip("/")
    if base_url.endswith("/api/v1"):
        api_url = base_url
    else:
        api_url = f"{base_url}/api/v1"

    root_dir = Path(__file__).resolve().parent
    synthetic_dir = root_dir / "data" / "synthetic"
    if not synthetic_dir.exists():
        print(f"Error: synthetic data directory not found at {synthetic_dir}")
        sys.exit(1)

    print(f"Connecting to {api_url}...")
    try:
        req = urllib.request.Request(f"{api_url}/health", headers={"User-Agent": "AUDRIX-Seed-Script/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            health = json.loads(resp.read().decode("utf-8"))
            print(f"Connected! Backend status: {health.get('status')} | Service: {health.get('service')}")
    except Exception as exc:
        print(f"Warning: Could not check health ({exc}). Attempting import anyway...")

    print("\n--- Seeding 9 Local Datasets to Remote Backend ---")
    for dataset_type in IMPORT_ORDER:
        csv_file = synthetic_dir / f"{dataset_type}.csv"
        if not csv_file.exists():
            print(f"  [SKIP] {csv_file.name} not found")
            continue

        print(f"  Uploading {dataset_type} ({csv_file.stat().st_size // 1024} KB)...", end="", flush=True)
        try:
            res = post_multipart(
                f"{api_url}/ingestion/import",
                fields={"dataset_type": dataset_type, "mode": "replace", "assume_naive_utc": "true"},
                file_field="file",
                file_path=csv_file,
            )
            inserted = res.get("accepted_count", res.get("row_count", 0))
            print(f" Done! ({inserted} rows inserted)")
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            print(f" Failed ({err.code}): {err_body}")
        except Exception as exc:
            print(f" Failed: {exc}")

    print("\n--- Running Assessment to Compute Findings & Review Queue ---")
    try:
        req = urllib.request.Request(
            f"{api_url}/assessment/run",
            data=b"",
            headers={"User-Agent": "AUDRIX-Seed-Script/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            assessment = json.loads(resp.read().decode("utf-8"))
            print(f"Assessment complete! Generated {assessment.get('total_findings')} findings across {assessment.get('entity_count')} entities.")
            print(f"Supervisory review queue populated with {assessment.get('queue_inserted')} items.")
    except Exception as exc:
        print(f"Assessment run failed: {exc}")

    print("\nAll done! Open your Vercel frontend to view all your data!")


if __name__ == "__main__":
    main()
