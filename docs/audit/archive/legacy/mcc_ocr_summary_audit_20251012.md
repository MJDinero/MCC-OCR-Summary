# MCC OCR Summary Audit — 2025-10-12

## Exec Summary
- **Decision:** No-Go until idempotency + observability gaps are remediated and CI gating restored.
- **Readiness:** Nearly-Ready once High findings below are closed.
- **Top Risks:**
  | Risk | Sev | Evidence | Patch | Owner |
  | --- | --- | --- | --- | --- |
  | Missing split_done/ocr_lro/summary_done markers; weak JSON schema guard | High | src/services/docai_helper.py:473 / src/services/summariser_refactored.py:62 | observability_logs.patch + structured_outputs.patch | owner:platform |
  | PDF job lacks ifGenerationMatch guard; CLI default allows duplicate writes | High | workflows/pipeline.yaml:289 / src/services/pdf_writer_refactored.py:305 | reliability_idempotency.patch | owner:platform |
  | Summariser/PDF SAs hold storage.objectAdmin (beyond least-privilege) | Medium | infra/iam.sh:101 | iam_least_priv.patch | owner:platform |
  | CI skips lint/type + coverage gate; pytest gate commented out | Medium | cloudbuild.yaml:12 / pytest.ini:2 | ci_canary_rollback.patch (+ restore pytest.ini addopts) | owner:platform |
  | Env validation omits bucket vars; defaults mask prod misconfig | Medium | src/config.py:73 | env_validation.patch | owner:platform |

## Compliance Overview
### Architecture
- **Workflow fan-out via Cloud Workflows & Cloud Run Jobs** — Pass (Low); evidence workflows/pipeline.yaml:234.
- **Hash-aware dedupe key persisted with job metadata** — Pass (Low); evidence src/services/pipeline.py:202.
- **Legacy synchronous /process_drive removed** — Pass (Low); evidence src/main.py:156.
- **Root deployment manifest (`pipeline.yaml`) missing** — Fail (Medium); GAP create root deployment spec.

### Reliability
- **PDF upload idempotency (`ifGenerationMatch` everywhere)** — Fail (High); evidence workflows/pipeline.yaml:289.
- **State store writes guarded by ifGenerationMatch** — Pass (Low); evidence src/services/pipeline.py:405.
- **DLQ publish on workflow failure** — Pass (Low); evidence workflows/pipeline.yaml:358.

### State
- **Job schema exposes hash, history, retries, signed URL** — Pass (Low); evidence src/services/pipeline.py:708.
- **/status exposes UPLOADED with signed URL** — Pass (Low); evidence src/services/pdf_writer_refactored.py:334.
- **Structured job metadata manifest missing version pin** — Caveats (Medium); evidence src/services/summariser_refactored.py:760.

### Security
- **Least-privilege service accounts** — Fail (Medium); evidence infra/iam.sh:101.
- **Internal token required for job events** — Pass (Low); evidence src/main.py:272.
- **Secrets via Secret Manager paths in runtime env** — Pass (Low); evidence infra/runtime.env.sample:8.

### Observability
- **Ingress marker with trace metadata** — Pass (Low); evidence src/main.py:174.
- **Splitter/OCR/Summary completion markers with duration** — Fail (High); evidence src/services/docai_helper.py:473 / src/services/summariser_refactored.py:760.
- **Error logs include stage + trace ids** — Pass (Low); evidence src/main.py:240.

### Runtime
- **Cloud Run Gen2, concurrency<=2, no throttling** — Pass (Low); evidence cloudbuild.yaml:61-75.
- **Job timeouts bounded <=900s** — Pass (Low); evidence cloudbuild.yaml:109-132.
- **Fan-out concurrency capped (<=12)** — Pass (Low); evidence workflows/pipeline.yaml:153.

### CI/CD
- **Lint + type checks executed pre-deploy** — Fail (Medium); evidence cloudbuild.yaml:12.
- **Coverage gate >=85% enforced** — Fail (Medium); evidence pytest.ini:2.
- **Smoke test exercises signed URL** — Pass (Low); evidence scripts/e2e_smoke.sh:69.

## Test Execution (local)
- `ruff check .` → `command not found`.
- `mypy .` → `command not found`.
- `pytest -q -m "not integration" --maxfail=1 --disable-warnings` → `command not found`.
- `RUN_INTEGRATION=1 pytest -q -m "integration" --maxfail=1` → `command not found`.
- `bash scripts/e2e_smoke.sh` not attempted (requires gcloud + creds).

## Gap Matrix
| Item | Evidence | Severity | Fix | Effort | Owner |
| --- | --- | --- | --- | --- | --- |
| Observability markers missing for splitter/OCR/summary | src/services/docai_helper.py:473 | High | observability_logs.patch | S | owner:platform |
| OpenAI output lacks strict schema & version pin | src/services/summariser_refactored.py:117 | High | structured_outputs.patch | S | owner:platform |
| PDF upload without ifGenerationMatch guard | workflows/pipeline.yaml:289 | High | reliability_idempotency.patch | S | owner:platform |
| PDF CLI default omits ifGenerationMatch | src/services/pdf_writer_refactored.py:248 | High | reliability_idempotency.patch | S | owner:platform |
| IAM grants storage.objectAdmin to jobs | infra/iam.sh:101 | Medium | iam_least_priv.patch | S | owner:platform |
| CI pipeline missing lint/type + coverage gate | cloudbuild.yaml:12 | Medium | ci_canary_rollback.patch | S | owner:platform |
| Coverage threshold commented out | pytest.ini:2 | Medium | Reinstate addopts + ci_canary_rollback.patch | S | owner:platform |
| Env validation skips bucket vars | src/config.py:73 | Medium | env_validation.patch | S | owner:platform |
| Root pipeline.yaml absent | GAP | Medium | Add root deployment manifest | M | owner:platform |

## Remediation Plan
1. Apply `observability_logs.patch` to emit split_done/ocr_lro/summary_done markers with duration + schema fields. Acceptance: logs show markers in unit tests + e2e smoke.
2. Apply `structured_outputs.patch` to enforce OpenAI JSON schema and schema_version checks. Acceptance: unit tests cover schema mismatch.
3. Apply `reliability_idempotency.patch` to add `--if-generation-match 0` to jobs and default CLI guard; rerun retries to confirm PreconditionFailed on duplicate. Acceptance: new idempotency test covers rerun scenario.
4. Apply `iam_least_priv.patch` to replace storage.objectAdmin with viewer+creator; ensure workflow deployment succeeds. Acceptance: terraform/apply script runs without extra grants.
5. Apply `ci_canary_rollback.patch` and restore pytest coverage gate to 85%; ensure Cloud Build fails when coverage <85%. Acceptance: forced low coverage run fails.
6. Apply `env_validation.patch` to fail-fast when bucket vars unset; add config unit test verifying error message. Acceptance: startup without bucket env raises RuntimeError listing vars.
7. Add root `pipeline.yaml` mirroring workflow/service deployment for operator reference. Acceptance: repo includes validated manifest.

## Verification Steps
1. `ruff check .` → expect clean exit (0).
2. `mypy .` → expect success with no new errors.
3. `pytest -q -m "not integration" --maxfail=1 --disable-warnings --cov=src --cov-fail-under=85` → expect pass.
4. `RUN_INTEGRATION=1 pytest -q -m "integration" --maxfail=1 --disable-warnings` → expect pass.
5. `bash scripts/e2e_smoke.sh` with valid PROJECT_ID/REGION creds → expect `/status` reaches `UPLOADED` and signed URL HEAD=200.
6. Review Cloud Logging for `split_done`, `ocr_lro_finished`, `summary_done` entries containing `job_id`, `trace_id`, `document_id`, `schema_version`.
7. Confirm GCS upload retry returns HTTP 412 when rerun without clearing object generation.

## Notes
- Sample monitoring policies ship in `infra/monitoring/alert_policies.yaml`; integrate with SLO dashboards alongside markers.
- Ensure missing root `pipeline.yaml` is created to document infra orchestration.
