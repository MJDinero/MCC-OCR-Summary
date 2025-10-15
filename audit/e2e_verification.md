# E2E Verification Checklist

1. Export env vars:
   ```bash
   export PROJECT_ID=prod-project
   export REGION=us-central1
   export INTAKE_BUCKET=mcc-ocr-intake
   export SERVICE_URL=https://mcc-ocr-summary-${REGION}-a.run.app
   ```
2. Run smoke script with fixture PDF:
   ```bash
   bash scripts/e2e_smoke.sh
   ```
   - Expect `/status/{job}` to reach `UPLOADED` within 15 min.
   - Capture emitted signed URL from script output.
3. Validate signed URL responds 200:
   ```bash
   curl -I "${SIGNED_URL}"
   ```
4. Query Cloud Logging for new markers (after patches):
   - `split_done` with matching `job_id`, `trace_id`, `document_id`.
   - `ocr_lro_finished` per shard (`shard_id`) showing `duration_ms`.
   - `summary_done` including `schema_version`.
5. Inspect state store (GCS) to confirm summary JSON + PDF objects created once (generation 1).
6. Confirm Pub/Sub DLQ topic empty: `gcloud pubsub subscriptions pull mcc-ocr-pipeline-dlq-sub --limit=1` returns no messages.
7. Document execution trace in ticket; attach `logs/latest_cloudrun*.txt` snippets if anomalies.
