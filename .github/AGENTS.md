# AGENTS.md — MCC‑OCR‑Summary Autonomous Agent (gpt‑5‑codex · VS Code) · **Bootstrap Edition v2**

**Purpose**: Run end‑to‑end production hardening for `MCC‑OCR‑Summary` **without pausing for questions**. Operate in a **self‑healing loop** (plan → patch → test → build → deploy → run → validate → iterate) until the pipeline is **green** and evidence is committed.

---

## Capability & Credential Contract (Non‑stop)

* You are running **inside VS Code** with an integrated shell. You **can** execute terminal commands.
* **Assume** runtime credentials (GCP, Drive, GitHub, OpenAI) exist **unless a command proves otherwise**.
* **Verify by executing**, not assuming:

  1. `gcloud auth application-default print-access-token`
  2. `gh auth status`
  3. `gcloud run services describe mcc-ocr-summary --region us-central1 --format='value(status.latestReadyRevisionName)'`
* You are **forbidden** from claiming lack of access without running those checks first.
* If a check fails, **print one** remedial command (e.g., `gcloud auth application-default login …` or `gh auth login …`) and **continue** with phases that do not require that credential. Re‑attempt on the **next loop**. **Never stop** to ask questions.

---

## Git Sync & Squash Policy (Run before every phase)

1. `git config --global pull.rebase true`
2. `git fetch --all --prune && git remote -v`
3. Detect base branch:

   ```bash
   git show-ref --verify --quiet refs/remotes/origin/ops/finalize-prometheus-drive-docai \
     && BASE=ops/finalize-prometheus-drive-docai || BASE=main
   ```
4. `git checkout "$BASE" && git pull --rebase origin "$BASE"`
5. Auto‑merge eligible PRs into `$BASE` (non‑draft, CI green):

   ```bash
   gh pr list --state open --base "$BASE" --json number | jq -r '.[].number' \
     | xargs -I{} gh pr merge {} --squash --auto || true
   ```
6. Rebase all local branches on the updated base:

   ```bash
   for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do
     git checkout "$b" && git rebase "origin/$BASE" || true
   done
   ```
7. Verify sync:

   ```bash
   git checkout "$BASE" && git rev-parse HEAD && git rev-parse origin/"$BASE"
   ```

---

## 1) Agent Role & Non‑Stop Policy

* **Autonomy**: Do **not** ask the operator questions. Do **not** halt for confirmations. Make conservative, reversible changes and continue.
* **Idempotence**: Every step must be safe to re‑run. Use **minimal diffs**; snapshot before risky changes.
* **Evidence‑first**: Append structured proof (run JSON, validator JSON, revision, commit, metrics note) to `docs/audit/HARDENING_LOG.md` when green.
* **Security**: No PHI/PII in logs. Never echo secrets. Keep `/metrics` private; rely on **GMP sidecar**.

---

## 2) Success Definition (Acceptance Criteria)

1. **Format/List contract**: Only 4 canonical headers; single list block; filters & cross‑section de‑dup. Tests pass.
2. **OpenAI compat**: Responses path **without** `response_format`; Chat JSON fallback works; tests pass.
3. **Build & Deploy**: Cloud Build + Cloud Run deploy succeed; `latestReadyRevisionName` recorded.
4. **Final Verification**: Real Drive file processed; validator prints `{ ok:true, sections_ok:true, noise_found:false, length ≥ 500 }`.
5. **PR Opened**: Checklist + evidence links; squash‑merged if protections allow.

---

## 3) Environment & Constants

* **GCP Project**: `quantify-agent`
* **Region**: `us-central1`
* **Cloud Run Service**: `mcc-ocr-summary`
* **Run URL** (private): `https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app`
* **Artifact Registry**: `us-central1-docker.pkg.dev/quantify-agent/mcc/mcc-ocr-summary`
* **Drive Intake Folder ID**: `1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`
* **Drive Output Folder ID**: `130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`
* **Real Test File (263p) ID**: `1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS`
* **DocAI OCR toggles**: `enableImageQualityScores=true`, `advancedOcrOptions=["legacy_layout"]`, location=`us`
* **OpenAI flags**: `openai_model`, `openai_use_responses`, `openai_json_mode`
* **Branches**: working `ops/openai-compat-hotfix`; base `ops/finalize-prometheus-drive-docai`; safety `ops/safety-<UTCts>`

---

## 4) Pre‑Deploy Gates (Must pass locally **before** any deploy)

* **Test Health Gate**

  * Run: `pytest -q tests/test_format_contract.py tests/test_openai_backend.py tests/test_lists_contract.py`
  * If `ModuleNotFoundError: src.utils.pipeline_failures`, create a minimal `src/utils/pipeline_failures.py` stub (matching referenced symbols) and re‑run.
* **Container Startup Gate**

  * Build: `docker build -t mcc-local:preflight .`
  * Run: `docker run --rm -e PORT=8080 -p 8080:8080 mcc-local:preflight &`
  * Probe: `curl -sf http://127.0.0.1:8080/healthz` must return **200**. If not, fix entrypoint and ensure binding to `0.0.0.0:$PORT` (e.g., uvicorn/gunicorn or `start.py`).
* **DocAI Preflight (location=us)**

  * Call `…/v1/projects/{PROJECT}/locations/us/processors/{PROC}:process` with a tiny 1‑page PDF and:

    ```json
    {"processOptions":{"ocrConfig":{"enableImageQualityScores":true,"advancedOcrOptions":["legacy_layout"]}}}
    ```
  * If fails: ensure `roles/documentai.apiUser` on the Cloud Run service account, correct processor/location env, and Drive quota headers elsewhere.

---

## 5) Self‑Healing Loop (Phases)

**Loop until all acceptance criteria are met; run *Git Sync & Squash* before every phase.**

**PHASE 0 — Prechecks**
Print env; create `./remediation/{logs,reports,patches}`.

**PHASE 1 — Test Health (BLOCKER)**
Make tests green (see *Pre‑Deploy Gates*). Store logs under `./remediation/logs/pytest.txt`.

**PHASE 2 — Container Startup (BLOCKER)**
Prove `/healthz` locally. If failing, patch entrypoint/bind or imports; keep diffs under `./remediation/patches/`.

**PHASE 3 — DocAI Preflight (BLOCKER)**
Smoke `:process` (us). If failing, fix IAM/env; retry until 200.

**PHASE 4 — CI/CD & Scans (NON‑BLOCKING but required before final)**
Workflows: lint/type/test/build/security/gates; actions pinned by SHA; trivy gate on main/tags; pip‑audit report‑only on PR. Open PRs; **--auto --squash**.

**PHASE 5 — Build & Deploy**
Cloud Build → Cloud Run with `timeout=3600`, `concurrency=1`, `--no-cpu-throttling`. Ensure latest revision is **Ready**.

**PHASE 6 — Final Verification**
Call `/process/drive?file_id=<263p>` with ID token; download report via Drive (quota + resourceKeys header as needed); `pdftotext -eol unix -nopgbrk -enc UTF-8 -nodiag`; run validator; require `{ok:true, sections_ok:true, noise_found:false, length≥500}`.

**PHASE 7 — Evidence & PR**
Append compact `run.json`, `validator.json`, revision & metrics note to `docs/audit/HARDENING_LOG.md`; push topic branch and open PR; **squash & auto‑merge**.

---

## 6) Implementation Notes & Guardrails

* **Drive API**: Always set `X-Goog-User-Project: quantify-agent` and `supportsAllDrives=true`. If metadata returns `resourceKey`, add `X-Goog-Drive-Resource-Keys: <fileId>/<resourceKey>[,…]`.
* **Cloud Run Auth**: Service is **private**. Use an ID token with `--audiences=<RUN_URL>`.
* **OpenAI**: Responses → **no** `response_format` (use `output_text`); Chat → `response_format={"type":"json_object"}` or JSON schema.
* **DocAI**: Use `us-documentai.googleapis.com`; include OCR toggles; prefer online `process` for deterministic validation.
* **Metrics**: GMP sidecar scrapes `:8080/metrics` locally; do **not** expose `/metrics` publicly.
* **Logs/Evidence**: No PHI/PII. Commit structured metadata only.

---

## 7) Failure Matrix → Auto‑Fixes

* **Container fails on PORT=8080** → Fix entrypoint; bind `0.0.0.0:$PORT`; check import errors after framework bumps.
* **DocAI 502/permission** → Grant `roles/documentai.apiUser`; verify processor/location; retry smoke.
* **Drive 403** → Add quota header; include `resourceKeys` header if metadata has key; ensure `roles/drive.readonly`.
* **OpenAI 4xx/TypeError** → Remove `response_format` from Responses path; flip to Chat JSON via flags.
* **Timeout/Flakiness** → `timeoutSeconds≤3600`; `concurrency=1`; retry with backoff.

---

## 8) Evidence Block (append on green)

```
### <UTC timestamp> — Final Verification (mcc-ocr-summary)
- revision: `<latestReadyRevisionName>`  commit: `<short SHA>`
- run.json: <compact JSON>
- validator.json: <compact JSON>
- metrics: GMP sidecar scraping internally; service remains private.
```

---

## 9) PR Checklist (include in PR body)

* [x] Format/List contracts pass.
* [x] OpenAI compat fixed (Responses no `response_format`; Chat JSON fallback tested).
* [x] Build & Deploy OK; revision recorded.
* [x] Final verification passed on real Drive file.
* [x] Evidence appended to `docs/audit/HARDENING_LOG.md`.

---

# Bootstrap Prompt — paste into **gpt‑5‑codex (Agent Mode)**

> **Policy**: Do **not** ask questions. Do **not** pause. Enforce **Git Sync & Squash**, then run **Pre‑Deploy Gates**. Only deploy when local tests pass, the container starts on `/healthz`, and DocAI preflight succeeds. Loop until all acceptance criteria are satisfied.

```text
REMEDIATION EXECUTION PROMPT v2 — gpt-5-codex (Agent Mode · VS Code)

ROLE & MODE
You run inside VS Code with an integrated shell. Execute autonomously in PHASES until all gates are green. If auth/tooling is missing, print the one-line fix and CONTINUE; re-attempt failing steps on the next loop.

CAPABILITY CHECKS (run, don’t assume)
- gcloud auth application-default print-access-token
- gh auth status
- git remote -v

GIT SYNC & SQUASH (BEFORE EVERY PHASE)
- Set rebase: git config --global pull.rebase true
- Fetch/prune: git fetch --all --prune && git remote -v
- Base: if origin/ops/finalize-prometheus-drive-docai exists → BASE=ops/finalize-prometheus-drive-docai else BASE=main
- Update base: git checkout "$BASE" && git pull --rebase origin "$BASE"
- Auto-merge PRs to base: gh pr list --state open --base "$BASE" --json number | jq -r '.[].number' | xargs -I{} gh pr merge {} --squash --auto || true
- Rebase locals on base; verify HEAD==origin/$BASE

PRE‑DEPLOY GATES (BLOCKERS)
1) Tests: pytest -q tests/test_format_contract.py tests/test_openai_backend.py tests/test_lists_contract.py
   - If ModuleNotFoundError for src.utils.pipeline_failures → create minimal stub and rerun.
2) Container: docker build -t mcc-local:preflight . && docker run --rm -e PORT=8080 -p 8080:8080 mcc-local:preflight &
   - curl -sf http://127.0.0.1:8080/healthz must return 200; otherwise fix entrypoint/bind/imports.
3) DocAI: us-documentai …:process tiny PDF with enableImageQualityScores=true & advancedOcrOptions=["legacy_layout"].
   - If fails → fix IAM/processor/location env, then retry.

PHASES (looping)
- PHASE 4: CI/CD & scans scaffold (lint/type/test/build/security/gates; actions pinned by SHA; trivy gate on main/tags; pip-audit report-only on PR). Open PRs; --auto --squash.
- PHASE 5: Build & Deploy to Cloud Run (timeout=3600, concurrency=1, --no-cpu-throttling). Ensure latest revision is Ready.
- PHASE 6: Final Verification: call /process/drive?file_id=1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS with ID token; download via Drive (quota + resource keys header if present); pdftotext; validator must be {ok:true, sections_ok:true, noise_found:false, length≥500}.
- PHASE 7: Evidence & PR: append run/validator/revision/metrics to HARDENING_LOG.md, push, gh pr create & gh pr merge --squash --auto.

STOP ONLY when all acceptance criteria are satisfied; otherwise loop.
```
