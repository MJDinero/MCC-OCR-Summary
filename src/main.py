"""FastAPI application entrypoint for MCC OCR → Summary pipeline."""

from __future__ import annotations

import logging
import os
import socket
import sys
from typing import TYPE_CHECKING, Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.api.ingest import router as ingest_router
from src.api.process import router as process_router
from src.errors import ValidationError
from src.logging_setup import configure_logging
from src.services.docai_helper import OCRService
from src.services.drive_service import DriveService
from src.services.metrics import PrometheusMetrics, NullMetrics
from src.services.pdf_writer import PDFWriter, ReportLabBackend
from src.services.process_pipeline import ProcessPipelineService
from src.services.pipeline import (
    create_state_store_from_env,
    create_workflow_launcher_from_env,
)
from src.services.summariser_refactored import (
    OpenAIResponsesBackend,
    RefactoredSummariser,
)
from src.startup import hydrate_google_credentials_file
from src.utils.mode_manager import is_mvp
from src.utils.secrets import resolve_secret_env
from src.config import get_config
from src.utils.logging_utils import structured_log

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from src.services.pipeline import PipelineStateStore, WorkflowLauncher

DEBUG_ENABLED = any(arg == "--debug" for arg in sys.argv) or os.getenv(
    "DEBUG", "false"
).strip().lower() in {"1", "true", "yes", "on"}
LOG_LEVEL = logging.DEBUG if DEBUG_ENABLED else logging.INFO
configure_logging(level=LOG_LEVEL, force=True)
structured_log(
    logging.getLogger("startup"),
    logging.INFO,
    "service_bootstrap",
    debug_enabled=DEBUG_ENABLED,
)

hydrate_google_credentials_file()

_API_LOG = logging.getLogger("api")

_MVP_MODE = is_mvp()
MODE = "MVP" if _MVP_MODE else "AUDIT"
print(f"🚀 MCC-OCR-Summary starting in {MODE} mode")


def _metrics_enabled(default: bool) -> bool:
    raw = os.getenv("ENABLE_METRICS")
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENABLE_METRICS = _metrics_enabled(True)


def _health_payload() -> dict[str, str]:
    return {"status": "ok"}


def _build_summariser(stub_mode: bool, *, cfg) -> Any:
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

    configured_mode = str(getattr(cfg, "summary_compose_mode", "")).strip().lower()
    env_override = os.getenv("SUMMARY_COMPOSE_MODE", "").strip().lower()
    if configured_mode and configured_mode != "refactored":
        structured_log(
            _API_LOG,
            logging.WARNING,
            "summary_compose_mode_overridden",
            configured_mode=configured_mode,
        )
    if env_override and env_override != "refactored":
        structured_log(
            _API_LOG,
            logging.WARNING,
            "summary_compose_env_override_ignored",
            env_value=env_override,
        )
    refactored_backend = OpenAIResponsesBackend(
        model=cfg.openai_model or "gpt-4o-mini",
        api_key=cfg.openai_api_key,
    )
    return RefactoredSummariser(backend=refactored_backend)


def _build_pdf_writer(
    *, cfg, override_mode: str | None = None
) -> tuple[str, PDFWriter, str]:
    writer_mode = "rich"
    configured_mode = str(getattr(cfg, "pdf_writer_mode", "") or "").strip().lower()
    env_override = (override_mode or os.getenv("PDF_WRITER_MODE", "")).strip().lower()
    ignored_value = env_override or configured_mode
    if ignored_value and ignored_value != "rich":
        structured_log(
            _API_LOG,
            logging.WARNING,
            "pdf_writer_mode_forced",
            requested_mode=ignored_value,
        )
    backend_label = "reportlab"
    return writer_mode, PDFWriter(ReportLabBackend()), backend_label


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
    app = FastAPI(title="MCC-OCR-Summary API", version="1.0.0")
    app.state.config = cfg

    current_mvp = is_mvp()
    stub_mode = os.getenv("STUB_MODE", "false").strip().lower() == "true"
    supervisor_simple = (
        current_mvp or os.getenv("SUPERVISOR_MODE", "").strip().lower() == "simple"
    )
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
            def process(self, _file_bytes: bytes, **_kwargs: Any) -> dict[str, Any]:
                return {"text": "", "pages": []}

            def close(self) -> None:
                return None

        app.state.ocr_service = _StubOCRService()
    else:
        app.state.ocr_service = OCRService(  # type: ignore[assignment]
            processor_id=cfg.doc_ai_processor_id,
            doc_ai_splitter_id=cfg.doc_ai_splitter_id,
            doc_ai_location=cfg.doc_ai_location,
            force_split_min_pages=cfg.doc_ai_force_split_min_pages,
            config=cfg,
        )

    app.state.summary_compose_mode = "refactored"

    noise_override = os.getenv("ENABLE_NOISE_FILTERS")
    if noise_override is None:
        enable_noise_filters = bool(getattr(cfg, "enable_noise_filters", True))
    else:
        enable_noise_filters = noise_override.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    app.state.enable_noise_filters = enable_noise_filters

    summariser = _build_summariser(stub_mode, cfg=cfg)
    app.state.summariser = summariser
    writer_override = os.getenv("PDF_WRITER_MODE", "").strip().lower() or None
    writer_mode, pdf_writer, writer_backend = _build_pdf_writer(
        cfg=cfg, override_mode=writer_override
    )
    app.state.pdf_writer = pdf_writer
    app.state.pdf_writer_mode = writer_mode
    app.state.writer_backend = writer_backend
    summariser_backend = getattr(summariser, "backend", None)
    structured_log(
        _API_LOG,
        logging.INFO,
        "summary_components_configured",
        summary_mode="refactored",
        summariser_class=summariser.__class__.__name__,
        summariser_backend=(
            summariser_backend.__class__.__name__ if summariser_backend else "None"
        ),
        pdf_writer_mode=writer_mode,
        pdf_writer_backend=writer_backend,
    )
    drive_service = DriveService(stub_mode=stub_mode, config=cfg)
    app.state.drive_client = drive_service  # Back-compat for download helpers
    app.state.pdf_delivery_service = drive_service

    pipeline_service = ProcessPipelineService(
        ocr_service=app.state.ocr_service,
        summariser=summariser,
        pdf_writer=pdf_writer,
        pdf_delivery=drive_service,
        drive_report_folder_id=cfg.drive_report_folder_id,
        stub_mode=stub_mode,
        supervisor_simple=supervisor_simple,
        summary_compose_mode=app.state.summary_compose_mode,
        pdf_writer_mode=writer_mode,
        writer_backend=writer_backend,
        metrics=getattr(app.state, "metrics", None),
    )
    app.state.process_pipeline = pipeline_service

    internal_token = resolve_secret_env(
        "INTERNAL_EVENT_TOKEN", project_id=cfg.project_id
    )
    if not internal_token:
        raise RuntimeError(
            "INTERNAL_EVENT_TOKEN must be configured via Secret Manager or environment variable"
        )
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
    app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
    app.include_router(process_router, prefix="/process", tags=["process"])

    @app.middleware("http")
    async def _component_probe(
        request: Request, call_next: Callable[[Request], Any]
    ):  # pragma: no cover - instrumentation only
        summariser_obj = getattr(request.app.state, "summariser", None)
        summariser_backend = (
            getattr(summariser_obj, "backend", None) if summariser_obj else None
        )
        structured_log(
            _API_LOG,
            logging.INFO,
            "request_component_selection",
            path=str(request.url.path),
            summary_compose_mode=getattr(
                request.app.state, "summary_compose_mode", "unknown"
            ),
            summariser_class=(
                summariser_obj.__class__.__name__ if summariser_obj else "None"
            ),
            summariser_backend=(
                summariser_backend.__class__.__name__
                if summariser_backend
                else "None"
            ),
            pdf_writer_mode=getattr(request.app.state, "pdf_writer_mode", "unknown"),
            pdf_writer_backend=getattr(request.app.state, "writer_backend", "unknown"),
            stub_mode=getattr(request.app.state, "stub_mode", False),
        )
        return await call_next(request)

    @app.on_event("startup")
    async def _startup_diag():  # pragma: no cover
        cfg.validate_required()
        routes = [getattr(r, "path", str(r)) for r in app.router.routes]
        _API_LOG.info(
            "boot_canary", extra={"service": "mcc-ocr-summary", "routes": routes}
        )
        _API_LOG.info(
            "service_startup_marker",
            extra={"phase": "post-config", "version": app.version},
        )
        try:
            import openai  # type: ignore

            _API_LOG.info(
                "openai_sdk_version",
                extra={"version": getattr(openai, "__version__", "unknown")},
            )
        except Exception:
            _API_LOG.info("openai_sdk_version_unavailable")
        try:
            resolved_ip = socket.gethostbyname("api.openai.com")
            _API_LOG.info(
                "openai_dns_resolution",
                extra={"host": "api.openai.com", "ip": resolved_ip},
            )
        except Exception as err:
            _API_LOG.error("openai_dns_resolution_failed", extra={"error": str(err)})

    return app


__all__ = ["create_app"]
