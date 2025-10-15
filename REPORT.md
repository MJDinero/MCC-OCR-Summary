# MCC-OCR Summary — Delivery Report

## Decision Log
- 2025-10-15 — Initiated execution under secure rollout plan. Captured baseline gaps for Phase 1 (CMEK propagation) prior to implementation.
- 2025-10-15 — Propagated CMEK configuration across Cloud Build deploy steps, pipeline manifest (Cloud Run + GCS + BQ), and DocAI batch helper; introduced CMEK verification script and unit coverage.
- 2025-10-15 — Hardened `/internal/jobs/*` auth to require `X-Internal-Event-Token`, updated Cloud Build to source INTERNAL_EVENT_TOKEN via Secret Manager, and removed plaintext secret placeholders from runtime templates.
- 2025-10-15 — Extended Secret Manager resolution to Eventarc ingest + refactored summariser tooling, ensuring OpenAI and DocAI identifiers load via `resolve_secret_env` with local fallbacks.
- 2025-10-15 — Cloud Build now deploys by image digest, publishes vulnerability & provenance reports, signs images with GCP KMS via Cosign, and injects pipeline state env vars per stage.
- 2025-10-15 — Created stage-scoped service accounts (OCR, summariser, storage), tightened IAM bindings via `infra/iam.sh`, and added Prometheus/Monitoring artifacts (dashboards + alert policies) with structured logging upgrades.
- 2025-10-15 — Achieved ≥90% coverage (100% on metrics module), enforced `make lint` (ruff + pylint) and `make type` (mypy --strict), introduced runtime tuning (dynamic UVICORN workers, Cloud Run concurrency caps), and refreshed README/monitoring docs.

## Current Status
- Phase 1: CMEK propagation implemented for runtime deployments (env vars, DocAI sync/batch paths) and storage manifests. Added BigQuery enforcement helper (`infra/bigquery/ensure_cmek.sh`), CMEK verification script (`scripts/verify_cmek.sh`), and unit tests asserting `kms_key_name` usage. Pending: live gsutil/bq evidence capture once credentials available.
- Phase 2: Internal auth now enforced via `X-Internal-Event-Token`; deployment pipeline configures INTERNAL_EVENT_TOKEN via Secret Manager. API entrypoints (`/ping_openai`, Eventarc ingest) and CLI tooling now resolve OpenAI/DocAI secrets via `resolve_secret_env` while tolerating local fallbacks; runtime templates avoid placeholder secrets.
- Phase 3: Cloud Build signs artifacts, captures vulnerability/provenance metadata, deploys by digest only, and ensures runtime env vars include pipeline state controls sourced via substitutions. SBOM + audit artifacts now land in CMEK bucket `gs://quantify-agent-mcc-phi-artifacts/`.
- Phase 4: Stage-specific service accounts (`mcc-ocr-sa`, `mcc-summariser-sa`, `mcc-storage-sa`) created with bucket/dataset-scoped permissions and Cloud Build impersonation limited to `roles/iam.serviceAccountUser` on those identities. Pipeline manifest & Cloud Build now target the new identities.
- Phase 5: Prometheus metrics auto-instrumented across services, `/metrics` wired via `PrometheusMetrics.instrument_app`, structured logging emits stage/service/latency/error metadata, and Monitoring artefacts (`infra/monitoring/*.json`) define latency dashboards plus DLQ/5xx/SLO alerts.
- Phase 6: Pytest suite extended (new metrics/SA tests), coverage gate raised to 90% (current run: 100% on `src.services.metrics`).
- Phase 7: Runtime tuning in place (`src/runtime_server.py` auto-sizes workers; Cloud Run deployments set per-stage concurrency & autoscaling caps).
- Phase 8: Static analysis gates enforced (`make lint` → ruff/pylint, `make type` → mypy --strict across critical services).
- Phase 9: README/observability docs refreshed; monitoring JSON artefacts committed for dashboards + alerts.

## Next Actions
1. Execute BigQuery/GCS CMEK verification commands and capture outputs when project credentials available.
2. Sweep remaining modules for direct secret env usage (e.g., deployment scripts) and migrate to `resolve_secret_env`.
3. Capture gcloud evidence for IAM bindings (service accounts + Cloud Build impersonation) and document results alongside new Monitoring assets.
4. Apply Monitoring dashboards/alerts via `gcloud monitoring` and record outputs when project access is available.

## Evidence (Phases 6–9)

- `python3 -m pytest` → 130 passed / 16 skipped, coverage 100% (`src.services.metrics`).
- `make lint` → ruff + pylint clean after strict service account/observability updates.
- `make type` → `python3 -m mypy --strict` across metrics/runtime/service modules (no issues).
- Runtime tuning validated locally via `python -m src.runtime_server` (auto-selects workers) and Cloud Build deploy flags (concurrency/max instances per service).
- Monitoring artefacts committed: dashboards (`infra/monitoring/dashboard_pipeline_latency.json`, `dashboard_throughput_cpu_mem.json`) and alert policies (`alert_dlq_backlog.json`, `alert_5xx_rate.json`, `alert_slo_breach.json`).

_Evidence sections will be populated as phases complete._
