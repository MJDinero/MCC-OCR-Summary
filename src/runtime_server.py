"""Runtime launcher for MCC OCR Summary with adaptive worker tuning."""

from __future__ import annotations

import multiprocessing
import os

import uvicorn


def _worker_count() -> int:
    explicit = os.getenv("UVICORN_WORKERS")
    if explicit:
        try:
            value = int(explicit)
            if value > 0:
                return value
        except ValueError:
            pass
    cpu_total = multiprocessing.cpu_count() or 1
    return max(1, cpu_total)


def main() -> None:
    workers = _worker_count()
    os.environ.setdefault("UVICORN_WORKERS", str(workers))
    port = int(os.getenv("PORT", "8080"))
    app_path = os.getenv("FASTAPI_APP", "src.main:create_app")
    uvicorn.run(
        app_path,
        host="0.0.0.0",
        port=port,
        factory=True,
        workers=workers,
        lifespan="on",
    )


if __name__ == "__main__":  # pragma: no cover - exercised in runtime
    main()
