"""FastAPI application entrypoint (slimmed MVP version).

batch-v11 connectivity hardening:
 - Force stdout logging early with basicConfig (before configure_logging)
 - Sanitize OPENAI_API_KEY (strip whitespace/newlines) before use
 - Explicit startup markers
"""
from __future__ import annotations

import io
import logging
import sys
import socket
import time
import os
from contextlib import suppress
from fastapi import FastAPI, File, UploadFile, Depends, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse

from src.config import get_config
from src.services.docai_helper import OCRService
from src.services.summariser import Summariser, StructuredSummariser, OpenAIBackend
from src.services.pdf_writer import PDFWriter, MinimalPDFBackend
from src.errors import ValidationError, OCRServiceError, SummarizationError, PDFGenerationError
from src.logging_setup import configure_logging

# drive_client will be added shortly
try:  # pragma: no cover - optional during refactor
    from src.services.drive_client import download_pdf, upload_pdf  # type: ignore
except Exception:  # pragma: no cover
    download_pdf = upload_pdf = None  # type: ignore

# Force basic stdout logging early (will be complemented by structured config in configure_logging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.info("\u2705 Logging initialized (stdout)")

_API_LOG = logging.getLogger("api")


def create_app() -> FastAPI:
    configure_logging()
    try:  # refresh cache for tests altering env
        get_config.cache_clear()  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover
        pass
    cfg = get_config()
    app = FastAPI(title="MCC-OCR-Summary", version="0.2.0")
    app.state.config = cfg
    app.state.ocr_service = OCRService(cfg.doc_ai_processor_id or 'missing-processor')
    # Sanitize API key (strip whitespace/newlines) to avoid header formatting errors
    sanitized_key = (cfg.openai_api_key or '').strip().replace('\n', '')
    if cfg.openai_api_key and sanitized_key != cfg.openai_api_key:
        _API_LOG.info("openai_api_key_sanitized", extra={"original_len": len(cfg.openai_api_key), "sanitized_len": len(sanitized_key)})
    # Determine model with fallback precedence: env OPENAI_MODEL > default list
    fallback_models = [m for m in [cfg.openai_model, "gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"] if m]
    selected_model = fallback_models[0]
    _API_LOG.info("openai_model_selected", extra={"model": selected_model, "candidates": fallback_models})
    structured_enabled = bool(cfg.use_structured_summariser)
    variant = 'structured-v1' if structured_enabled else 'legacy'
    if structured_enabled:
        _API_LOG.info("structured_summariser_active", extra={"event": "structured_active", "variant": variant, "emoji": "✅"})
    else:
        _API_LOG.warning("legacy_summariser_active", extra={"event": "legacy_active", "variant": variant, "emoji": "⚠️"})
    _API_LOG.info("summariser_variant_selected", extra={"variant": variant, "structured": structured_enabled, "USE_STRUCTURED_SUMMARISER": structured_enabled})
    summariser_cls = StructuredSummariser if structured_enabled else Summariser
    app.state.summariser = summariser_cls(OpenAIBackend(api_key=sanitized_key, model=selected_model))
    app.state.pdf_writer = PDFWriter(MinimalPDFBackend())
    # Convenience alias so other code can access report folder quickly
    app.state.drive_report_folder_id = cfg.drive_report_folder_id

    # Dependency accessors
    def get_ocr() -> OCRService: return app.state.ocr_service  # pragma: no cover
    def get_sm() -> Summariser: return app.state.summariser  # pragma: no cover
    def get_pdf() -> PDFWriter: return app.state.pdf_writer  # pragma: no cover

    # Exception handlers -> JSON
    @app.exception_handler(ValidationError)
    async def _val_handler(_r: Request, exc: ValidationError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    @app.exception_handler(OCRServiceError)
    async def _ocr_handler(_r: Request, exc: OCRServiceError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})
    @app.exception_handler(SummarizationError)
    async def _sum_handler(_r: Request, exc: SummarizationError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})
    @app.exception_handler(PDFGenerationError)
    async def _pdf_handler(_r: Request, exc: PDFGenerationError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    # Health route (primary)
    # Unified health payload
    def _health_payload():  # small helper for consistent body
        return {"status": "ok"}

    @app.get('/healthz', summary="Healthz")
    async def healthz():
        return _health_payload()

    # Redundant aliases - sometimes platform/frontends or external checks use different conventions.
    @app.get('/health', include_in_schema=False)
    async def health_alias():
        return _health_payload()

    @app.get('/readyz', include_in_schema=False)
    async def readyz():
        return _health_payload()

    @app.get('/', include_in_schema=False)
    async def root_health():
        return _health_payload()

    # Lightweight middleware to debug path resolution issues (can be removed later)
    @app.middleware('http')
    async def _path_debug_mw(request: Request, call_next):  # pragma: no cover - diagnostic only
        if request.url.path in {'/healthz', '/health', '/readyz', '/'}:
            _API_LOG.debug("health_probe", extra={"path": request.url.path})
        return await call_next(request)

    @app.post('/process')
    async def process_upload(
        file: UploadFile = File(...),
        ocr: OCRService = Depends(get_ocr),
        sm: Summariser = Depends(get_sm),
        pdf: PDFWriter = Depends(get_pdf),
    ):
        """(Legacy) Direct upload endpoint maintained for simple local testing."""
        filename = file.filename or 'upload.pdf'
        if not filename.lower().endswith('.pdf'):
            raise ValidationError('File must have .pdf extension')
        data = await file.read()
        if not data:
            raise ValidationError('Empty file')
        if len(data) > cfg.max_pdf_bytes:
            raise ValidationError('File too large')
        ocr_doc = ocr.process(data)
        summary_struct = sm.summarise(ocr_doc.get('text', ''))
        pdf_bytes = pdf.build(summary_struct)
        return StreamingResponse(io.BytesIO(pdf_bytes), media_type='application/pdf')

    @app.get('/process_drive')
    async def process_drive(
        file_id: str = Query(..., description='Google Drive file ID of source PDF'),
        ocr: OCRService = Depends(get_ocr),
        sm: Summariser = Depends(get_sm),
        pdf: PDFWriter = Depends(get_pdf),
    ):
        # commit: robust error handling for drive pipeline
        if not download_pdf or not upload_pdf:
            raise ValidationError('Drive client not available yet')
        try:
            source_bytes = download_pdf(file_id)
            ocr_doc = ocr.process(source_bytes)
            summary_struct = sm.summarise(ocr_doc.get('text',''))
            pdf_bytes = pdf.build(summary_struct)
            report_name = f"summary_{file_id}.pdf"
            uploaded_id = upload_pdf(pdf_bytes, report_name)
            return {"report_file_id": uploaded_id}
        except ValidationError:
            raise
        except (OCRServiceError, SummarizationError, PDFGenerationError) as exc:
            _API_LOG.exception("/process_drive stage error: %s", exc)
            # Re-raise to leverage existing handlers
            raise
        except Exception as exc:  # pragma: no cover - generic fallback
            _API_LOG.exception("/process_drive unexpected error: %s", exc)
            return JSONResponse(status_code=500, content={"detail": "Drive processing failure", "error": str(exc)})

    # Metrics endpoint (minimal) using prometheus_client if available
    try:  # pragma: no cover - optional dependency
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST  # type: ignore

        @app.get('/metrics')
        async def metrics():  # pragma: no cover - simple passthrough
            data = generate_latest()  # type: ignore
            return PlainTextResponse(data.decode('utf-8'), media_type=CONTENT_TYPE_LATEST)
    except Exception:  # pragma: no cover
        _API_LOG.warning("prometheus_client not installed; /metrics disabled")

    @app.on_event('startup')
    async def _startup_diag():  # pragma: no cover
        cfg.validate_required()
        # Log route table for diagnostics
        routes = [getattr(r, 'path', str(r)) for r in app.router.routes]
        _API_LOG.info("boot_canary: service=mcc-ocr-summary routes=%s", routes)
        _API_LOG.info("service_startup_marker", extra={"phase": "post-config", "version": app.version})
        # Log OpenAI SDK version for observability
        try:
            import openai  # type: ignore
            _API_LOG.info("openai_sdk_version", extra={"version": getattr(openai, '__version__', 'unknown')})
        except Exception:
            _API_LOG.info("openai_sdk_version_unavailable")
        # DNS pre-resolution for api.openai.com (diagnostic early warning)
        try:
            resolved_ip = socket.gethostbyname("api.openai.com")
            _API_LOG.info("openai_dns_resolution", extra={"host": "api.openai.com", "ip": resolved_ip})
        except Exception as e:  # pragma: no cover - environment specific
            _API_LOG.error("openai_dns_resolution_failed", extra={"error": str(e)})
        # Fallback health route injection if missing (parity with MCC_Phase1 style)
        if not any(getattr(r, 'path', '') in {'/healthz', '/healthz/'} for r in app.router.routes):
            @_API_LOG.info("Injecting fallback /healthz route")
            @app.get('/healthz')  # type: ignore
            async def _health_fallback():  # pragma: no cover
                return {"status": "ok"}

    # Temporary connectivity diagnostic endpoint (to be removed after batch-v10 validation)
    @app.get('/ping_openai')
    async def ping_openai():  # pragma: no cover - external call
        import requests  # local import to avoid mandatory dependency if removed later
        payload: dict[str, object] = {"ts": time.time()}
        host = "api.openai.com"
        start = time.perf_counter()
        try:
            ip = socket.gethostbyname(host)
            payload["dns_ip"] = ip
        except Exception as e:  # DNS failure
            payload["dns_error"] = str(e)
            _API_LOG.error("ping_openai_dns_failure", extra=payload)
            return payload
        # Perform lightweight model list request
        api_key_raw = os.getenv("OPENAI_API_KEY", "")
        api_key = api_key_raw.strip().replace('\n', '')
        if api_key_raw and api_key != api_key_raw:
            payload["api_key_sanitized"] = True
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            resp = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=15)
            elapsed = round(time.perf_counter() - start, 3)
            payload.update({
                "status": resp.status_code,
                "elapsed_s": elapsed,
                "text_head": resp.text[:120],
            })
            level = _API_LOG.info if 200 <= resp.status_code < 300 else _API_LOG.warning
            level("ping_openai_result", extra=payload)
            return payload
        except Exception as e:
            payload["error"] = str(e)
            _API_LOG.error("ping_openai_exception", extra=payload)
            return payload

    return app


try:  # pragma: no cover
    # Module-level application instance required for Cloud Run / uvicorn entrypoint (src.main:app)
    app = create_app()
except Exception:  # pragma: no cover
    from fastapi import FastAPI as _F
    app = _F(title='MCC-OCR-Summary (init failure)')

# NOTE: /healthz is already defined inside create_app(); avoid redefining here to prevent duplicate route entries.

__all__ = ['create_app', 'app']
