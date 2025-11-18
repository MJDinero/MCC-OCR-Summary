"""Drive helper that doubles as a PdfDeliveryService for the pipeline."""

from __future__ import annotations

import logging
import os
import secrets
from typing import Any

from src.services import drive_client as drive_client_module
from src.services.process_pipeline import PdfDeliveryService
from src.utils.logging_utils import structured_log

_LOG = logging.getLogger("drive_service")


class DriveService(PdfDeliveryService):
    """Adapter around drive_client module that supports stub mode."""

    def __init__(self, *, stub_mode: bool, config: Any) -> None:
        self._stub_mode = stub_mode
        self._config = config

    def deliver_pdf(
        self,
        payload: bytes,
        *,
        trace_id: str | None = None,
        source: str | None = None,
        folder_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        report_name = f"summary-{os.getenv('REPORT_PREFIX', '')}{secrets.token_hex(8)}.pdf"
        target_folder = folder_id or self._config.drive_report_folder_id
        log_context = {"trace_id": trace_id, "source": source}
        if metadata:
            log_context.update(metadata)
        if self._stub_mode:
            structured_log(
                _LOG,
                logging.INFO,
                "drive_upload_stub",
                report_name=report_name,
                folder_id=target_folder,
                bytes=len(payload),
            )
            return f"stub-{report_name}"
        return drive_client_module.upload_pdf(
            payload,
            report_name,
            parent_folder_id=target_folder,
            log_context=log_context | {"component": "process_api"},
        )

    def download_pdf(self, *args: Any, **kwargs: Any) -> bytes:
        return drive_client_module.download_pdf(*args, **kwargs)

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - passthrough
        return getattr(drive_client_module, item)


__all__ = ["DriveService"]
