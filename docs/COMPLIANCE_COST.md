# Compliance & Cost Guide

## 1. Regulatory & BAA Considerations

| Topic | Guidance |
|-------|----------|
| **Business Associate Agreement (BAA)** | Use a Google Cloud project covered by the MCC BAA. Enable GCP Organization Policies to restrict deployment targets to BAA-approved regions. |
| **HIPAA logging** | All logs flow through the PHI redaction filter (`utils/logging_filter.PHIRedactFilter`). Avoid adding `logger.info(raw_payload)` entries elsewhere. |
| **Data residency** | Keep DocAI, Pub/Sub, Cloud Run, and BigQuery resources in the same HIPAA-approved region (default `us-central1`). Cloud Run resource settings are defined in `pipeline.yaml`. |
| **Access control** | Service accounts are least-privilege (see `infra/iam.sh`). Limit human access via Google Groups with `logWriter` or `monitoring.viewer` roles. |
| **Retention** | State + output buckets should enforce lifecycle rules: intake/object outputs delete after 30 days (see `pipeline.yaml` bucket config). BigQuery dataset retention is governed by MCC data policies; use table partitioning for fine-grained control. |
| **PII exports** | Avoid exporting summary tables outside GCP. Use CMEK-protected GCS for PDF outputs and enable VPC-SC if cross-project boundaries exist. |

## 2. Data Handling Checklist

1. **Input**: Drive intake -> GCS `INTAKE_BUCKET` (CMEK, 30-day retention).
2. **Processing**: DocAI (Document AI processor). Summaries + PDF generation use CMEK-protected buckets and `PIPELINE_STATE_BUCKET`.
3. **Output**: Summary JSON + PDF uploaded to `SUMMARY_BUCKET` and optionally back to Drive.
4. **State**: `PIPELINE_STATE_BUCKET` tracks progress; entries older than policy can be purged via `gcloud storage rm` with lifecycle rules.

## 3. Cost Drivers

| Area | Knobs | Notes |
|------|-------|-------|
| **DocAI OCR** | `MAX_SHARD_CONCURRENCY` (pipeline.yaml), shard size (DocAI splitter max pages) | Higher concurrency increases parallel LRO cost but reduces latency; tune to stay within DocAI quota. |
| **Summarisation** | `target_chars`, `max_chars`, `overlap` (see `src/services/summariser_refactored.py`) | Smaller chunks → more OpenAI calls; larger chunks → longer prompts. Benchmarked default (6.5k/8.5k/0.9k) balances latency vs. token count. |
| **OpenAI model** | `OPENAI_MODEL` env (`AppConfig.openai_model`) | GPT‑4o-mini used by default; swap to `gpt-4.1` (higher cost) or `gpt-4o-mini` (cheaper) depending on accuracy/perf requirements. |
| **Cloud Run** | `minInstances`, `maxInstances`, CPU/memory (pipeline.yaml) | Setting `minInstances=1` for the ingest API avoids cold starts; adjust `maxInstances` (default 12) based on throughput. |
| **Drive storage** | Retention in shared drive folder; lifecycle rules | Periodically purge archived PDFs or move them to low-cost storage if retention requirements allow. |

## 4. Operational Tips

- Use `bench/run_bench.py` to measure summariser latency before changing chunk sizes or concurrency.
- Monitor runtime metrics (`chunks_total`, `needs_review_total`, etc.) to detect runaway retries or guardrail-triggered reviews.
- Capture compliance evidence (IAM bindings, audit logs, retention policies) in change tickets when modifying infrastructure.
- For incident response, export Cloud Logging entries filtered by `resource.type="cloud_run_revision" AND labels.job_id=...` to trace PHI-handled requests.

## 5. References

- `infra/iam.sh` – service account bootstrap script (least privilege).
- `pipeline.yaml` – Cloud Run scaling, bucket retention, IAM mappings.
- `docs/QUICK_START.md` – onboarding + stubbed-local testing instructions.
- `docs/audit/HARDENING_LOG.md` – remediation evidence and metrics snapshots.
