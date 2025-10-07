"""FastAPI application entrypoint for MCC-OCR-Summary service."""
from __future__ import annotations

import io
import uuid
import logging
from typing import Annotated

from fastapi import FastAPI, File, UploadFile, Depends, Request
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware

from src.config import get_config
from src.services.docai_helper import OCRService
from src.services.summariser import Summariser, OpenAIBackend
from src.services.pdf_writer import PDFWriter, MinimalPDFBackend
from src.errors import (
    ValidationError,
    OCRServiceError,
    SummarizationError,
    PDFGenerationError,
)
from src.logging_setup import configure_logging, set_request_id

try:  # pragma: no cover - optional metrics dependency
    from prometheus_client import Counter, Histogram  # type: ignore
    _HTTP_REQUESTS = Counter(
        "http_requests_total", "Total HTTP requests", ["method", "path", "status"]
    )
    _HTTP_LATENCY = Histogram(
        "http_request_latency_seconds", "HTTP request latency seconds", ["method", "path"]
    )
except Exception:  # pragma: no cover
    _HTTP_REQUESTS = None  # type: ignore
    _HTTP_LATENCY = None  # type: ignore

_API_LOG = logging.getLogger("api")


def create_app() -> FastAPI:
    configure_logging()
    cfg = get_config()

    app = FastAPI(title="MCC-OCR-Summary", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
    allow_origins=cfg.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Expose config on state for tests / dynamic overrides
    app.state.config = cfg

    # Service singletons (attached to state for easier test overrides)
    app.state.ocr_service = OCRService(cfg.doc_ai_ocr_parser_id or cfg.doc_ai_form_parser_id)
    app.state.summariser = Summariser(OpenAIBackend(api_key=cfg.openai_api_key))
    app.state.pdf_writer = PDFWriter(MinimalPDFBackend())

    # Lifespan cleanup
    @app.on_event("shutdown")
    async def _shutdown():  # pragma: no cover - trivial
        app.state.ocr_service.close()

    # Dependencies
    def get_ocr() -> OCRService:  # pragma: no cover - trivial accessor
        return app.state.ocr_service

    def get_summariser() -> Summariser:  # pragma: no cover
        return app.state.summariser

    def get_pdf_writer() -> PDFWriter:  # pragma: no cover
        return app.state.pdf_writer

    # Exception handlers
    @app.exception_handler(ValidationError)
    async def _validation_handler(_req: Request, exc: ValidationError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(OCRServiceError)
    async def _ocr_handler(_req: Request, exc: OCRServiceError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(SummarizationError)
    async def _sum_handler(_req: Request, exc: SummarizationError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(PDFGenerationError)
    async def _pdf_handler(_req: Request, exc: PDFGenerationError):
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.middleware("http")
    async def _request_id_mw(request: Request, call_next):  # type: ignore
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        set_request_id(rid)
        method = request.method
        path = request.scope.get("path", "")
        import time
        start = time.perf_counter()
        try:
            response = await call_next(request)
            return response
        finally:
            duration = time.perf_counter() - start
            status = getattr(locals().get('response', None), 'status_code', 500)
            if _HTTP_REQUESTS:
                try:
                    _HTTP_REQUESTS.labels(method=method, path=path, status=str(status)).inc()
                    if _HTTP_LATENCY:
                        _HTTP_LATENCY.labels(method=method, path=path).observe(duration)
                except Exception:  # pragma: no cover
                    pass
            if 'response' in locals():
                response.headers["x-request-id"] = rid


    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    try:  # pragma: no cover - simple wiring
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST  # type: ignore

        @app.get("/metrics")  # type: ignore
        async def metrics():  # noqa: D401
            if not app.state.config.enable_metrics:
                return JSONResponse(status_code=404, content={"detail": "Metrics disabled"})
            data = generate_latest()
            return PlainTextResponse(data.decode("utf-8"), media_type=CONTENT_TYPE_LATEST)
    except Exception:  # pragma: no cover
        pass

    @app.post("/process")
    async def process(
        file: UploadFile = File(...),
        ocr: OCRService = Depends(get_ocr),
        sm: Summariser = Depends(get_summariser),
        pdf: PDFWriter = Depends(get_pdf_writer),
    ):
        # Basic metadata validation before reading whole file
        filename = file.filename or "upload.pdf"
        if not filename.lower().endswith('.pdf'):
            raise ValidationError("File must have .pdf extension")
        if file.content_type not in ("application/pdf", "application/octet-stream"):
            raise ValidationError("Invalid content type; expected application/pdf")
        data = await file.read()
        size = len(data)
        if size == 0:
            raise ValidationError("Empty file upload")
        if size > cfg.max_pdf_bytes:
            raise ValidationError(f"File exceeds maximum allowed size of {cfg.max_pdf_bytes} bytes")
        _API_LOG.info(
            "process_start", extra={"filename": filename, "size": size, "request_id": getattr(cfg, 'request_id', None)}
        )
        # OCR expects path or bytes; we supply bytes
        ocr_doc = ocr.process(data)
        text = ocr_doc.get("text", "")
        summary = sm.summarise(text)
        pdf_bytes = pdf.build(summary)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=summary.pdf"},
        )

    # Defer validation to startup so test fixtures can set env before evaluation
    @app.on_event("startup")
    async def _validate_cfg():  # pragma: no cover - simple guard
        try:
            cfg.validate_required()
        except RuntimeError as exc:
            # Log and re-raise to crash startup in real deployment
            logging.getLogger("startup").error("config_validation_failed", exc_info=True)
            raise
    return app


__all__ = ["create_app"]
