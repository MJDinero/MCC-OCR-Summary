# MVP Execution Report (2025-10-15)

## Runtime Context
- Branch: `mvp-build`
- Latest deploy: Cloud Run revision `mcc-ocr-summary-00114-td2`
- Service URL: `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
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
- Cloud Build deploy: `gcloud builds submit --config cloudbuild.yaml --substitutions _TAG=v11mvp,_IMAGE_REPO=us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary` → **success** (revision `00114-td2`).
- Tests executed locally (Python 3.12.8 / `.venv`):
  1. `python -m pytest tests/test_main_integration.py tests/test_summariser_structured.py --maxfail=1 -q --no-cov`
  2. `python -m pytest tests/test_pipeline_endpoints.py tests/test_process_validation.py -q --no-cov`
- Cloud Run `/process` invocation (sample PDF) reaches Document AI + OpenAI; supervisor validation passes.

## Drive Remediation (2025-10-17)
- Updated Cloud Run env vars to target **MedCostContain – Team Drive** (`DRIVE_SHARED_DRIVE_ID=0AFPP3mbSAh_oUk9PVA`) and the **Output Folder** (`DRIVE_REPORT_FOLDER_ID=1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`).
- Enabled domain-wide delegation for `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com` and set `DRIVE_IMPERSONATION_USER=Matt@moneymediausa.com` so uploads run under a user with quota.
- Service account key stored at `/secrets/mcc-orch-sa-key.json` rotated and protected with CMEK; Cloud Run mount updated accordingly.
- Outbound command reference:
  ```
  gcloud run services update mcc-ocr-summary \
    --region us-central1 \
    --set-env-vars "DRIVE_SHARED_DRIVE_ID=0AFPP3mbSAh_oUk9PVA" \
    --set-env-vars "DRIVE_REPORT_FOLDER_ID=1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE" \
    --set-env-vars "DRIVE_IMPERSONATION_USER=Matt@moneymediausa.com" \
    --set-env-vars "PROJECT_ID=quantify-agent" \
    --set-env-vars "DOC_AI_LOCATION=us" \
    --set-env-vars "DOC_AI_OCR_PROCESSOR_ID=21c8becfabc49de6" \
    --set-env-vars "GOOGLE_APPLICATION_CREDENTIALS=/secrets/mcc-orch-sa-key.json"
  ```
- Validation status (2025-10-17 18:44Z):
  * `/process` invocation returns HTTP 500 because Cloud Storage upload fails with `403 Permission denied on Cloud KMS key`
  * `drive_*` logs not emitted yet (upload blocked before Drive stage); resolve CMEK IAM then re-test

## Next Steps
1. Grant `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com` Cloud KMS Encrypter/Decrypter on `projects/quantify-agent/locations/us-central1/keyRings/mcc-phi/cryptoKeys/mcc-phi-key` so Cloud Storage uploads succeed.
2. Re-run the `/process` curl test and capture `drive_impersonation_user` + `drive_upload_complete (parent=1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE)` logs.
3. Archive/rename the legacy "MCC artifacts" folder in Drive once new uploads confirmed.

## Cloud Run Recovery (2025-10-20)
- Branch `ops/v1.1.2-remediation` now carries:
  * `fix(ocr): fallback to batch_process_documents_gcs on PAGE_LIMIT_EXCEEDED`
  * `fix(docai): relax kms usage when storage bucket location differs`
  * `fix(ocr): add chunked sync fallback for oversized PDFs`
- Document AI flow changes:
  * Introduced `_process_via_batch` helper reuse and chunked synchronous fallback (10-page slices) when both sync + batch paths fail.
  * Added KMS/location guardrails in `batch_process_documents_gcs` to avoid incompatible bucket uploads while logging mismatches.
- Deployment:
  * Built `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:v1.1.2-pdf-fix-20251020102554`.
  * Cloud Run revision `mcc-ocr-summary-00147-rwz` (URL `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`) serves 100% traffic.
  * Environment updates include `MIN_SUMMARY_DYNAMIC_RATIO=0.005` to accommodate OpenAI output length.
- Validation:
  * `curl -X POST "$RUN_URL/process"` with ENTTEC PDF → **HTTP 200** (~150 s end-to-end).
  * Drive upload observed: `summary-34abeb11b01547caa6e2f229d145b884.pdf` in folder `130jJzsl3OBzMD8weGfBOaXikfEnD2KVg` (shared drive `0AFPP3mbSAh_oUk9PVA`).
  * Cloud Logging access for `mcc-orch-sa` still denied (`Permission denied for all log views`); IAM elevation required for future log pulls.
- Release:
  * Tag `v1.1.2` pushed to origin after successful deployment.
