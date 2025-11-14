# MVP Execution Report (2025-10-15)

## Runtime Context
- Branch: `mvp-build`
- Latest deploy: Cloud Run revision `demo-ocr-summary-00114-td2`
- Service URL: `https://demo-ocr-summary-uc.a.run.app`
- Target MODE: `mvp` (`STUB_MODE=false`, `WRITE_TO_DRIVE=true`)

## Changes Landed
- Hardened configuration for dedicated MVP mode (`.env.template`, `src/utils/mode_manager.py`, `src/services/supervisor.py`):
  * Forces lightweight supervisor path and disables retries.
  * Adds helper utilities for mode detection reused across services.
- Streamlined Cloud Build pipeline (`cloudbuild.yaml`):
  * Minimal build → push → deploy flow with `_TAG` and `_IMAGE_REPO` substitutions.
  * Enforces MVP env vars, DocAI location, structured summariser flag, and Secret Manager wiring.
  * Injects Drive/GCS buckets plus internal event token defaults.
- Document AI helper modernisation (`src/services/docai_helper.py`, `src/config.py`):
  * Explicit `doc_ai_location` support and regional endpoint fixes.
  * Removes deprecated `encryption_spec` usage; normalises protobuf responses via `MessageToDict`.
  * Ensures synchronous path is compatible with CMEK-configured processors.
- API surface updates (`src/main.py`):
  * Async summariser invocation to satisfy `Summariser.summarise_async`.
  * MVP Drive uploader adapter that honours runtime flags and logs structured metadata.
- Cloud Run environment now provisions:
  * `MODE=mvp`, `STUB_MODE=false`, `WRITE_TO_DRIVE=true`, `DOC_AI_LOCATION=us`.
  * Secret-backed `DOC_AI_PROCESSOR_ID` and `OPENAI_API_KEY`.

## Validation
- Cloud Build deploy: `gcloud builds submit --config cloudbuild.yaml --substitutions _TAG=v11mvp,_IMAGE_REPO=us-central1-docker.pkg.dev/demo-gcp-project/mcc/mcc-ocr-summary` → **success** (revision `00114-td2`).
- Tests executed locally (Python 3.12.8 / `.venv`):
  1. `python -m pytest tests/test_main_integration.py tests/test_summariser_structured.py --maxfail=1 -q --no-cov`
  2. `python -m pytest tests/test_pipeline_endpoints.py tests/test_process_validation.py -q --no-cov`
- Cloud Run `/process` invocation (sample PDF) reaches Document AI + OpenAI; supervisor validation passes.

## Drive Remediation (2025-10-17)
- Updated Cloud Run env vars to target the production Shared Drive (`DRIVE_SHARED_DRIVE_ID=<shared-drive-id>`) and Output Folder (`DRIVE_REPORT_FOLDER_ID=<report-folder-id>`).
- Enabled domain-wide delegation for `orchestrator-sa@demo-gcp-project.iam.gserviceaccount.com` and set `DRIVE_IMPERSONATION_USER=user@example.com` so uploads run under a quota-bearing user.
- Service account key stored at `/secrets/orchestrator-sa-key.json` rotated and protected with CMEK; Cloud Run mount updated accordingly.
- Outbound command reference:
  ```
  gcloud run services update mcc-ocr-summary \
    --region us-central1 \
    --set-env-vars "DRIVE_SHARED_DRIVE_ID=<shared-drive-id>" \
    --set-env-vars "DRIVE_REPORT_FOLDER_ID=<report-folder-id>" \
    --set-env-vars "DRIVE_IMPERSONATION_USER=user@example.com" \
    --set-env-vars "PROJECT_ID=demo-gcp-project" \
    --set-env-vars "DOC_AI_LOCATION=us" \
    --set-env-vars "DOC_AI_OCR_PROCESSOR_ID=projects/demo-gcp-project/locations/us/processors/processor-id" \
    --set-env-vars "GOOGLE_APPLICATION_CREDENTIALS=/secrets/orchestrator-sa-key.json"
  ```
- Validation status (2025-10-17 18:44Z):
  * `/process` invocation returns HTTP 500 because Cloud Storage upload fails with `403 Permission denied on Cloud KMS key`
  * `drive_*` logs not emitted yet (upload blocked before Drive stage); resolve CMEK IAM then re-test

## Next Steps
1. Grant `orchestrator-sa@demo-gcp-project.iam.gserviceaccount.com` Cloud KMS Encrypter/Decrypter on `projects/demo-gcp-project/locations/us-central1/keyRings/demo-kms/cryptoKeys/summary-pdfs` so Cloud Storage uploads succeed.
2. Re-run the `/process` curl test and capture `drive_impersonation_user` + `drive_upload_complete (parent=<report-folder-id>)` logs.
3. Archive/rename the legacy artifacts folder in Drive once new uploads confirmed.
