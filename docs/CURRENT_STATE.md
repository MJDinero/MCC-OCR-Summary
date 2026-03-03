# docs/CURRENT_STATE.md — Verified Current State Register

Last updated: 2026-03-03 10:26:53 PST
Updated by: Codex (thread: config-align-live-runtime)
Repo branch: `codex/feat/config-align-live-runtime`
Repo commit (branch baseline): `b1a020c3c99f4fb4879c11735ddc029ba8305bcc`
Task id: `config-align-live-runtime`
Target GCP project: `quantify-agent` (approved canonical target)
Target region: `us-central1` (approved canonical target)
Cloud audit status: `DONE (read-only commands only; no cloud writes)`

## Phase Queue Status (this pass)
- Phase 0: `DONE` (repo + live runtime re-verified with read-only commands)
- Phase 1: `DONE` (live-vs-repo alignment matrix built and classified)
- Phase 2: `DONE` (repo-controlled deploy/docs aligned to approved live runtime)
- Phase 3: `DONE` (`pipeline.yaml` explicitly marked legacy/non-authoritative)
- Phase 4: `DONE` (`reportlab` direct dependency declaration fixed; vulnerabilities inventoried/prioritized)
- Phase 5: `DONE` (full required validation commands passed)
- Phase 6: `DONE` (strict self-review complete; scope stayed config/docs/metadata-focused)
- Phase 7: `BLOCKED` (awaiting post-update commit/push/PR lifecycle)

## Live Runtime Values Re-Verified (2026-03-03)
- Project/region: `quantify-agent` / `us-central1`
- Cloud Run service: `mcc-ocr-summary`
- Cloud Run URLs:
  - `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
  - `https://mcc-ocr-summary-720850296638.us-central1.run.app`
- Service account: `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com`
- Ingress: `all`
- Auth posture from IAM policy: `roles/run.invoker` bound only to service accounts; no `allUsers`/`allAuthenticatedUsers` binding observed.
- Image/tag: `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary:ops-final-20251117-1613`
- Runtime controls:
  - `containerConcurrency=1`
  - `timeoutSeconds=3600`
  - `autoscaling.knative.dev/maxScale=1`
- OCR processor IDs:
  - `DOC_AI_PROCESSOR_ID=21c8becfabc49de6`
  - `DOC_AI_OCR_PROCESSOR_ID=21c8becfabc49de6`
- Drive IDs:
  - `DRIVE_INPUT_FOLDER_ID=1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`
  - `DRIVE_REPORT_FOLDER_ID=130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`
- Metrics flag: `ENABLE_METRICS=true`
- Runtime buckets:
  - `INTAKE_GCS_BUCKET=mcc-intake`
  - `OUTPUT_GCS_BUCKET=mcc-output`
  - `SUMMARY_BUCKET=mcc-output`

## Alignment Matrix (Live Truth vs Repo)

| Surface | Live Runtime (2026-03-03) | Repo Before This Pass | Classification | Outcome |
| --- | --- | --- | --- | --- |
| Project / region | `quantify-agent` / `us-central1` | same in `cloudbuild.yaml` | `safe to align automatically` | no change needed |
| Cloud Run URLs | both URLs above | mostly tracked in docs, not deploy-pinned | `documentation clarification only` | refreshed in this file |
| Service account | `mcc-orch-sa@...` | not explicitly pinned in `cloudbuild.yaml` | `safe to align automatically` | pinned with `_SERVICE_ACCOUNT` + deploy arg |
| Ingress + IAM posture | ingress `all`; invoker restricted to service accounts | ingress/auth not explicit in deploy config | `security-sensitive` | ingress pinned in `cloudbuild.yaml`; IAM remains HUMAN MUST RUN |
| Image/tag | `ops-final-20251117-1613` | `_TAG=v11mvp` | `safe to align automatically` | `_TAG` default aligned to verified live tag |
| OCR processor ID | `21c8becfabc49de6` | already matched | `safe to align automatically` | no functional change |
| Drive input/report IDs | `1eyMO...` / `130jJ...` | stale values in `cloudbuild.yaml` + `README.md` | `safe to align automatically` | updated config/docs to live IDs |
| Metrics flag | `true` | `ENABLE_METRICS=false` in `cloudbuild.yaml` | `safe to align automatically` | set `ENABLE_METRICS=true` |
| Buckets | `mcc-intake`, `mcc-output`, `mcc-output` | already matched | `safe to align automatically` | no functional change |
| Concurrency / timeout / maxScale | `1` / `3600` / `1` | not pinned in `cloudbuild.yaml` | `safe to align automatically` | deploy flags pinned (`--concurrency`, `--timeout`, `--max-instances`) |
| `pipeline.yaml` status | live runtime does not use its spec | ambiguous legacy manifest with stale runtime values | `documentation clarification only` | marked as `legacy-reference-only`; tests enforce explicit status |

## `pipeline.yaml` Decision
- Decision: `B` (not authoritative deployment path).
- Reason:
  - Root policy treats `cloudbuild.yaml` as deploy truth unless superseded.
  - `pipeline.yaml` is referenced by tests and still useful as a legacy reference.
  - Keeping it without a status marker was misleading.
- Action taken:
  - Added file-level legacy notice and metadata annotations:
    - `mcc.dev/manifest-status=legacy-reference-only`
    - `mcc.dev/authoritative-deploy=cloudbuild.yaml`
  - Added test guard in `tests/test_infra_manifest.py` to keep this status explicit.

## Dependency Hygiene + Vulnerability Inventory
- `deptry` result: `DONE` (tool runs; many mixed `DEP002` findings intentionally not mass-pruned here).
- High-confidence metadata issue: `reportlab` imported directly in `src/services/pdf_writer.py` and previously undeclared.
  - Action: added `reportlab==4.2.0` to `requirements.txt`.

### pip-audit Prioritization (`.venv/bin/pip-audit --local`)
- Result: `Found 25 known vulnerabilities in 10 packages`.
- Runtime-critical candidates (prioritize first):
  - `python-multipart` (`GHSA-wp53-j4wj-2cfg`, fix `0.0.22`)
  - `pypdf` (multiple GHSA IDs, fix chain up to `6.7.4`)
  - `protobuf` (`GHSA-7gcm-g887-7qv7`, fix `5.29.6` / `6.33.5`; constrained by current `<5` policy)
  - `urllib3` (three GHSA IDs, fixes up to `2.6.3`)
  - `orjson` (`GHSA-hx9q-6w63-j58v`, no fix version listed)
- Likely tooling or non-runtime-first:
  - `pip`, `wheel`, `filelock`
- Mixed/depends-on-runtime-path usage:
  - `pillow` (used by PDF/image paths in some stacks; validate usage before remediation)
  - `pyasn1` (mostly auth/crypto stack dependency)

## Files Changed This Pass
- `cloudbuild.yaml`
- `README.md`
- `pipeline.yaml`
- `tests/test_infra_manifest.py`
- `requirements.txt`
- `PLANS.md` (current pass evidence entry)
- `docs/CURRENT_STATE.md` (this file)

## Validation Evidence
- Passed: `.venv/bin/python -m ruff check src tests`
- Passed: `.venv/bin/python -m mypy --strict src`
- Passed: `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - `192 passed, 6 skipped`
  - coverage summary: `Total coverage: 94.91%`
- Passed: `git diff --check`
- Supplemental config checks:
  - `yaml.safe_load('cloudbuild.yaml')`
  - `yaml.safe_load('pipeline.yaml')`

## Risks / Unknowns / Rollback
- Remaining risks:
  - Ingress remains `all` by approved runtime policy; although IAM invoker is currently restricted, this is security-sensitive and should stay under explicit review.
  - `pip-audit` findings are recorded but not remediated in this scoped pass.
  - `protobuf` vulnerability remediation likely conflicts with current `<5` compatibility constraint and needs a separate compatibility task.
- Rollback:
  - Revert this pass commit to restore prior repo config/doc state.
- No cloud writes performed.

## Evidence Log (Commands Run This Pass)
- Branch/bootstrap:
  - `git fetch origin`
  - `git checkout main`
  - `git merge --ff-only origin/main`
  - `git checkout -b codex/feat/config-align-live-runtime`
- Read-first docs:
  - `AGENTS.md`
  - `PLANS.md`
  - `docs/CURRENT_STATE.md`
  - `docs/REFACTOR_RUNBOOK.md`
  - `docs/ARCHITECTURE.md`
  - `docs/CODEBASE_MAP.md`
  - `docs/TESTING.md`
  - `docs/GCP_REFACTOR_PLAN.md`
- Phase 0 verification:
  - `git status --short --branch`
  - `gcloud auth list --format='table(account,status)'`
  - `gcloud config list --format='text(core.project,core.account,compute.region,run.region,workflows.location)'`
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format='yaml(metadata.name,status.url,spec.template.spec.serviceAccountName,spec.template.metadata.annotations,spec.template.spec.containers,metadata.annotations)'`
  - `gcloud run services get-iam-policy mcc-ocr-summary --region us-central1 --project quantify-agent --format='json'`
  - `gcloud run services describe mcc-ocr-summary --region us-central1 --project quantify-agent --format='yaml(metadata.annotations,spec.template.metadata.annotations,spec.template.spec.containerConcurrency,spec.template.spec.timeoutSeconds,spec.template.spec.serviceAccountName,status.url)'`
- Dependency audit:
  - `.venv/bin/python -m deptry .`
  - `.venv/bin/pip-audit --local`
- Validation:
  - `.venv/bin/python -m ruff check src tests`
  - `.venv/bin/python -m mypy --strict src`
  - `.venv/bin/python -m pytest --cov=src --cov-branch --cov-report=term-missing`
  - `git diff --check`
