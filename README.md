MCC-OCR-Summary
================

Microservice that ingests a PDF (up to 80MB), performs OCR via Google Document AI, summarises the extracted text with a pluggable LLM backend (OpenAI by default), and returns a generated summary PDF.

Architecture Highlights
-----------------------
* FastAPI application (`src/main.py`)
* Services: OCR (`OCRService`), Summariser (`Summariser`), PDF generation (`PDFWriter`)
* Strict input validation (extension, MIME type, magic header, size limit) & custom exceptions mapped to HTTP status codes
* Structured JSON logging with request correlation (`request_id` header)
* Exponential backoff retries (tenacity) for transient OCR & summarisation failures
* Dependency injection via lightweight service singletons (future-ready for a container)

Environment Variables
---------------------
The service loads configuration from environment variables (see `src/config.py`). Startup validation (`cfg.validate_required()`) enforces required values unless `STUB_MODE=true`.

New / notable variables:
* `MAX_PDF_BYTES` (default 83886080 = 80MB) – upload hard limit.
* `ENABLE_METRICS` (default true) – expose Prometheus metrics.
* `OPENAI_API_KEY_SECRET_RESOURCE` – optional Secret Manager fallback if `OPENAI_API_KEY` not set.

Core / Project:
* `PROJECT_ID` (or `GCP_PROJECT_ID` fallback)
* `REGION` (optional for deployment scripts)

Document AI:
* `DOC_AI_LOCATION` (e.g. `us`)
* `DOC_AI_OCR_PROCESSOR_ID` (primary OCR processor)
* Optional: `DOC_AI_FORM_PARSER_ID`, `DOC_AI_INVOICE_PROCESSOR`, `DOC_AI_SPLITTER_PROCESSOR`, `DOC_AI_CLASSIFIER_PROCESSOR`

Summarisation:
* `OPENAI_API_KEY` (required unless `STUB_MODE=true`) – may be auto-fetched from Secret Manager via `OPENAI_API_KEY_SECRET_RESOURCE`.

Drive / Sheets (optional features):
* `DRIVE_ROOT_FOLDER_ID`, `DRIVE_INTAKE_FOLDER_ID`
* `SHEET_ID`, `SHEET_TAB_GID`
* `ARTIFACT_BUCKET`

Security / Misc:
* `CLAIM_LOG_SALT`
* `ALLOWED_ORIGINS` (comma-separated for CORS) default allows all
* Flags: `STUB_MODE`, `FULL_PROCESSING`, `DRIVE_ENABLED`, `WRITE_TO_DRIVE`, `SHEETS_ENABLED`, `PDF_ENABLED`

Secrets should be provided via your deployment environment (Cloud Run secrets, Secret Manager, or CI environment), never hard-coded.

Local Development
-----------------
Create & activate a Python 3.11 environment, install dependencies, then run:

```bash
uvicorn src.main:app --reload --port 8000
```

Health Check:

```bash
curl http://localhost:8000/healthz
```

Processing Endpoint (multipart upload):

```bash
curl -X POST -F "file=@sample.pdf" http://localhost:8000/process -o summary.pdf
```

Testing & Coverage
------------------
Run unit tests with coverage (target >= 80%):

```bash
pytest --cov=src --cov-report=term-missing
```

Logging
-------
Logs are emitted as structured JSON to stdout. Provide an `x-request-id` header to propagate correlation; otherwise one is generated per request.

Metrics
-------
If `ENABLE_METRICS=true` (default), a Prometheus scrape endpoint is exposed at `/metrics` providing counters / histograms for:
* OCR (`ocr_service_calls_total`, `ocr_service_latency_seconds`)
* Summariser (`summariser_calls_total`, `summariser_latency_seconds`)
* PDF Writer (`pdf_writer_calls_total`)
* HTTP layer (`http_requests_total{method,path,status}`, `http_request_latency_seconds`)

Example:
```bash
curl http://localhost:8000/metrics | grep ocr_service_calls_total
```

CI
--
GitHub Actions workflow `.github/workflows/ci.yml` runs on pushes and PRs:
1. Installs dependencies & pylint
2. Lints all Python files
3. Runs tests with coverage (threshold enforced via `pytest.ini`)

Failing lint or coverage will fail the workflow.

Docker
------
The Docker image is multi-stage, installs only runtime dependencies, and runs as a non-root `app` user. A health check probes `/healthz`.

Build the container:

```bash
docker build -t mcc-ocr-summary:latest .
```

Run:

```bash
docker run -p 8000:8000 \
	-e PROJECT_ID=your-project \
	-e DOC_AI_LOCATION=us \
	-e DOC_AI_OCR_PROCESSOR_ID=processor123 \
	-e OPENAI_API_KEY=sk-... \
	mcc-ocr-summary:latest
```

Security & Validation
---------------------
* File extension must be `.pdf` and content type `application/pdf` (or fallback `application/octet-stream`).
* Pre-OCR size check rejects oversize uploads early.
* Magic header (`%PDF-`) validation ensures minimal structural integrity before sending to Document AI.
* Sanitization: control characters stripped and overly long text bounded before summarisation to mitigate token waste.
* Container runs as non-root; slim base and no build toolchain in final stage.

Future Enhancements
-------------------
* Richer PDF layout (tables, headers) via ReportLab backend.
* OpenTelemetry traces end-to-end.
* Additional summarisation providers (Anthropic, Vertex AI) via backend registry.
* Circuit breaker & adaptive retry jitter.

Deployment (Cloud Run)
----------------------
Use the provided script:

```bash
export PROJECT_ID=your-project
export DOC_AI_LOCATION=us
export DOC_AI_OCR_PROCESSOR_ID=processor123
export OPENAI_API_KEY=your-key
./scripts/deploy.sh
```

The script builds an image and deploys it to Cloud Run, setting key environment variables. For sensitive values prefer using `--set-secrets` (manually add or adapt script as needed).

Local Secrets Management
------------------------
For local dev you can create a `.env` file (not committed) with required variables. `pydantic-settings` loads it automatically.

Example `.env` (do NOT commit real secrets):
```
PROJECT_ID=dev-project
DOC_AI_LOCATION=us
DOC_AI_OCR_PROCESSOR_ID=xxxx
OPENAI_API_KEY=xxxx
MAX_PDF_BYTES=83886080
ENABLE_METRICS=true
STUB_MODE=true
```


