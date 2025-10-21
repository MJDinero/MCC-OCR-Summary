# Phase 4 Report – Deployment & Integration Validation

## Deployment
- Cloud Build `f9f598e6-1e7e-4243-a4ae-cbc7e5fbf717` produced image `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:v1.3.0-20251021071703`.
- Cloud Run revision `mcc-ocr-summary-00150-gjt` now serving 100% traffic at `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`.
- `/` endpoint responds 200; service requires authenticated requests (IAM). `/healthz` remains proxied through GFE 404; use root endpoint for liveness.

## End-to-End Process Drive Test
- Invocation: `curl --max-time 360 -H "Authorization: Bearer <token>" "$RUN_URL/process_drive?file_id=1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS"`
- Response: HTTP 200 with payload `{"report_file_id":"1qZRslTJBPKYI-YNotZYMoJcM_8AYQc7e","supervisor_passed":false,"request_id":"aae19c23bc9e41438c4814257896d8e3"}` (supervisor length gate warning only).
- Drive logs confirm upload: `drive_upload_success` with `trace_id=0a892075998a4f143040c52e8dacc8bb`, `drive_file_id=1qZRslTJBPKYI-YNotZYMoJcM_8AYQc7e`, and shared drive `0AFPP3mbSAh_oUk9PVA`.
- DocAI fallback telemetry captured full escalation path: sync → batch → chunked, culminating in `docai_call_success` with 27 chunks for 263 pages.

## Structured Logging & Trace Propagation
- Each phase emits JSON-formatted logs (`drive_download_start/success`, `docai_call_start/failure/success`, `summary_failure/complete`, `pdf_writer_start/success`, `process_complete`).
- Trace IDs from Cloud Logging map to client requests; e.g., `trace_id=0a892075998a4f143040c52e8dacc8bb` spans download, DocAI, OpenAI, PDF and Drive upload events.

## Integration & Regression Tests
- Targeted integration suite executed without coverage enforcement: 
  - `python3 -m pytest tests/test_main_integration.py tests/test_pipeline_endpoints.py tests/test_docai_batch_integration.py tests/test_large_pdf_split_integration.py --maxfail=1 --disable-warnings --no-cov`
  - Result: 9 passed, 0 failed (5.55s).

## Outstanding Items
- Supervisor length gate triggered (`supervisor_passed=false`) for the MedCostContain sample. Consider adjusting dynamic thresholds or providing override for large multi-hundred page inputs.
- `/healthz` path still returns GFE 404; root endpoint currently used for liveness checks.

## Next Steps
- Update documentation (README/TROUBLESHOOTING) with new logging semantics, health check guidance, and IAM-authenticated access instructions.
- Prepare release tag `v1.3.0-stable` once docs are refreshed and CI gating confirmed.
- Kick off Phase 5: fold structured-log events into dashboards/alerts and review supervisor thresholds (`MIN_SUMMARY_DYNAMIC_RATIO`, `MIN_SUMMARY_CHARS`) against large-doc samples.
