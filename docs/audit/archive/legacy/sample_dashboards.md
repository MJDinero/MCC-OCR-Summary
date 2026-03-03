# Sample Dashboards & Metrics

## Log-Based Metrics
- **Ingest Success Rate**: filter `logName=...` and `jsonPayload.marker="ingest_received"`.
- **Splitter Duration**: filter `jsonPayload.marker="split_done"` and chart `jsonPayload.duration_ms` p95.
- **OCR Fan-out Latency**: filter `jsonPayload.marker="ocr_lro_finished"`, group by `jsonPayload.shard_id`.
- **Summary Generation Duration**: filter `jsonPayload.marker="summary_done"`, chart p95 by `jsonPayload.schema_version`.
- **Error Surface**: filter `jsonPayload.error` across `pipeline_status_transition` for `status="FAILED"`.

## Cloud Monitoring Widgets
- **SLO Burn Rate**: import alert in infra/monitoring/alert_policies.yaml:1 (request 5xx burn rate >2x).
- **DLQ Depth**: display metric `pubsub.googleapis.com/subscription/num_undelivered_messages` scoped to DLQ.
- **OCR Stall Counter**: chart log metric tied to `labels.status="OCR_SCHEDULED"` stagnation (see alert_policies.yaml:49).
- **Signed URL Success**: custom metric counting 200 responses from smoke test `curl -I <signed_url>`.

## Dashboard Assembly Steps
1. Create log metrics for `split_done`, `ocr_lro_finished`, `summary_done` once patches land.
2. Add time-series widgets for p95 duration and error-rate with 14-day window.
3. Add table widget for top `job_id` in DLQ using log-based metric from DLQ publish step.
4. Pin Pub/Sub DLQ alert and burn-rate alert to dashboard for quick status view.
5. Document runbooks inline linking to `scripts/e2e_smoke.sh` for manual recovery.
