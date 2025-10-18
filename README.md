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

# Run static analysis + type + tests
make lint            # ruff + pylint (strict)
make type            # mypy --strict on critical services
make test            # pytest with coverage ≥90%

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

## Drive Configuration & Runtime Mapping

- **Shared Drive (MedCostContain – Team Drive)**: `0AFPP3mbSAh_oUk9PVA`
- **Intake Folder (Eventarc staging)**: `19xdu6hV9KNgnE_Slt4ogrJdASWXZb5gl`
- **Output Folder (Final summaries)**: `130jJzsI3OBzMDBweGfBOaXikfEnD2KVg`
- **Legacy Folder**: `MCC artifacts` (`1eyMOO126vfLBk3bBQE…`) — decommissioned; remove from configs and env vars.

### Domain-Wide Delegation & Impersonation

1. Grant `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com` **Content manager** access to the shared drive.
2. In Google Workspace Admin Console → Security → API controls → Domain-wide delegation, authorise the service account client ID with scope `https://www.googleapis.com/auth/drive`.
3. Set `DRIVE_IMPERSONATION_USER=Matt@moneymediausa.com` for Cloud Run, ensuring the runtime impersonates a user with quota.
4. Rotate the service-account key stored at `/secrets/mcc-orch-sa-key.json`; protect it with CMEK (Secret Manager supports CMEK on create/update).

### Cloud Run Environment Mapping

Provision the service with:

- `DRIVE_SHARED_DRIVE_ID=0AFPP3mbSAh_oUk9PVA`
- `DRIVE_INPUT_FOLDER_ID=19xdu6hV9KNgnE_Slt4ogrJdASWXZb5gl`
- `DRIVE_REPORT_FOLDER_ID=130jJzsI3OBzMDBweGfBOaXikfEnD2KVg`
- `DRIVE_IMPERSONATION_USER=Matt@moneymediausa.com`
- `DOC_AI_PROCESSOR_ID=21c8becfabc49de6`
- `PROJECT_ID=quantify-agent`
- `REGION=us-central1`
- `DOC_AI_LOCATION=us`
- `CMEK_KEY_NAME=projects/quantify-agent/locations/us/keyRings/mcc-keyring/cryptoKeys/mcc-ocr-summary`
- `GOOGLE_APPLICATION_CREDENTIALS=/tmp/google-application-credentials.json`

Apply these with `gcloud run services update mcc-ocr-summary --region us-central1 --set-env-vars ...` before triggering deployments.

---

## Security & Privacy

- **Secrets**: Retrieved at runtime from Secret Manager (see `src/utils/secrets.py`). Use `scripts/verify_cmek.sh` to validate CMEK coverage before releases.
- **Encryption**: Intake, summary, output, and state buckets plus the BigQuery dataset all enforce CMEK (`CMEK_KEY_NAME`).
- **Redaction**: All logs pass through `utils/redact.py` before emitting messages that contain user-provided text.
- **IAM**: Stage-scoped identities (`mcc-ocr-sa`, `mcc-summariser-sa`, `mcc-storage-sa`) are created via `infra/iam.sh` with least-privilege, and Cloud Build impersonates them only via `roles/iam.serviceAccountUser`.
- **Diagnostics**: `ENABLE_DIAG_ENDPOINTS=false` by default; enable only for controlled debugging sessions.

---

## Observability

- **Metrics**: Prometheus instrumentation is enabled by default (`/metrics` endpoint attached via `PrometheusMetrics.instrument_app`). Latency, throughput, DLQ counters, and job completions are emitted per stage.
- **Logs**: Structured JSON logs now include `stage`, `service`, `latency_ms`, `error_type`, and `redaction_applied` to streamline SRE triage.
- **Monitoring Assets**: Dashboards in `infra/monitoring/dashboard_pipeline_latency.json` and `infra/monitoring/dashboard_throughput_cpu_mem.json` visualise latency distributions and Cloud Run CPU/memory. Alert policies (`alert_dlq_backlog.json`, `alert_5xx_rate.json`, `alert_slo_breach.json`) enforce DLQ backlog, 5xx error rate, and pipeline SLOs.
- **Verification**: After deployment, run `gcloud monitoring dashboards create` / `alert-policies create` with the JSON manifests and confirm `/metrics` exposes Prometheus samples.

## Runtime Tuning

- **Worker Auto-sizing**: `src/runtime_server.py` computes `UVICORN_WORKERS` from available CPU cores (overridable via env) before starting Uvicorn.
- **Cloud Run Scaling**: `cloudbuild.yaml` deploys each revision with explicit concurrency & max instance caps (`_OCR_CONCURRENCY`, `_SUMMARY_CONCURRENCY`, `_STORAGE_CONCURRENCY`).
- **Temp Cleanup**: Batch OCR helper reuses CMEK-backed buckets and cleans transient uploads after completion.
- **Summary Floor**: `src/utils/summary_thresholds.py` enforces `max(120, int(0.35 * ocr_len))`; adjust `MIN_SUMMARY_DYNAMIC_RATIO` for ratio tuning. The base floor clamps to `120` even if `MIN_SUMMARY_CHARS` is higher, preventing short-doc rejections.

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
2. `make type` (mypy --strict on critical services)  
3. `make test` (pytest, coverage ≥90%, include new pipeline tests)  
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
