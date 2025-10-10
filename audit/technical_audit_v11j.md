diff --git a//dev/null b/audit/technical_audit.md
index 0000000000000000000000000000000000000000..407cafdad5f71b826536ace545e95827c7d76cc5 100644
--- a//dev/null
+++ b/audit/technical_audit.md
@@ -0,0 +1,317 @@
+# MCC OCR Summariser — Technical Audit (FastAPI, Cloud Run)
+
+## 1. Executive Summary
+- **Risk rating:** 🔴 **Critical** – The service cannot pass local tests or survive Cloud Run deployment without major remediation.
+- **Immediate blockers:**
+  1. `create_app()` eagerly instantiates a real Document AI client; without Application Default Credentials every local/CI test fails before stubbing services.【F:src/main.py†L45-L72】【F:src/services/docai_helper.py†L92-L123】【feffb6†L1-L25】
+  2. Cloud Build/Run configuration omits the required Drive/OpenAI secrets and still uses the legacy `DOC_AI_OCR_PROCESSOR_ID` name, guaranteeing startup failure in production.【F:cloudbuild.yaml†L21-L36】【F:src/config.py†L32-L87】
+  3. The GitHub Actions workflow installs only `requirements.txt`, so pytest never receives `pytest-cov` and aborts due to the enforced `--cov` addopts.【F:.github/workflows/ci.yml†L21-L41】【cbd5a4†L1-L7】
+- **Top 10 findings:**
+  1. 🚫 **Document AI dependency hard-blocks tests:** `create_app()` hits ADC before fixtures can inject stubs, breaking every offline test path.【F:src/main.py†L45-L72】【feffb6†L1-L25】
+  2. 🚫 **Cloud Run env misconfiguration:** Deployment still references `DOC_AI_OCR_PROCESSOR_ID` and omits Drive/OpenAI envs, so runtime validation raises immediately.【F:cloudbuild.yaml†L21-L36】【F:src/config.py†L57-L86】
+  3. 🚫 **CI workflow missing coverage plugin:** GitHub Actions installs runtime deps only; pytest exits with argument errors due to missing `pytest-cov` plugin required by `pytest.ini` addopts.【F:.github/workflows/ci.yml†L21-L41】【cbd5a4†L1-L7】
+  4. ⚠️ **Structured logging drops contextual fields:** `JsonFormatter` ignores `record.__dict__` extras, so keys such as `{phase, summary_stage}` never reach logs, defeating observability goals.【F:src/logging_setup.py†L19-L42】【F:src/main.py†L189-L207】
+  5. ⚠️ **Hard-coded GCS buckets:** Batch OCR helpers and the PDF splitter always use `quantify-agent-*`, blocking reuse across projects and risking data exfiltration to the wrong bucket.【F:src/services/docai_batch_helper.py†L20-L107】【F:src/utils/pdf_splitter.py†L30-L118】
+  6. ⚠️ **`ping_openai` leaks response text:** The diagnostics endpoint logs and returns raw OpenAI model listing text and API key sanitation hints, risking PII/API leakage in Cloud Run logs.【F:src/main.py†L210-L244】
+  7. ⚠️ **Summariser lacks structured schema validation:** Chunk responses are coerced ad-hoc without validating required keys or types, leaving the legacy `'dict'.strip` failure only partially mitigated.【F:src/services/summariser.py†L331-L524】
+  8. ⚠️ **`OpenAIBackend` blocks the event loop:** Tenacity + `time.sleep` retries run inside FastAPI endpoints, so each request monopolises the worker thread for up to ~6 minutes under repeated failures.【F:src/services/summariser.py†L114-L188】
+  9. ⚠️ **Docker command ignores `$PORT`:** The image always binds to 8080, preventing flexible Cloud Run port negotiation and complicating local overrides.【F:Dockerfile†L5-L49】
+  10. ⚠️ **Test fixtures rely on removed `STUB_MODE`:** `tests/conftest.py` still sets `STUB_MODE`, but `AppConfig` no longer honours it, so automated validation cannot be bypassed in lower environments.【F:tests/conftest.py†L1-L12】【F:src/config.py†L31-L87】
+
+## 2. Repository Overview
+- **Runtime & entry point:** FastAPI application defined in `src/main.py`, exporting `create_app()` and a module-level `app` instance for Cloud Run / Uvicorn.【F:src/main.py†L45-L258】
+- **Core services:**
+  - Configuration via `AppConfig`/`get_config` (`pydantic-settings`).【F:src/config.py†L31-L91】
+  - OCR orchestration (`src/services/docai_helper.py`) with optional batch splitting and GCS uploads.【F:src/services/docai_helper.py†L24-L326】
+  - Summarisation pipeline and OpenAI backend (`src/services/summariser.py`).【F:src/services/summariser.py†L40-L541】
+  - PDF rendering (`src/services/pdf_writer.py`).【F:src/services/pdf_writer.py†L1-L207】
+  - Google Drive IO helpers (`src/services/drive_client.py`).【F:src/services/drive_client.py†L1-L78】
+- **Utilities:** DocAI request builder (`src/utils/docai_request_builder.py`) and PDF splitter (`src/utils/pdf_splitter.py`).
+- **Deployment artifacts:** Dockerfile and Cloud Build recipe (`cloudbuild.yaml`).【F:Dockerfile†L1-L49】【F:cloudbuild.yaml†L1-L36】
+- **Tests:** Extensive suite under `tests/`, but many modules are skipped or assume stub modes that no longer exist.【F:tests/test_summariser.py†L1-L67】【F:tests/test_config_alias.py†L1-L16】
+
+## 3. Test & Coverage Results
+- Installation attempts:
+  - `python -m pip install -r requirements-dev.txt` retried repeatedly under the sandbox proxy; runtime deps already present but pinned dev wheels (pytest 8.1.1) could not be fetched.【e9a47a†L1-L35】【770e0c†L1-L5】
+- Pytest invocations:
+  - `pytest -q --maxfail=1` fails because `pytest-cov` is absent; `pytest.ini` enforces `--cov` options.【e3e71f†L1-L6】【cbd5a4†L1-L7】
+  - `pytest --cov=src --cov-report=term-missing` repeats the same option-parsing failure.【cbd5a4†L1-L7】
+- **Coverage status:** Not measurable; the existing configuration mandates ≥85% but tooling cannot start until the coverage plugin is installed and Document AI dependency is stubbed.
+
+## 4. Static Analysis Results
+- **Ruff 0.12.11:** 45 findings, including unused imports (`src/config.py`, `src/main.py`), unused variables (`docai_batch_helper.py`), style violations (semicolon usage in PDF writer), and module import ordering issues across utilities/tests.【8642ef†L1-L118】
+  - Example: `import os` is unused in `src/config.py` post-refactor.【8642ef†L4-L13】
+- **Mypy 1.17.1:** 24 errors. Highlights include missing required args when instantiating `AppConfig`, missing type stubs (`requests`), and Google Cloud stubs absent for storage/documentai modules.【2c610b†L1-L9】【67c788†L1-L4】【d890e9†L1-L18】
+- **Bandit / pip-audit:** Could not be executed because the packages are unavailable via the restricted proxy (`bandit` and `pip-audit` commands missing).【dd89a9†L1-L5】【5cdbf2†L1-L3】
+
+## 5. Endpoint & Contract Review
+- Defined routes: `/healthz`, `/health`, `/readyz`, `/`, `/process` (file upload), `/process_drive` (Drive), `/metrics` (optional), `/ping_openai` (diagnostic).【F:src/main.py†L95-L214】
+- **Status codes:** Custom exception handlers map validation errors to 400, OCR failures to 502, summariser/PDF errors to 500.【F:src/main.py†L81-L171】
+- **Schema exposure:** OpenAPI still reflects these routes, but when module-level `create_app()` fails the fallback `FastAPI(title='... (init failure)')` exposes only docs endpoints, leading to silent production degradation.【F:src/main.py†L249-L255】【ab8b99†L1-L2】
+- **Async correctness:** Routes are `async` but invoke synchronous, blocking service methods (`ocr.process`, `sm.summarise`, `pdf.build`), so concurrency relies entirely on worker scaling rather than cooperative IO.
+
+## 6. Summariser Pipeline Review
+- **Chunking:** `Summariser` uses `SummarizerChunker` to split sanitized text into ~2.5k character segments with deterministic ordering.【F:src/services/summariser.py†L310-L378】
+- **Merge logic:**
+  - `_merge_field` now attempts to coerce dict/list values via `json.dumps` before stripping, addressing the prior `'dict' object has no attribute 'strip'` exception.【F:src/services/summariser.py†L430-L448】
+  - `_merge_list_field` flattens dict/list/set inputs and deduplicates while preserving order.【F:src/services/summariser.py†L453-L489】
+- **Root cause recap:** Earlier versions assumed every chunk field was a string and called `.strip()` directly, exploding when OpenAI returned a dict. The current guard converts dict/list/set/int to strings before stripping, preventing the AttributeError but still lacking schema validation to guarantee required keys are present.【F:src/services/summariser.py†L351-L384】【F:src/services/summariser.py†L430-L505】
+- **Schema gaps:** No JSON schema or Pydantic model verifies chunk completeness. Missing keys silently degrade to `'N/A'`, making regressions hard to detect. Proposed fix: introduce a `ChunkSchema` dataclass/Pydantic model and validate each chunk before merging.
+- **Determinism:** Narrative sections and index lists are assembled with static ordering; dedupe uses set semantics, ensuring consistent output per chunk order.【F:src/services/summariser.py†L491-L520】
+- **Retry & budgeting:** `OpenAIBackend` retries up to six times with exponential backoff but uses blocking `time.sleep`, so worst-case latency per chunk can exceed three minutes and ties up the worker thread.【F:src/services/summariser.py†L114-L188】
+
+## 7. OCR & Drive Integration Review
+- **Synchronous OCR:** `OCRService` immediately constructs a `DocumentProcessorServiceClient` in `__post_init__`, requiring valid ADC even when running tests with stubs.【F:src/services/docai_helper.py†L92-L123】
+- **Batch processing:** Large documents trigger `batch_process_documents_gcs` and optionally `split_pdf_by_page_limit`, but both utilities hard-code intake/output buckets (`quantify-agent-intake/output`).【F:src/services/docai_batch_helper.py†L20-L123】【F:src/utils/pdf_splitter.py†L30-L118】
+- **Error handling:** Tenacity retries transient `ServiceUnavailable`/`DeadlineExceeded`. Permanent errors bubble as `OCRServiceError`. However, missing configuration (e.g., no Drive folder IDs) is only detected at startup event, not before heavy client instantiation.
+- **Drive client:** `_drive_service` optionally loads credentials from `GOOGLE_APPLICATION_CREDENTIALS`; uploads include `supportsAllDrives=True`. Missing folder IDs raise runtime errors, so Cloud Run must wire both folder env vars.【F:src/services/drive_client.py†L17-L78】
+
+## 8. PDF Writer Review
+- `MinimalPDFBackend` produces deterministic single-page PDFs for tests; `ReportLabBackend` available for richer output.【F:src/services/pdf_writer.py†L17-L121】
+- Structured indices appended when `_diagnoses_list` etc. exist, using bullet formatting.【F:src/services/pdf_writer.py†L152-L187】
+- `PDFWriter.build` still assumes dictionary values expose `.strip()`, so callers must fully normalise summary entries. This is acceptable after the summariser fix but should be guarded with isinstance checks to fail fast if schema breaks again.【F:src/services/pdf_writer.py†L138-L171】
+
+## 9. Config & Secrets Review
+- **Required env vars:** `PROJECT_ID`, `REGION`, `DOC_AI_PROCESSOR_ID`, `OPENAI_API_KEY`, `DRIVE_INPUT_FOLDER_ID`, `DRIVE_REPORT_FOLDER_ID`; all enforced by `validate_required()`.【F:src/config.py†L57-L86】
+- **Alias regression:** `DOC_AI_OCR_PROCESSOR_ID` is no longer recognised, yet legacy tooling (Cloud Build, tests) still sets it, so deployments silently fall back to `'missing-processor'` and fail downstream.【F:cloudbuild.yaml†L21-L32】【F:tests/conftest.py†L7-L12】
+- **Stub mode removed:** `STUB_MODE` was previously a guard for local testing. Without it, every environment (including CI) must provide production credentials.
+- **Optional `.env`:** Not committed; recommended sample added in this audit (`/audit/.env.example`).
+
+## 10. Observability & Logging
+- `configure_logging()` installs `JsonFormatter`, but the formatter only serialises `{ts, level, logger, msg, request_id}`. Any `extra` fields (e.g., `phase`, `summary_stage`, `trace_id`) are dropped, violating observability requirements.【F:src/logging_setup.py†L19-L42】【F:src/main.py†L189-L207】
+- Route middleware adds minimal debugging for health checks, but there is no request-level structured logging or trace propagation beyond the `request_id` context var.
+- Sensitive payloads (OpenAI responses) are logged in full by `/ping_openai`.
+
+## 11. Docker & Cloud Run Review
+- Docker image:
+  - Uses `python:3.11-slim`, installs deps with constraints, runs as non-root and exposes port 8080.【F:Dockerfile†L1-L49】
+  - `CMD` hardcodes `--port 8080`; should respect `$PORT` for portability.
+- Cloud Build:
+  - Builds image, runs tests/smoke, then deploys via `gcloud run deploy` with outdated env vars and without secrets for Drive/OpenAI.【F:cloudbuild.yaml†L21-L32】
+  - No explicit concurrency/memory settings; relies on defaults.
+
+## 12. Security & Compliance
+- No secret leaks in repo scan (only README placeholders).【b4a873†L1-L2】
+- `/ping_openai` returns `resp.text[:120]`, which could include model metadata or error details; logging this to stdout risks exposing sensitive OpenAI responses and hints that sanitised keys existed.【F:src/main.py†L210-L244】
+- Logs do not redact PHI/PII if downstream services emit it; summariser output is written verbatim to logs when errors occur (`summariser_retry_attempt`).
+
+## 13. Performance & Cost
+- FastAPI handlers call synchronous OCR, Drive, and OpenAI code, so each request consumes a worker thread for the entire duration. Retries with `time.sleep` exacerbate latency and cost under failure bursts.【F:src/services/summariser.py†L114-L188】
+- No caching of DocAI or summariser results; repeated Drive downloads for the same file will repeat the full pipeline.
+- Hard-coded GCS buckets prevent multi-tenant cost segregation.
+
+## 14. CI/CD Review
+- **GitHub Actions:** Installs only runtime deps and pylint, so pytest fails immediately due to missing `pytest-cov`. Environment variables also use the removed `DOC_AI_OCR_PROCESSOR_ID`, so even after fixing dependencies the suite would crash when instantiating Document AI.【F:.github/workflows/ci.yml†L21-L41】
+- **Cloud Build:** Runs `pip install -r requirements-dev.txt` (good) but deploys with incomplete env wiring, guaranteeing post-deploy failure.【F:cloudbuild.yaml†L21-L36】
+- No caching or artifact retention; builds always re-download wheels.
+
+## 15. Prioritized Remediation Plan
+| # | Finding | Severity | Impact | Effort | Owner | ETA |
+|---|---------|----------|--------|--------|-------|-----|
+| 1 | Gate Document AI client creation behind stub/lazy mode | Blocker | Unblocks local/CI tests and non-GCP devs | M | Backend | 2d |
+| 2 | Restore config aliases & stub mode, update deployment env wiring | Blocker | Enables Cloud Run startup & legacy tooling | M | Backend | 2d |
+| 3 | Fix CI dependency install (`requirements-dev`), inject Drive/OpenAI env secrets | Blocker | Re-enables coverage gating & deployment confidence | S | DevOps | 1d |
+| 4 | Extend JsonFormatter to emit `extra` fields | High | Restores structured telemetry (`phase`, `summary_stage`) | S | Platform | 1d |
+| 5 | Parameterise GCS buckets & allow override via config | High | Prevents data landing in wrong project | M | Backend | 3d |
+| 6 | Add chunk schema validation + explicit error logs for summariser | High | Catches malformed OpenAI outputs early | M | Backend | 2d |
+| 7 | Sanitize `/ping_openai` output or guard behind auth | Medium | Reduces leakage risk | S | Security | 1d |
+| 8 | Make Docker/uvicorn honour `$PORT` & allow worker tuning via env | Medium | Improves deploy portability | S | Platform | 1d |
+| 9 | Replace blocking sleeps with async-aware retry or background executor | Medium | Improves throughput | M | Backend | 3d |
+| 10| Add integration tests covering Drive/GCS stub flows under stub mode | Medium | Prevent regressions of Bible-spec pipeline | M | QA | 3d |
+
+## 16. Proposed Code Diffs (unapplied)
+```diff
+diff --git a/src/config.py b/src/config.py
+@@
+-    doc_ai_processor_id: str = Field('', validation_alias='DOC_AI_PROCESSOR_ID')
++    doc_ai_processor_id: str = Field(
++        '',
++        validation_alias=AliasChoices('DOC_AI_PROCESSOR_ID', 'DOC_AI_OCR_PROCESSOR_ID'),
++    )
++    stub_mode_raw: str | bool | None = Field(False, validation_alias='STUB_MODE')
+@@
+-        strict_env_presence = {"PROJECT_ID", "DOC_AI_PROCESSOR_ID", "OPENAI_API_KEY", "DRIVE_INPUT_FOLDER_ID", "DRIVE_REPORT_FOLDER_ID"}
++        if self.stub_mode:
++            return
++        strict_env_presence = {
++            "PROJECT_ID",
++            "DOC_AI_PROCESSOR_ID",
++            "DOC_AI_OCR_PROCESSOR_ID",
++            "OPENAI_API_KEY",
++            "DRIVE_INPUT_FOLDER_ID",
++            "DRIVE_REPORT_FOLDER_ID",
++        }
+@@
+     def use_structured_summariser(self) -> bool:
+         ...
++    @property
++    def stub_mode(self) -> bool:
++        raw = self.stub_mode_raw
++        if isinstance(raw, bool):
++            return raw
++        return parse_bool(str(raw)) if raw is not None else False
+```
+
+```diff
+diff --git a/src/services/docai_helper.py b/src/services/docai_helper.py
+@@
+-        self._client_factory = self.client_factory or _default_client
+-        # Build regional endpoint (region previously doc_ai_location)
+-        self._endpoint = f"{self._cfg.region}-documentai.googleapis.com"
+-        self._client = self._client_factory(self._endpoint)
++        self._client_factory = self.client_factory or _default_client
++        self._endpoint = f"{self._cfg.region}-documentai.googleapis.com"
++        self._client = None
++        if not self._cfg.stub_mode:
++            self._client = self._client_factory(self._endpoint)
+@@
+-        try:
+-            result = self._client.process_document(request=request)
++        client = self._client or self._client_factory(self._endpoint)
++        if self._cfg.stub_mode:
++            raise OCRServiceError("DocAI disabled in stub mode")
++        try:
++            result = client.process_document(request=request)
+```
+
+```diff
+diff --git a/src/main.py b/src/main.py
+@@
+-    app.state.ocr_service = OCRService(cfg.doc_ai_processor_id or 'missing-processor')
++    app.state.ocr_service = OCRService(
++        cfg.doc_ai_processor_id or 'missing-processor',
++        config=cfg,
++        client_factory=None if cfg.stub_mode else None,
++    )
+@@
+-    app.state.summariser = summariser_cls(OpenAIBackend(api_key=sanitized_key, model=selected_model))
++    backend = OpenAIBackend(api_key=sanitized_key, model=selected_model)
++    app.state.summariser = summariser_cls(backend)
+```
+
+```diff
+diff --git a/src/logging_setup.py b/src/logging_setup.py
+@@
+-        data: Dict[str, Any] = {
+-            "ts": datetime.now(timezone.utc).isoformat(),
+-            "level": record.levelname,
+-            "logger": record.name,
+-            "msg": record.getMessage(),
+-        }
++        data: Dict[str, Any] = {
++            "ts": datetime.now(timezone.utc).isoformat(),
++            "level": record.levelname,
++            "logger": record.name,
++            "msg": record.getMessage(),
++        }
++        for key, value in record.__dict__.items():
++            if key not in {"msg", "args", "levelname", "levelno", "pathname", "filename", "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs", "relativeCreated", "thread", "threadName", "processName", "process"}:
++                data[key] = value
+```
+
+```diff
+diff --git a/cloudbuild.yaml b/cloudbuild.yaml
+@@
+-      - --set-env-vars=PROJECT_ID=$PROJECT_ID,DOC_AI_LOCATION=us,DOC_AI_OCR_PROCESSOR_ID=processor-placeholder,USE_STRUCTURED_SUMMARISER=true
++      - --set-env-vars=PROJECT_ID=$PROJECT_ID,REGION=us,DOC_AI_PROCESSOR_ID=processor-placeholder,DRIVE_INPUT_FOLDER_ID=drive-intake,DRIVE_REPORT_FOLDER_ID=drive-report,USE_STRUCTURED_SUMMARISER=true
++      - --set-secrets=OPENAI_API_KEY=projects/$PROJECT_ID/secrets/OPENAI_API_KEY:latest
+```
+
+```diff
+diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
+@@
+-        - name: Install dependencies
+-          run: |
+-            python -m pip install --upgrade pip
+-            pip install -r requirements.txt
+-            pip install pylint
++        - name: Install dependencies
++          run: |
++            python -m pip install --upgrade pip
++            pip install -r requirements-dev.txt
++            pip install pylint
+@@
+-        - name: Run tests with coverage
+-          env:
+-            PROJECT_ID: test-project
+-            DOC_AI_LOCATION: us
+-            DOC_AI_OCR_PROCESSOR_ID: dummy
+-            OPENAI_API_KEY: dummy
+-            STUB_MODE: 'true'
+-            USE_STRUCTURED_SUMMARISER: 'true'
+-          run: |
+-            pytest
++        - name: Run tests with coverage
++          env:
++            PROJECT_ID: test-project
++            REGION: us
++            DOC_AI_PROCESSOR_ID: dummy
++            DRIVE_INPUT_FOLDER_ID: drive-in
++            DRIVE_REPORT_FOLDER_ID: drive-out
++            OPENAI_API_KEY: mock-test-key
++            STUB_MODE: 'true'
++            USE_STRUCTURED_SUMMARISER: 'true'
++          run: |
++            pytest -q --maxfail=1
+```
+
+## 17. Tests to Add/Amend
+- **`tests/test_main_integration.py`** – Add a fixture that sets `STUB_MODE=true` and patches `OCRService` to a no-op to verify `create_app()` succeeds without ADC, then assert `/process` and `/process_drive` use injected stubs.
+- **`tests/test_summariser_new_schema.py`** – Introduce a parametrised test that feeds dict/list/int chunk values and asserts that the merged output never raises and deduplicates correctly (guards future `'dict'.strip` regressions).
+- **`tests/test_logging_extras.py`** *(new)* – Instantiate `JsonFormatter` with a dummy record containing `phase`, `trace_id`, `summary_stage` extras and assert they appear in the JSON payload after the formatter patch.
+- **`tests/test_cloudbuild_config.py`** *(new)* – Validate that `cloudbuild.yaml` contains the modern env names and secrets, preventing future regression.
+
+## 18. Exact Commands for Build/Deploy on Cloud Run
+```bash
+# 1. Install tooling (once stub mode is implemented)
+pip install -r requirements-dev.txt
+
+# 2. Run static + unit tests with coverage
+env STUB_MODE=true DOC_AI_PROCESSOR_ID=test DRIVE_INPUT_FOLDER_ID=intake DRIVE_REPORT_FOLDER_ID=reports OPENAI_API_KEY=mock-key pytest -q --maxfail=1
+
+# 3. Build container
+gcloud builds submit --config cloudbuild.yaml --substitutions _TAG=v11k
+
+# 4. Deploy to Cloud Run with explicit secrets
+gcloud run deploy mcc-ocr-summary   --image gcr.io/$PROJECT_ID/mcc-ocr-summary:v11k   --region us-central1   --platform managed   --allow-unauthenticated   --set-env-vars PROJECT_ID=$PROJECT_ID,REGION=us,DOC_AI_PROCESSOR_ID=$PROCESSOR_ID,DRIVE_INPUT_FOLDER_ID=$DRIVE_IN,DRIVE_REPORT_FOLDER_ID=$DRIVE_OUT,USE_STRUCTURED_SUMMARISER=true   --set-secrets OPENAI_API_KEY=OPENAI_API_KEY:latest   --set-cloudsql-instances=""   --max-instances=3
+```
+
+## 19. Appendices
+### A. Raw Tool Outputs (trimmed)
+- `pip install -r requirements-dev.txt` proxy failure logs.【e9a47a†L1-L35】【770e0c†L1-L5】
+- `ruff check .` summary with representative violations.【8642ef†L1-L118】
+- `mypy .` errors for config/App instantiation and Google stubs.【2c610b†L1-L9】【67c788†L1-L4】【d890e9†L1-L18】
+- `pytest` failure due to missing `--cov` plugin.【e3e71f†L1-L6】【cbd5a4†L1-L7】
+- Runtime import of `src.main` succeeding only after logging init, but route list limited to docs when app bootstrap fails.【cd107f†L1-L1】【ab8b99†L1-L2】
+- `create_app` crash under missing ADC.【feffb6†L1-L25】
+
+### B. Environment Variable Catalog
+| Variable | Required | Default | Notes |
+|----------|----------|---------|-------|
+| `PROJECT_ID` | Yes | – | GCP project for Document AI & Drive.【F:src/config.py†L32-L65】 |
+| `REGION` / `DOC_AI_LOCATION` | Yes | `us` | Region for Document AI endpoint.【F:src/config.py†L33-L35】 |
+| `DOC_AI_PROCESSOR_ID` | Yes | – | Must alias legacy `DOC_AI_OCR_PROCESSOR_ID`.【F:src/config.py†L35-L63】 |
+| `OPENAI_API_KEY` | Yes | – | Required for `OpenAIBackend`; should come from Secret Manager.【F:src/config.py†L63-L86】 |
+| `DRIVE_INPUT_FOLDER_ID` | Yes | – | Source Drive folder for intake.【F:src/config.py†L63-L86】 |
+| `DRIVE_REPORT_FOLDER_ID` | Yes | – | Destination Drive folder for summaries.【F:src/config.py†L63-L86】 |
+| `GOOGLE_APPLICATION_CREDENTIALS` | Optional | – | Required locally for Drive/DocAI clients when not using stub mode.【F:src/services/drive_client.py†L17-L37】 |
+| `USE_STRUCTURED_SUMMARISER` | Optional | `True` | Enables new summariser variant.【F:src/config.py†L38-L55】 |
+| `OPENAI_MODEL` | Optional | Fallback list | Allows overriding default model chain.【F:src/main.py†L59-L72】 |
+| `STUB_MODE` | Proposed | `False` | Reintroduce to bypass live GCP dependencies during tests.【F:tests/conftest.py†L7-L12】 |
+
+### C. Risk Register
+| Risk | Likelihood | Impact | Mitigation |
+|------|------------|--------|------------|
+| Missing ADC during startup | High | Blocker | Implement stub/lazy client creation & document local setup. |
+| Wrong env vars in deployment | High | Blocker | Update config aliases and Cloud Build env wiring; add CI lint. |
+| Loss of structured logs | High | High | Extend JsonFormatter to emit extras; add regression test. |
+| Hard-coded buckets | Medium | High | Parameterise via config and secrets. |
+| Diagnostic endpoint leaking data | Medium | Medium | Sanitize output or require auth. |
+| Blocking retries (OpenAI) | Medium | Medium | Move to async-friendly retry or background tasks. |
+
+### D. Glossary
+- **ADC:** Application Default Credentials used by Google Cloud client libraries.
+- **DocAI:** Google Document AI processor powering OCR.
+- **Bible-spec:** Internal specification for the “Structured Summariser” output contract adopted in v11.
+- **Stub mode:** Legacy configuration allowing offline/test environments to bypass strict env validation and external clients.
+- **GCS:** Google Cloud Storage, used for batch OCR inputs/outputs.
