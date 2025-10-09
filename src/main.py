"""FastAPI application entrypoint (slimmed MVP version)."""
from __future__ import annotations

import io
import logging
from fastapi import FastAPI, File, UploadFile, Depends, Request, Query
from fastapi.responses import StreamingResponse, JSONResponse, PlainTextResponse

from src.config import get_config
from src.services.docai_helper import OCRService
from src.services.summariser import Summariser, OpenAIBackend
from src.services.pdf_writer import PDFWriter, MinimalPDFBackend
from src.errors import ValidationError, OCRServiceError, SummarizationError, PDFGenerationError
from src.logging_setup import configure_logging

# drive_client will be added shortly
try:  # pragma: no cover - optional during refactor
    from src.services.drive_client import download_pdf, upload_pdf  # type: ignore
except Exception:  # pragma: no cover
    download_pdf = upload_pdf = None  # type: ignore

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
    app.state.summariser = Summariser(OpenAIBackend(api_key=cfg.openai_api_key))
    app.state.pdf_writer = PDFWriter(MinimalPDFBackend())

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
    @app.get('/healthz')
    async def healthz():
        return {"status": "ok"}

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
        # Fallback health route injection if missing (parity with MCC_Phase1 style)
        if not any(getattr(r, 'path', '') in {'/healthz', '/healthz/'} for r in app.router.routes):
            @_API_LOG.info("Injecting fallback /healthz route")
            @app.get('/healthz')  # type: ignore
            async def _health_fallback():  # pragma: no cover
                return {"status": "ok"}

    return app


try:  # pragma: no cover
    # Module-level application instance required for Cloud Run / uvicorn entrypoint (src.main:app)
    app = create_app()
except Exception:  # pragma: no cover
    from fastapi import FastAPI as _F
    app = _F(title='MCC-OCR-Summary (init failure)')

# NOTE: /healthz is already defined inside create_app(); avoid redefining here to prevent duplicate route entries.

__all__ = ['create_app', 'app']
