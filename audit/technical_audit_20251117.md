# MCC OCR Summary — Technical Audit (2025-11-17)

## 1. Executive Summary
- **Decision:** ✅ **Ready to deploy** – The refactored pipeline, MCC Bible module, and PDF guard are live on Cloud Run revision `mcc-ocr-summary-00363-pmz` running image `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:ops-bible-20251117-104528`.
- **Risk rating:** 🟢 **Low** – All blocking findings from the prior audit have been closed: CI enforces lint/type/coverage gates, Cloud Build deploys with the hardened env set, and validator evidence on the 263-page regression file shows zero intake-form leakage.
- **Highlights:**
  1. Shared MCC Bible constants (`src/services/bible.py`) feed the API, pipeline, formatter, and validator so heading updates propagate everywhere.
  2. `ProcessPipelineService` (src/services/process_pipeline.py) now drives OCR → summary → PDF with supervisor/guard counters and forbidden-phrase enforcement before Drive delivery.
  3. Cloud Build (`cloudbuild.yaml`) pins `SUMMARY_COMPOSE_MODE=refactored`, `PDF_WRITER_MODE=rich`, `PDF_GUARD_ENABLED=true`, and runs the Drive validator step post-deploy; GitHub Actions (`.github/workflows/ci.yml`) runs `ruff`, `mypy --strict`, `pytest --cov=src`, and the local PDF validator on every PR.

## 2. Compliance Overview
### Architecture
- ✅ Canonical MCC Bible module imported by the FastAPI routes, pipeline, formatter, and validator so Provider→Reason→Clinical→Treatment→Diagnoses→Healthcare Providers→Medications order is enforced (`src/api/process.py`, `src/services/process_pipeline.py`, `src/services/summarization/formatter.py`, `scripts/validate_summary.py`).
- ✅ Refactored summariser package plus compatibility shim (`src/services/summariser_refactored.py`, `src/services/summarization/*`) keeps CLI/tests stable while production uses the new controller.

### Reliability
- ✅ OCR helper throttles Document AI splits and falls back to local PyPDF (now `pypdf`) chunking when file size limits are hit (`src/services/docai_helper.py`, `src/services/chunker.py`).
- ✅ PDF guard rejects forbidden phrases before Drive upload and publishes structured logs for every failure path (`src/services/process_pipeline.py`, `src/api/process.py`).
- ✅ Cloud Build deployment step sets deterministic env vars (Cloud Run concurrency 1, CPU 2, no throttling) and immediately validates the deployed revision (`cloudbuild.yaml`).

### Observability
- ✅ Structured logging funnels through `structured_log` with trace IDs, stage, supervisor flags, Run stats, and forbidden phrase hits (`src/services/process_pipeline.py`, `src/utils/logging_utils.py`).
- ✅ Metrics hooks increment `summarisation_failures`, `supervisor_alerts`, `pdf_validation_hits`, etc., and Prometheus exporter is wired when `ENABLE_METRICS=true`.

### Security & Secrets
- ✅ Secrets resolved via Secret Manager references, and `AppConfig` enforces presence of Drive buckets, DocAI processors, and internal tokens outside of local test mode (`src/config.py`, `src/utils/secrets.py`).
- ✅ Cloud Build deploys with the hardened runtime service account `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com`, and GitHub Actions pins reusable actions to immutable SHAs (`cloudbuild.yaml`, `.github/workflows/ci.yml`).

### CI/CD
- ✅ GitHub Actions job `tests` runs `ruff check`, `mypy --strict src`, the local PDF validator, and `pytest --cov=src` with artifacts + `pip-audit` gating PRs; the `trivy` job retains vulnerability scans (`.github/workflows/ci.yml`).
- ✅ Cloud Build compiles Docker, deploys to Cloud Run with the full env bundle, removes stale Google creds, and invokes `scripts/validate_summary.py` against the canonical Drive file before marking the build successful (`cloudbuild.yaml`).

### Documentation & Runbooks
- ✅ README documents dependency/validator workflow, runtime envs, and Cloud Build overrides; audit/HARDENING_LOG.md captures every remediation + evidence trail.
- ✅ Monitoring assets (`infra/monitoring/*.json`, `apply_monitoring.py`) encode the dashboards/alerts with `${ENV}` templating and PagerDuty/email channels.

## 3. Test, Lint, and Deployment Evidence
- `pip-compile requirements.in` / `requirements-dev.in` — regenerated pins after landing the Drive shim + PDF folder updates.
- `pip install -r requirements-dev.txt -c constraints.txt` — ensured the refreshed dev stack (types-python-dateutil, pip-tools 7.5.x) is available locally and in Cloud Build.
- `pytest --cov=src` — 239 tests, coverage 90.23% (gate ≥90% met).
- `ruff check src tests` — clean.
- `mypy --strict src` — clean under the refreshed aliases/Drive config.
- `python scripts/validate_summary.py --pdf-path tests/fixtures/validator_sample.pdf --expected-pages 1` — local validator sanity check.
- `gcloud builds submit --config cloudbuild.yaml --substitutions ... (_TAG=ops-bible-20251117-104528, DocAI/Drive/PDF IDs, `_SERVICE_ACCOUNT_SECRET=mcc_orch_sa_key`) --service-account=projects/quantify-agent/serviceAccounts/mcc-orch-sa@quantify-agent.iam.gserviceaccount.com --gcs-log-dir=gs://quantify-agent_cloudbuild/logs` — Cloud Build + Cloud Run deploy + Drive validator (report `1Gsvu85XxJOVgmE18JvH_4Fh6ilR_fV0u`).
- `curl -H "Authorization: Bearer $(gcloud auth print-identity-token --audiences=$RUN_URL)" "$RUN_URL/process/drive?file_id=1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS&force=true&nonce=$(date -u +%Y%m%d-%H%M%S)"` — manual forced pipeline runs (reports `1_idpB4fSZmLpV6UYqExRIm-QV34-fvQp` and `1EJSdhVLCQzawV_tq1ojpckl_sHWEpdZ4`).
- `python scripts/validate_summary.py --pdf-path outputs/latest-summary.pdf` — downloaded the latest PDF to confirm canonical headings and section counts 1/6/29/10/6/1/3.

## 4. Validator & Runtime Evidence
- **Cloud Build validator:** Post-deploy step processed the 263-page regression Drive file and reported `report_file_id=1Gsvu85XxJOVgmE18JvH_4Fh6ilR_fV0u`, canonical section line counts 1/6/29/10/6/1/3, supervisor_passed=true, pdf_compliant=true.
- **Manual force runs:** `/process/drive?force=true&nonce=<ts>` produced Drive PDFs `1_idpB4fSZmLpV6UYqExRIm-QV34-fvQp`, `1EJSdhVLCQzawV_tq1ojpckl_sHWEpdZ4`, and (latest) `1OlnR_ra2ME0d810tOjlP4BsKSxrkfjfV`; the downloaded PDF validated locally with the same 1/6/29/10/6/1/3 heading counts and zero forbidden phrases.
- **Cloud Logging:** OCR chunk logs and `ocr_failure` counters show any DocAI issue (e.g., invalid arguments) with trace IDs, and the latest deployment logs no forbidden-phrase hits for the regression run.

## 5. Residual Risks / Follow-ups
- 📌 **DocAI quotas:** Continue monitoring Document AI quota/latency dashboards (deployed via `infra/monitoring/apply_monitoring.py`) to ensure the 263-page intake stays within SLA; no additional code work needed today.
- 📌 **Cloud Build IAM:** The project still needs an owner to grant `iam.serviceAccountUser` on `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com` to `720850296638-compute@developer.gserviceaccount.com`; current deployments run Cloud Build with `--service-account=mcc-orch-sa@quantify-agent.iam.gserviceaccount.com` as a workaround.
- 📌 **Validator cadence:** Keep Cloud Build validator secrets (`_VALIDATION_CREDENTIALS_SECRET=mcc_orch_sa_key`) fresh; rotate if the orchestrator SA key rotates to avoid future build failures.

## 6. Scorecard

| Category        | Score | Notes |
| --------------- | ----- | ----- |
| Architecture    | 95%   | Refactored summariser + MCC Bible module in every surface; no outstanding gaps. |
| Reliability     | 93%   | DocAI fallbacks exercised; forced 263-page run returns clean PDF and guard metrics. |
| Observability   | 92%   | Structured logs + Prometheus exporter wired; dashboards deployed via `infra/monitoring`. |
| Security        | 91%   | Secrets via Secret Manager + CMEK buckets; Cloud Build runs as orchestrator SA (IAM follow-up noted). |
| CI/CD           | 96%   | GitHub Actions + Cloud Build enforce pytest/ruff/mypy/validator and post-deploy Drive validation. |
| Documentation   | 94%   | README + HARDENING_LOG updated with deployment steps and evidence; runbooks cover validator + monitoring. |
