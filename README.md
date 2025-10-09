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

Required in non-stub mode:
* `PROJECT_ID` (or `GCP_PROJECT_ID`)
* `DOC_AI_LOCATION` (e.g. `us`)
* `DOC_AI_OCR_PROCESSOR_ID` (primary OCR processor; legacy alias `DOC_AI_OCR_PARSER_ID` accepted)
* `OPENAI_API_KEY` (unless retrieved from Secret Manager fallback)
* `ALLOWED_ORIGINS` (explicit, comma-separated list; wildcard `*` rejected)

Important / Optional:
* `STUB_MODE` (enable relaxed validation; allow wildcard or missing `ALLOWED_ORIGINS` + missing `OPENAI_API_KEY`)
* `MAX_PDF_BYTES` (default 83886080 = 80MB) – upload streaming hard limit.
* `ENABLE_METRICS` (default true) – expose `/metrics`.
* `DOC_AI_FORM_PARSER_ID`, `DOC_AI_INVOICE_PROCESSOR`, `DOC_AI_SPLITTER_PROCESSOR`, `DOC_AI_CLASSIFIER_PROCESSOR`
* `DRIVE_ROOT_FOLDER_ID`, `DRIVE_INTAKE_FOLDER_ID`, `DRIVE_ENABLED`, `WRITE_TO_DRIVE`
* `SHEET_ID`, `SHEET_TAB_GID`, `SHEETS_ENABLED`
* `PDF_ENABLED`, `PDF_TEMPLATE`
* `FULL_PROCESSING` (reserved future flag)
* `CLAIM_LOG_SALT`, `ARTIFACT_BUCKET`
* `REGION` (deployment convenience)

Alias Behavior:
* Legacy env var `DOC_AI_OCR_PARSER_ID` maps to `DOC_AI_OCR_PROCESSOR_ID` (see `tests/test_config_alias.py`).

Secret Fallback:
* If `OPENAI_API_KEY` missing in non-stub mode the service will attempt to fetch Secret Manager secret `OPENAI_API_KEY` under the active project. Failures are ignored silently.

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

Metrics & Instrumentation
-------------------------
If `ENABLE_METRICS=true` (default), a Prometheus scrape endpoint is exposed at `/metrics` providing counters / histograms for HTTP and internal operations. Additional request instrumentation (via `prometheus-fastapi-instrumentator`) is automatically added when available and metrics are enabled.

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
* Streaming size enforcement: upload is read in ~1MB chunks; aborts once `MAX_PDF_BYTES` exceeded (prevents large memory allocation of huge files).
* Magic header (`%PDF-`) validation ensures minimal structural integrity before sending to Document AI.
* Sanitization: control characters stripped and overly long text bounded before summarisation to mitigate token waste.
* Strict CORS in production: wildcard origins rejected unless `STUB_MODE=true`.
* Container runs as non-root; slim base and no build toolchain in final stage.

Future Enhancements
-------------------
* Richer PDF layout (tables, headers) via ReportLab backend.
* OpenTelemetry traces end-to-end (foundation in place; metrics already integrated).
* Additional summarisation providers (Anthropic, Vertex AI) via backend registry.
* Circuit breaker & adaptive retry jitter.
* Streaming direct-to-GCS to eliminate RAM use for very large PDFs.
* PDF size histogram & summarisation chunk count metrics.

Deployment (Cloud Run)
----------------------
Hardened manual deployment example:

```bash
export PROJECT_ID=your-project
export REGION=us-central1
export PROCESSOR_ID=processor123
gcloud builds submit --tag gcr.io/$PROJECT_ID/mcc-ocr-summary:final
gcloud run deploy mcc-ocr-summary \
	--image gcr.io/$PROJECT_ID/mcc-ocr-summary:final \
	--region $REGION \
	--platform managed \
	--allow-unauthenticated \
	--set-env-vars PROJECT_ID=$PROJECT_ID,DOC_AI_LOCATION=us,DOC_AI_OCR_PROCESSOR_ID=$PROCESSOR_ID,MAX_PDF_BYTES=83886080,ENABLE_METRICS=true \
	--set-env-vars ALLOWED_ORIGINS=https://your.frontend.app \
	--set-secrets OPENAI_API_KEY=OPENAI_API_KEY:latest
```

Only after verifying the service starts (health endpoint 200) should you apply IAM policy bindings for invokers:

```bash
gcloud run services add-iam-policy-binding mcc-ocr-summary \
	--region $REGION \
	--member="user:you@example.com" \
	--role="roles/run.invoker"
```

The existing `scripts/deploy.sh` can be updated to reflect these flags.

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
# Example explicit CORS for local multi-origin dev
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:5173
```

Pre-commit Hooks
----------------
Install developer tooling locally:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

Configured hooks (see `.pre-commit-config.yaml`):
* `ruff` (lint & format check & autofix)
* `black` (code formatting)
* `isort` (import ordering)
* `mypy` (static typing)
* `forbid-print` custom guard

Resiliency & Connectivity Diagnostics (batch-v10)
------------------------------------------------
Recent production issues surfaced transient `APIConnectionError` failures when calling the OpenAI Responses API with no accompanying summariser lifecycle logs. To diagnose and remediate, batch-v10 introduced several temporary and permanent hardening changes:

Permanent Changes:
* DNS Pre-resolution: At startup (and per summariser call) the service resolves `api.openai.com` and logs the resolved IP (`openai_dns_resolution`). Failures log `openai_dns_resolution_failed`.
* Extended Timeout: OpenAI client timeout increased to 180s (was 120s) to better accommodate large multi-chunk prompts and upstream latency spikes.
* Retry Policy: Manual retry loop in `OpenAIBackend._invoke_with_retry` increased from 5 → 6 attempts with exponential backoff + jitter (capped at 30s). Connection and timeout errors categorized (`category`: connection | rate_limit | unexpected) for observability.
* Structured Lifecycle Events: `summariser_call_start`, `summariser_retry_attempt`, `summariser_call_complete`, and `summariser_call_failed` provide attempt, latency, error type, and token/char metadata.
* Multi-Chunk Summaries: Large OCR texts are chunked (threshold ~20k chars) with partial aggregation to reduce token waste while retaining context.

Temporary Diagnostic Endpoint:
* `/ping_openai`: Performs DNS resolution and a lightweight `GET /v1/models` request, returning status, elapsed seconds, first 120 chars of response body, and any error diagnostic. This endpoint is intended ONLY for short-lived production validation of outbound connectivity and will be removed after confirmation (create an issue referencing its removal once stable).

Operational Verification Steps:
1. Deploy image (e.g. `batch-v10`).
2. Invoke `/ping_openai` (authenticated if service requires auth) and expect HTTP 200 plus a non-error body snippet. If DNS fails, investigate Cloud DNS / VPC egress.
3. Process a known large PDF via `/process_drive` and verify logs contain the summariser lifecycle events and final `aggregated_batch_complete`.

Log Field Reference (key extras):
* `attempt` – 1-based retry counter.
* `latency_ms` – end-to-end OpenAI API call duration.
* `wait_seconds` – backoff delay on retry events.
* `error_type` – exception class name.
* `category` – high-level grouping (connection, rate_limit, unexpected).
* `approx_tokens` – estimated prompt size (chars/4 heuristic) for capacity planning.

Removal Plan:
* After two successful large-document end-to-end runs (no connection retries exhausted), delete `/ping_openai` and associated references. Track via an issue titled "Remove temporary /ping_openai connectivity endpoint".

Security Notes:
* No secret values are logged. Only metadata and truncated response heads are exposed.
* Ensure `/ping_openai` is not left unauthenticated in long-term deployments; prefer restricting or removing.

This section will be pruned once the diagnostics endpoint is removed and stability is confirmed.

Structured Summariser (structured-v1)
------------------------------------
The legacy single-paragraph summariser has been upgraded to a structured multi-section generator that produces a medically oriented narrative plus indexed lists. It is controlled by the feature flag `USE_STRUCTURED_SUMMARISER` (defaults to `true`).

Activation:
* Set `USE_STRUCTURED_SUMMARISER=true` (already the default in `.env.template` and CI). Setting `false` will fall back to legacy behaviour (single aggregated paragraph); this path is retained only temporarily for rollback and will be removed in a future major version.

Pipeline Overview:
1. OCR text is sanitised (control chars removed, capped) then chunked into ~2.5K character segments (hard max 3K) on word boundaries.
2. Each chunk is sent to the OpenAI backend with a deterministic (`temperature=0`) JSON‑only instruction requesting these snake_case keys:
	* `provider_seen`
	* `reason_for_visit`
	* `clinical_findings`
	* `treatment_plan`
	* `diagnoses` (array or comma string, includes ICD-10 codes if explicitly present)
	* `providers`
	* `medications`
3. Per‑chunk JSON is merged:
	* Narrative fields concatenated with de‑duplication preserving first occurrence order.
	* List fields normalised: strings → split on commas/semicolons when multi-valued, flattened, de‑duplicated.
4. A composite human‑readable "Medical Summary" is assembled containing four narrative sections and three indexed lists with simple text formatting (no markdown):
	* Provider Seen
	* Reason for Visit
	* Clinical Findings
	* Treatment / Follow-Up Plan
	* Diagnoses (bulleted)
	* Providers (bulleted)
	* Medications / Prescriptions (bulleted)
5. Legacy PDF contract: The final dict returned to the PDF writer preserves the original display headings:
	* `Patient Information` (currently `N/A` placeholder – future enhancement may parse demographics)
	* `Medical Summary` (full multi-section narrative + indices)
	* `Billing Highlights` (`N/A` unless future extraction added)
	* `Legal / Notes` (`N/A` placeholder for compliance notes)

Observability Events (JSON logs):
* `summariser_chunking` – number of chunks determined.
* `summariser_chunk_start` / `summariser_chunk_complete` – per chunk lifecycle.
* `summariser_call_start` / `summariser_call_complete` – raw OpenAI call timing & token heuristics.
* `summariser_retry_attempt` – retry metadata (category: connection | rate_limit | unexpected).
* `summariser_generation_complete` – counts of merged list items.
* `summariser_variant_selected` (on startup) – confirms active variant (`structured-v1` or `legacy`).

Error Handling:
* Transient OpenAI connectivity/timeouts & 5xx responses retried up to 6 attempts with exponential backoff + jitter (capped 30s) at the backend layer, and an outer 4-attempt retry in the `Summariser` class for chunk-level transient failures.
* Authentication (401/403) failures are surfaced immediately without retry.

Determinism:
* `temperature=0` and explicit instruction to return **only JSON** yields stable outputs. Minor wording differences can still occur if upstream OCR differs between runs.

Extensibility / Future Enhancements:
* Demographics extraction to populate `Patient Information`.
* Billing code / CPT summarisation to repopulate `Billing Highlights`.
* Structured medication normalisation (dose / frequency parsing) prior to PDF rendering.
* Additional backend providers (Anthropic / Vertex) via a pluggable backend registry.

Rollback Strategy:
* Toggle `USE_STRUCTURED_SUMMARISER=false` and redeploy (legacy path preserved while code still contains mapping of legacy snake_case keys). Plan removal after two stable production cycles.

Removal TODOs:
* Remove legacy summariser path & flag (create issue: "Retire legacy summariser path and USE_STRUCTURED_SUMMARISER flag").
* Remove `/ping_openai` endpoint after connectivity stability validated (see above diagnostics section).

Testing:
* New tests cover multi-chunk merging, list de-duplication, structured → legacy heading mapping, PDF section ordering, and error fallbacks for missing keys.
* Legacy tests referencing old behaviour have been skipped (retained as inert files to minimise churn until permanent removal).




