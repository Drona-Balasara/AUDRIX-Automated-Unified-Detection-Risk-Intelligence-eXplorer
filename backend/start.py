"""AUDRIX production runner for Render and cloud environments."""
from __future__ import annotations

import os
import sys
import uvicorn

if __name__ == "__main__":
    port_str = os.environ.get("PORT", "10000")
    try:
        port = int(port_str)
    except ValueError:
        port = 10000

    host = os.environ.get("HOST", "0.0.0.0")
    print(f"[AUDRIX] Starting production server on {host}:{port}...", file=sys.stdout, flush=True)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
