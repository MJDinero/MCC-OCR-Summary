"""FastAPI application entrypoint for MCC OCR → Summary pipeline."""

from __future__ import annotations

import logging
import os
import secrets
import socket
import sys
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api import build_api_router
from src.errors import ValidationError
from src.logging_setup import configure_logging
from src.services import drive_client as drive_client_module
from src.services.docai_helper import OCRService
from src.services.metrics import PrometheusMetrics, NullMetrics
from src.services.pdf_writer import MinimalPDFBackend, PDFWriter
from src.services.pipeline import create_state_store_from_env, create_workflow_launcher_from_env

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from src.services.pipeline import PipelineStateStore, WorkflowLauncher
from src.services.summariser import OpenAIBackend, StructuredSummariser, Summariser
from src.startup import hydrate_google_credentials_file
from src.utils.mode_manager import is_mvp
from src.utils.secrets import resolve_secret_env
from src.config import get_config

# Force stdout logging early (before configure_logging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.info("✅ Logging initialised (stdout)")

hydrate_google_credentials_file()

_API_LOG = logging.getLogger("api")

_MVP_MODE = is_mvp()
MODE = "MVP" if _MVP_MODE else "AUDIT"
print(f"🚀 MCC-OCR-Summary starting in {MODE} mode")
ENABLE_METRICS = not _MVP_MODE


def _health_payload() -> dict[str, str]:
    return {"status": "ok"}


class _DriveClientAdapter:
    def __init__(self, *, stub: bool, config) -> None:
        self._stub = stub
        self._config = config

    def upload_pdf(self, file_bytes: bytes, folder_id: str | None = None) -> str:
        report_name = f"summary-{os.getenv('REPORT_PREFIX', '')}{secrets.token_hex(8)}.pdf"
        if self._stub:
            _API_LOG.info(
                "drive_upload_stub",
                extra={"report_name": report_name, "folder_id": folder_id, "bytes": len(file_bytes)},
            )
            return f"stub-{report_name}"
        parent_folder = folder_id or self._config.drive_report_folder_id
        return drive_client_module.upload_pdf(
            file_bytes,
            report_name,
            parent_folder_id=parent_folder,
            log_context={"component": "process_api"},
        )

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - passthrough to legacy helpers
        return getattr(drive_client_module, item)


def _build_summariser(stub_mode: bool, *, cfg) -> Any:
    ocr_service: Any
    if stub_mode:

        class _StubSummariser:
            chunk_target_chars = 1200
            chunk_hard_max = 1800

            def summarise(self, text: str) -> dict[str, str]:
                payload = (text or "").strip()
                trimmed = payload if len(payload) <= 2000 else payload[:2000] + "..."
                return {
                    "Patient Information": "N/A",
                    "Medical Summary": trimmed or "N/A",
                    "Billing Highlights": "N/A",
                    "Legal / Notes": "N/A",
                }

            async def summarise_async(self, text: str) -> dict[str, str]:
                return self.summarise(text)

        return _StubSummariser()

    backend = OpenAIBackend(
        model=cfg.openai_model or "gpt-4o-mini",
        api_key=cfg.openai_api_key,
    )
    summariser_cls = StructuredSummariser if cfg.use_structured_summariser else Summariser
    return summariser_cls(backend=backend)


def _configure_state_store() -> tuple["PipelineStateStore", "WorkflowLauncher"]:
    state_store = create_state_store_from_env()
    workflow_launcher = create_workflow_launcher_from_env()
    return state_store, workflow_launcher


def create_app() -> FastAPI:
    configure_logging()
    try:
        get_config.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass

    cfg = get_config()
    app = FastAPI(title="MCC-OCR-Summary", version="1.0.0")
    app.state.config = cfg

    current_mvp = is_mvp()
    stub_mode = os.getenv("STUB_MODE", "false").strip().lower() == "true"
    supervisor_simple = current_mvp or os.getenv("SUPERVISOR_MODE", "").strip().lower() == "simple"
    app.state.mvp_mode = current_mvp
    app.state.stub_mode = stub_mode
    app.state.supervisor_simple = supervisor_simple

    state_store, workflow_launcher = _configure_state_store()
    app.state.state_store = state_store
    app.state.workflow_launcher = workflow_launcher
    if ENABLE_METRICS:
        app.state.metrics = PrometheusMetrics.instrument_app(app)
    else:
        app.state.metrics = NullMetrics()

    if stub_mode:

        class _StubOCRService:
            def process(self, _file_bytes: bytes) -> dict[str, Any]:
                return {"text": "", "pages": []}

            def close(self) -> None:
                return None

        ocr_service = _StubOCRService()
    else:
        ocr_service = OCRService(processor_id=cfg.doc_ai_processor_id, config=cfg)  # type: ignore[assignment]
    app.state.ocr_service = ocr_service

    app.state.summariser = _build_summariser(stub_mode, cfg=cfg)
    app.state.pdf_writer = PDFWriter(MinimalPDFBackend())
    app.state.drive_client = _DriveClientAdapter(stub=stub_mode, config=cfg)

    internal_token = resolve_secret_env("INTERNAL_EVENT_TOKEN", project_id=cfg.project_id)
    if not internal_token:
        raise RuntimeError("INTERNAL_EVENT_TOKEN must be configured via Secret Manager or environment variable")
    app.state.internal_event_token = internal_token

    @app.exception_handler(ValidationError)
    async def _val_handler(_r: Request, exc: ValidationError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # Health endpoints ---------------------------------------------------------
    @app.get("/healthz", summary="Healthz")
    async def healthz():
        return _health_payload()

    @app.get("/health", include_in_schema=False)
    async def health_alias():
        return _health_payload()

    @app.get("/readyz", include_in_schema=False)
    async def readyz():
        return _health_payload()

    @app.get("/", include_in_schema=False)
    async def root_health():
        return _health_payload()

    # Include API routes
    app.include_router(build_api_router())

    @app.on_event("startup")
    async def _startup_diag():  # pragma: no cover
        cfg.validate_required()
        routes = [getattr(r, "path", str(r)) for r in app.router.routes]
        _API_LOG.info("boot_canary", extra={"service": "mcc-ocr-summary", "routes": routes})
        _API_LOG.info("service_startup_marker", extra={"phase": "post-config", "version": app.version})
        try:
            import openai  # type: ignore

            _API_LOG.info("openai_sdk_version", extra={"version": getattr(openai, "__version__", "unknown")})
        except Exception:
            _API_LOG.info("openai_sdk_version_unavailable")
        try:
            resolved_ip = socket.gethostbyname("api.openai.com")
            _API_LOG.info("openai_dns_resolution", extra={"host": "api.openai.com", "ip": resolved_ip})
        except Exception as err:
            _API_LOG.error("openai_dns_resolution_failed", extra={"error": str(err)})

    return app


__all__ = ["create_app"]
