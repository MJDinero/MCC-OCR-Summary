# MCC-OCR-Summary Troubleshooting Guide

This guide summarises the operational runbooks observed during the v1.3.0 remediation cycle. Commands assume the active GCP project is `quantify-agent` and that you have `gcloud` authenticated (`gcloud auth login`).

---

## Health Checks & IAM Access

| Scenario | Command | Notes |
| --- | --- | --- |
| Verify liveness | `curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app/` | Returns `{"status":"ok"}`. Use `/` for Cloud Run health probes. |
| Legacy `/healthz` returns 404 | `curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app/healthz` | Cloud Load Balancer surfaces the GFE 404 page. Internal probes (within VPC) may still call `/healthz`. |
| 401 / unauthorised errors | Ensure the caller includes an identity token (`gcloud auth print-identity-token`) or uses a service account with `roles/run.invoker`. |
| Long running `curl` calls | Append `--max-time 360` for `process_drive` to allow DocAI batch fallback to finish. |

---

## Structured Logging Cheat Sheet

All runtime logs use `structured_log(...)` with an `event` key. Sample queries:

```bash
# Fetch the latest Drive upload events
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=\"mcc-ocr-summary\" AND jsonPayload.event=\"drive_upload_success\"" \
  --limit 10 --format='value(jsonPayload)' --project quantify-agent

# Trace a specific request end-to-end (replace TRACE_ID)
TRACE_ID=0a892075998a4f143040c52e8dacc8bb
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=\"mcc-ocr-summary\" AND jsonPayload.trace_id=\"$TRACE_ID\"" \
  --limit 200 --format='value(jsonPayload)' --project quantify-agent
```

Notable events:

- `drive_download_start` / `drive_download_success`
- `docai_call_start` / `docai_call_failure` / `docai_call_success` (phases: `docai_sync_primary`, `docai_batch`, `docai_chunked_sync`)
- `summary_failure`, `summary_too_short`
- `pdf_writer_start`, `pdf_writer_success`, `pdf_writer_failure`
- `drive_upload_start`, `drive_upload_success`
- `process_complete`

Set `DEBUG=true` (or pass `--debug` to `python -m src.runtime_server`) to increase log verbosity locally or during incident response.

---

## Supervisor Warnings

`supervisor_passed` may be `false` for extremely long summaries (e.g., >200 pages). This is recorded as a warning so pipelines can continue while signalling reviewers.

- Dynamic ratio floor is controlled by `MIN_SUMMARY_DYNAMIC_RATIO` (defaults to `0.005`).
- Absolute minimum characters default to `MIN_SUMMARY_CHARS=120`.
- Adjust these env vars if the downstream business process accepts shorter summaries for mega-documents.

Log query for supervisor flags:

```bash
gcloud logging read \
  "jsonPayload.event=\"supervisor_basic_check_failed\"" \
  --limit 20 --format='value(jsonPayload)' --project quantify-agent
```

---

## Drive & DocAI Failure Modes

1. **Credential / impersonation issues** – Look for `drive_credentials_invalid` or `drive_impersonation_check` events. Ensure the Cloud Run service account has `Content manager` access to the shared drive and the impersonation user still exists.
2. **Folder lookup problems** – `drive_folder_lookup_failed` indicates an invalid folder ID or missing shared drive access. Reconfirm `DRIVE_REPORT_FOLDER_ID` and `DRIVE_SHARED_DRIVE_ID`.
3. **DocAI page-limit errors** – Logs include `docai_call_failure` with `error=PAGE_LIMIT_EXCEEDED`. The service automatically escalates to batch and chunked sync fallbacks, but repeated failures may indicate a corrupt PDF.
4. **DocAI batch failures** – Error reason `Failed to process all documents` typically arises from malformed pages. Re-run with `RUN_URL/process` after manually splitting the source PDF.

---

## Common Recovery Steps

| Symptom | Corrective Action |
| --- | --- |
| `curl` returns 500 during `/process_drive` | Inspect logs for `docai_call_failure` or `drive_upload_failure`. Re-run with `DEBUG=true` and confirm Drive service account permissions. |
| Drive upload missing | Search for `drive_upload_start` but no `drive_upload_success`. Ensure `DRIVE_REPORT_FOLDER_ID` still exists and the user has write access to the shared drive. |
| LLM timeouts | Increase `MAX_OUTPUT_TOKENS` and `MAX_WORDS`, or split the input PDF earlier (chunker settings). |
| IAM 403 on Cloud Run | Re-run deployment with `--allow-unauthenticated` if public access is desired, or ensure calling identity has `roles/run.invoker`. |

---

## Useful Shortcuts

- Regenerate IAM token: `gcloud auth print-identity-token`
- Monitor long-running requests: `watch -n 15 'gcloud logging read "jsonPayload.event=\"process_complete\"" --limit 5 --format="value(jsonPayload.trace_id,jsonPayload.source,jsonPayload.summary_chars)"'`
- Tail logs locally: `gcloud beta logging tail "resource.type=cloud_run_revision AND resource.labels.service_name=\"mcc-ocr-summary\""`

---

For broader architectural concerns (IAM, Pub/Sub, CMEK), refer back to `README.md` and the `infra/` scripts.
