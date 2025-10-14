# MCC OCR Summary

Modular, event-driven pipeline that converts medical PDF intake documents into redacted, hierarchical summaries using Google Cloud services. The architecture is engineered for documents well beyond 200 pages by streaming OCR output, chunking summaries, and persisting results securely with CMEK.

---

## Architecture Overview

```
            +------------------+     Pub/Sub      +-------------------+
 GCS Intake |  ocr_topic       |=================>|  OCR Service      |
  (Event)   |  ocr_dlq         |                  |  Document AI      |
            +------------------+                  |  Chunk Publisher  |
                       |                          +-------------------+
                       | summary_topic                              |
                       v                                            |
            +------------------+     Pub/Sub      +-------------------+
            | summarisation    |=================>|  Summarisation    |
            | summary_dlq      |                  |  Hierarchical LLM |
            +------------------+                  |  Storage Request  |
                       |                          +-------------------+
                       | storage_topic                              |
                       v                                            |
            +------------------+                  +-------------------+
            | storage_topic    |=================>|  Storage Service  |
            | storage_dlq      |                  |  BigQuery + GCS   |
            +------------------+                  +-------------------+
```

- **OCR Service (`src/services/ocr_service.py`)** – Streams Document AI output page-by-page, chunking on the fly and publishing `ocr.chunk.ready` messages with retry and idempotency controls.
- **Summarisation Service (`src/services/summarization_service.py`)** – Consumes chunks, performs hierarchical summarisation via Gemini (or pluggable LLM), persists chunk summaries, and emits a single `storage.persist.requested` message per job.
- **Storage Service (`src/services/storage_service.py`)** – Writes aggregated summaries to CMEK-protected GCS and BigQuery with strict idempotency.
- **Shared Modules** – `src/models/events.py`, `src/services/chunker.py`, `src/services/summary_store.py`, `src/services/summary_repository.py`, and `src/utils/redact.py` provide typed messages, streaming chunking, persistence, and PHI/PII redaction.

### Key Guarantees

- **Streaming I/O**: No stage loads the entire PDF or OCR output into memory. Chunking and summarisation operate on iterators with bounded windows.
- **Idempotency**: All GCS writes use `if_generation_match=0`; Pub/Sub message metadata carries `job_id` + `chunk_id` for dedupe.
- **Observability**: Structured JSON logs with `trace_id` and `job_id`; Prometheus metrics exported for latency and DLQ counts.
- **Security**: Secrets sourced from Secret Manager, artifacts encrypted with CMEK, logs redacted via `utils/redact.py`, and diagnostic endpoints gated by `ENABLE_DIAG_ENDPOINTS`.

---

## Quick Start

### Prerequisites

- Python 3.11+
- `gcloud` CLI with authenticated project access
- Document AI processor (OCR)
- Secret Manager secrets:
  - `mcc-ocr-openai-key` (or equivalent Gemini credential)
  - CMEK key references for intake/summary/output buckets

### Local Development

```bash
# Install dependencies
make install

# Run format + type + tests
make lint
make type
make test            # runs pytest with coverage ≥85%

# Execute targeted tests
python3 -m pytest tests/test_summarization_service_pipeline.py -q
```

Environment variables can be set via `.env` (see `.env.template`). Core configuration lives in `src/config.py`; overrides can come from env or YAML.

### Cloud Run Deployment

1. Build the image:
   ```bash
   gcloud builds submit --config cloudbuild.yaml \
     --substitutions=_PROJECT=$PROJECT_ID,_REGION=$REGION
   ```
2. Deploy services (Pub/Sub handlers can run as Cloud Run jobs or GKE workloads). Example:
   ```bash
   make deploy \
     PROJECT_ID=$PROJECT_ID \
     REGION=$REGION \
     SERVICE=mcc-ocr-summary
   ```
3. Terraform or scripts in `infra/` provision Pub/Sub topics, subscriptions, and service accounts with least-privilege IAM.

---

## Configuration Reference

`src/config.py` exposes the canonical settings:

- Pub/Sub topics & subscriptions (`OCR_TOPIC`, `SUMMARY_TOPIC`, `STORAGE_TOPIC`, matching DLQs)
- Document AI processor IDs (`DOC_AI_PROCESSOR_ID`)
- LLM options (`MODEL_NAME`, `TEMPERATURE`, `MAX_OUTPUT_TOKENS`, `MAX_WORDS`)
- Chunking (`CHUNK_SIZE`, default 4000 tokens)
- Storage destinations (`SUMMARY_OUTPUT_BUCKET`, `SUMMARY_BIGQUERY_DATASET`, `SUMMARY_BIGQUERY_TABLE`)
- Security flags (`CMEK_KEY_NAME`, `ENABLE_DIAG_ENDPOINTS`)

All services consume the same config module, enabling override via `ConfigMap` or Cloud Run env vars.

---

## Security & Privacy

- **Secrets**: Retrieved at runtime from Secret Manager. No secrets stored in source control.
- **Encryption**: Intake, summary, and output buckets require CMEK (`CMEK_KEY_NAME`). BigQuery tables enforce CMEK-aligned dataset.
- **Redaction**: All logs pass through `utils/redact.py` before emitting messages that contain user-provided text.
- **IAM**: Each service (OCR, summarisation, storage) runs with its own service account scoped to the minimum roles listed in `AGENTS.md`.
- **Diagnostics**: `ENABLE_DIAG_ENDPOINTS=false` by default; enable only for controlled debugging sessions.

---

## Observability

- **Metrics**: `ocr_latency_seconds`, `summarization_latency_seconds`, `jobs_completed_total`, and `dlq_messages_total` exported via Prometheus (`src/services/metrics.py`).
- **Logs**: Structured JSON logs with `trace_id`, `job_id`, `stage` fields. Pub/Sub message attributes also mirror correlation IDs.
- **Monitoring**: `infra/monitoring/alert_policies.yaml` defines burn-rate alerts for DLQ backlog and latency regression. Extend with GCS / BigQuery metrics as needed.

---

## Benchmarks

Use `scripts/benchmark_large_docs.py` to profile end-to-end performance. Example output (n1-standard-4 runner, Gemini Pro 2024-10):

| Pages | Median Runtime | Peak RSS | Notes                          |
|-------|----------------|----------|--------------------------------|
| 10    | 48 s           | 420 MB   | Single summarisation pass      |
| 50    | 3 m 12 s       | 640 MB   | Hierarchical aggregator stable |
| 200   | 11 m 05 s      | 910 MB   | Document AI dominates runtime  |
| 500   | 24 m 40 s      | 1.2 GB   | Requires concurrency=4         |

Re-run benchmarks after model or configuration updates. Results feed the README and CI quality gates.

---

## CI / CD

1. `make lint` (ruff + pylint)  
2. `make type` (mypy)  
3. `make test` (pytest, coverage ≥85%, include new pipeline tests)  
4. `make audit-deps` (pip-audit)  
5. `make sbom` (CycloneDX SBOM)  
6. Build + deploy Cloud Run images  
7. Execute smoke & integration tests (`pytest -m integration`, `scripts/smoke_test.py`)  

`cloudbuild.yaml` orchestrates the same stages in Cloud Build. GitHub Actions mirrors the workflow for pull requests.

---

## Troubleshooting

- **DLQ Growth** – Inspect the relevant DLQ subscription (`ocr_dlq`, `summary_dlq`, `storage_dlq`). Replay by re-publishing the original message with a new `trace_id`.
- **Context Budget Exceeded** – Lower `MAX_WORDS` or reduce `CHUNK_SIZE`; the chunker is configurable without redeployment.
- **Document AI Throttling** – Increase exponential backoff parameters in `OCRService._process_with_retry` or scale concurrency via Pub/Sub subscription settings.
- **LLM Timeouts** – Adjust `MAX_OUTPUT_TOKENS` and `TEMPERATURE`, or switch the `LanguageModelClient` implementation to Vertex AI streaming.

For further details, see `AGENTS.md` and `audit/technical_audit_v11j.md`.

- **Smoke Test (staging / prod)**
  ```bash
  PROJECT_ID=... REGION=... INTAKE_BUCKET=... SERVICE_URL=https://mcc-ocr-summary-... \
  ./scripts/e2e_smoke.sh
  ```
  The script uploads a fixture PDF, waits for `/status` to reach `UPLOADED`, validates the signed URL,
  asserts duplicate ingest returns `412`, and verifies log markers in chronological order.

- **Log inspection**
  ```bash
  gcloud logging read \
    'jsonPayload.job_id="JOB123" AND resource.type="cloud_run_revision"' \
    --project "${PROJECT_ID}" --limit 100 --order asc
  ```

## IAM & Secrets

- Provision SAs with `infra/iam.sh`:
  - Ingestion: `roles/run.invoker`, `roles/workflows.invoker`, `roles/secretmanager.secretAccessor`,
    `roles/logging.logWriter`, `roles/monitoring.metricWriter`.
  - Workflow: + `roles/documentai.apiUser`, storage viewer/creator, `roles/pubsub.publisher`.
  - Summariser/PDF jobs: storage viewer/creator, secret accessor, logging/monitoring, PDF job also
    `roles/iam.serviceAccountTokenCreator` for optional signed URL KMS.
- All secrets (OpenAI API key, internal tokens) read from Secret Manager URIs defined in
  `infra/runtime.env.sample`.

## Local Development & Testing

- Python 3.11 virtualenv recommended:
  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  python -m pip install -r requirements-dev.txt
  ```
  (Note: CI installs dependencies offline inside Cloud Build. Local environment requires internet.)

- Run FastAPI locally:
  ```bash
  uvicorn src.main:app --reload --port 8080
  ```

- Offline unit tests (mocks OpenAI/DocAI/GCS):
  ```bash
  pytest -q -m "not integration" --cov=src --cov-report=term-missing --cov-fail-under=85
  ```
  Integration tests (`-m "integration"`) rely on the same mocks but execute broader flows.

- Lint/type:
  ```bash
  ruff check . && mypy .
  ```

## Release Checklist

- [ ] Confirm `scripts/e2e_smoke.sh` passes (signed URL 200, duplicate = 412, ordered markers).
- [ ] Review Cloud Logging for `ingest_received → split_done → ocr_lro_finished → summary_done → pdf_writer_complete → drive_upload_complete`.
- [ ] Ensure alert policies deployed (`gcloud monitoring policies create --policy-from-file=infra/monitoring/alert_policies.yaml`).
- [ ] Validate IAM via `infra/iam.sh` and `gcloud projects get-iam-policy`.
- [ ] Promote canary only after smoke test success; note rollback command from Cloud Build logs.
