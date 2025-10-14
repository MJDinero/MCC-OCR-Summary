# MCC OCR Summary

Event-driven medical summarisation pipeline for MCC intake documents. PDFs arriving in the intake
bucket trigger an asynchronous workflow that splits large files, fans out Document AI OCR, produces a
refactored LLM summary, renders a signed PDF, and uploads the result back to GCS and Drive. All stages
emit structured logs with shared correlation fields (`job_id`, `trace_id`, `schema_version`, `duration_ms`)
to enforce observability and SLO tracking.

## Architecture

```
          +-----------------------+        +------------------------------+
Drive -> | Intake GCS (Eventarc) | -----> | FastAPI Ingest (Cloud Run)   |
          +-----------------------+        +------------------------------+
                                                     |
                                                     | launches Cloud Workflows (trace, job_id)
                                                     v
                                      +----------------------------------------+
                                      | Cloud Workflows orchestrator           |
                                      | - Splitter (DocAI)                     |
                                      | - OCR fan-out (max 12 shards)          |
                                      | - Summariser job (Cloud Run Job)       |
                                      | - PDF writer job (Cloud Run Job)       |
                                      +----------------------------------------+
                                                     |
                                                     v
                          +----------------------+    +------------------------+
                          | Summary JSON (GCS)   |    | PDF + signed URL (GCS) |
                          +----------------------+    +------------------------+
                                                     |
                                                     v
                                      +------------------------------+
                                      | Drive uploader (optional)   |
                                      +------------------------------+
```

Key configuration, service accounts, and deployment wiring are documented in the top-level
`pipeline.yaml` manifest.

## Service Breakdown

- **Ingestion API (`src/main.py`)**
  - Validates Eventarc payloads, enforces env validation, and persists jobs via `PipelineStateStore`.
  - Emits `ingest_received` log with full context and returns `412 Precondition Failed` on duplicates.
  - Launches Cloud Workflows with trace propagation and signed internal token.

- **Cloud Workflows (`workflows/pipeline.yaml`)**
  - Schedules splitter, OCR fan-out, summariser, and PDF writer steps.
  - Passes `--gcs-if-generation 0` / `--if-generation-match 0` to enforce idempotent uploads.
  - Updates pipeline state milestones through `/internal/jobs/{job_id}/events`.

- **Document AI helpers (`src/services/docai_helper.py`)**
  - Adds `split_done`, `ocr_lro_started`, `ocr_lro_finished` markers with duration, shard id, attempt.
  - Deduplicates shards and persists manifest using `ifGenerationMatch`.

- **Summariser job (`src/services/summariser_refactored.py`)**
  - Strict JSON schema enforcement (`schema_version="2025-10-01"`) for chunk responses.
  - Emits `summary_done` log with duration, schema version, job metadata, and trace linkage.

- **PDF writer job (`src/services/pdf_writer_refactored.py`)**
  - Defaults `--if-generation-match 0`, logs `pdf_writer_complete` with pipeline context.
  - Updates state store with PDF URI and signed URLs; reuses new Drive client context.

- **Drive client (`src/services/drive_client.py`)**
  - Upload logs include pipeline metadata (`drive_upload_complete`) for metrics & SLOs.

## SLOs & Monitoring

- **Latency**: p95 end-to-end < 10 minutes (tracked via log-based metric chaining required markers).
- **Error Rate**: < 1% ingestion/API 5xx (Cloud Run request_count metric, burn rate alert).
- **DLQ Depth**: 0 messages in pipeline dead-letter topic.

Alert policies live in `infra/monitoring/alert_policies.yaml` (burn-rate, DLQ backlog, stalled OCR).
Dashboard queries and layout guidance are provided in `audit/sample_dashboards.md`. Log-based metrics
are derived from the structured markers above; ensure logs land in Cloud Logging with trace IDs.

## Runbook

- **Duplicate ingestion / idempotency**
  - Duplicate `/ingest` returns `412` with the existing `job_id`. Operators can inform clients that the
    original pipeline run continues; no manual dedupe required.
  - Replaying Cloud Workflows triggers will skip GCS uploads thanks to `ifGenerationMatch=0`.

- **DLQ drain**
  1. Inspect `mcc-ocr-pipeline-dlq` subscription via `gcloud pubsub subscriptions pull`.
  2. For retriable events, re-trigger ingestion with the original payload (ensure new trace id).
  3. For poisoned messages, record in incident doc and acknowledge to unblock backlog.

- **Rollback**
  - Latest successful canary revision is stored in Cloud Build artifact `/workspace/rollback_revision.txt`.
  - Execute:
    ```
    gcloud run services update-traffic mcc-ocr-summary \
      --region ${REGION} --to-revisions PREVIOUS=100
    ```
    or use the concrete revision printed by the `record-rollback-info` step.

## Operations

- **CI/CD Pipeline**
  - `cloudbuild.yaml` stages:
    1. `lint-and-type` (`ruff`, `mypy`)
    2. `unit-tests` (pytest offline, coverage ≥85%)
    3. `integration-tests` (DocAI/OpenAI mocked)
    4. Build runtime image
    5. Deploy ingestion canary (5%)
    6. Deploy summariser/PDF Cloud Run Jobs
    7. Deploy Workflow definition
    8. `scripts/e2e_smoke.sh` (signed URL validation, log sequence, duplicate ingest = 412)
    9. Promote to 100% or record rollback command

  - Trigger build:
    ```bash
    gcloud builds submit \
      --config cloudbuild.yaml \
      --substitutions=_PROJECT=${PROJECT_ID},_REGION=${REGION},\
_DOC_AI_PROCESSOR_ID=${DOC_AI_PROCESSOR_ID},_DOC_AI_SPLITTER_PROCESSOR_ID=${DOC_AI_SPLITTER_PROCESSOR_ID},\
_INTAKE_BUCKET=${INTAKE_BUCKET},_OUTPUT_BUCKET=${OUTPUT_BUCKET},_SUMMARY_BUCKET=${SUMMARY_BUCKET},\
_STATE_BUCKET=${STATE_BUCKET},_STATE_PREFIX=pipeline-state,_WORKFLOW_NAME=${WORKFLOW_NAME}
    ```

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
