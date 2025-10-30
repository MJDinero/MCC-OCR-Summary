Purpose
Finish the Pylint ≥ 9.5 milestone and ship audit-ready evidence by running a non-stop, self-healing loop that:

Confirms container health,

Proves DocAI preflight,

Fixes Drive OAuth scopes (if needed), downloads the real report, validates it,

Appends evidence to HARDENING_LOG.md, and

Opens a squash-merge PR.

Capability & Credential Contract (non-stop)

You are inside VS Code with an integrated shell; you can run commands.

Assume credentials exist unless a command proves otherwise. Verify by executing (do not assume):

gcloud auth application-default print-access-token

gh auth status

git remote -v

gcloud run services describe mcc-ocr-summary --region us-central1 --format='value(status.latestReadyRevisionName)'

If any check fails, print exactly one remedial command (e.g., gh auth login, gcloud auth application-default login …) and continue with unblocked phases; retry the blocked step on the next loop.

Never echo secrets/PII; keep /metrics private (GMP sidecar scrapes internally).

Git Sync & Squash (run before every phase)

git config --global pull.rebase true

git fetch --all --prune && git remote -v

Base branch:

git show-ref --verify --quiet refs/remotes/origin/ops/finalize-prometheus-drive-docai \
  && BASE=ops/finalize-prometheus-drive-docai || BASE=main


git checkout "$BASE" && git pull --rebase origin "$BASE"

Auto-merge eligible PRs (non-draft, CI green):

gh pr list --state open --base "$BASE" --json number | jq -r '.[].number' \
  | xargs -I{} gh pr merge {} --squash --auto || true


Rebase locals on updated base:

for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do
  git checkout "$b" && git rebase "origin/$BASE" || true
done


Verify sync:
git checkout "$BASE" && git rev-parse HEAD && git rev-parse origin/"$BASE"

Environment & Constants

PROJECT_ID: quantify-agent · REGION: us-central1

SERVICE_NAME: mcc-ocr-summary · RUN_URL (private): https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app

Drive Intake: 1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE · Drive Output: 130jJzsl3OBzMD8weGfBOaXikfEnD2KVg

Real file (263p) ID: 1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS

DocAI (us) OCR options: enableImageQualityScores=true, advancedOcrOptions=["legacy_layout"]

OpenAI flags: openai_model, openai_use_responses, openai_json_mode

Preflights / Gates (must pass for E2E)

Tests: pytest -q … (contract/compat lists).

Container health: run image, 200 on /health (or /healthz alias).

DocAI preflight (us): tiny 1-page PDF, success JSON logged.

Drive download: use OAuth access token (ADC) with Drive scopes for Drive API; use ID token for private Cloud Run.

Drive scopes one-liner (if 403 ACCESS_TOKEN_SCOPE_INSUFFICIENT):

gcloud auth application-default login --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/drive.file,https://www.googleapis.com/auth/cloud-platform && gcloud auth application-default set-quota-project ${PROJECT_ID}


Drive headers (always):

X-Goog-User-Project: ${PROJECT_ID}

If metadata returns resourceKey, add X-Goog-Drive-Resource-Keys: <fileId>/<resourceKey>

Artifacts (write under ./remediation/pylint/ unless noted)

logs/docker_health_response.json, logs/docai_smoke.json, logs/process_drive_status_latest.txt, logs/drive_meta_error.json

run.json, meta.json, validator.json (repo root ok), report.pdf / report.txt (prefer under remediation/artifacts/)

Pylint artifacts already present: pylint_report.json, scoreboard.md, SUMMARY.md

Failure Matrix → Auto-Fixes

Drive 403 scope: run the ADC Drive scopes one-liner above; continue loop.

Container not 200: ensure bind 0.0.0.0:$PORT and correct entrypoint; rebuild and retry.

DocAI errors: ensure roles/documentai.apiUser on the service account; confirm processor & location us.

Resource keys: include X-Goog-Drive-Resource-Keys only when metadata returns resourceKey.

Bootstrap Prompt — paste into gpt-5-codex (Agent Mode)
DRIVE-SCOPE FIX + FINAL EVIDENCE — GPT-5 Codex (Agent Mode · VS Code)

ROLE & MODE
You are GPT-5 Codex running autonomously in VS Code’s integrated shell. Finish the lint milestone by:
(1) fixing the Drive scope block, (2) downloading/validating the real report, and (3) appending evidence + opening a squash-merge PR.
Do NOT ask questions. If a step needs interactive auth, PRINT the exact one-liner and CONTINUE with any unblocked tasks; retry on the next loop.

NON-STOP POLICY
- Minimal, reversible diffs; one focused PR per change; always squash-merge when checks pass.
- Redact secrets. No PHI/PII in logs or commits.

CONSTANTS
PROJECT_ID=quantify-agent
REGION=us-central1
SERVICE_NAME=mcc-ocr-summary
RUN_URL="https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app"
SRC_FILE_ID="1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS"

ARTIFACTS
Write under ./remediation/pylint/ and repo root:
- logs/process_drive_status_latest.txt, logs/drive_meta_error.json, docker_health_response.json, docai_smoke.json
- run.json, meta.json, report.pdf, report.txt, validator.json
- pylint_report.json, scoreboard.md, SUMMARY.md

GIT SYNC & SQUASH (run before work and before exit)
1) git config --global pull.rebase true
2) git fetch --all --prune && git remote -v
3) BASE=$(git show-ref --verify --quiet refs/remotes/origin/ops/finalize-prometheus-drive-docai && echo ops/finalize-prometheus-drive-docai || echo main)
4) git checkout "$BASE" && git pull --rebase origin "$BASE"
5) gh pr list --state open --base "$BASE" --json number | jq -r '.[].number' | xargs -I{} gh pr merge {} --squash --auto || true
6) for b in $(git for-each-ref --format='%(refname:short)' refs/heads); do git checkout "$b" && git rebase "origin/$BASE" || true; done
7) git checkout "$BASE" && git rev-parse HEAD && git rev-parse origin/"$BASE"

EXECUTION PLAN (self-healing; loop until validator ok:true)
A) Quick recon (no-op if already present)
- Ensure ./remediation/pylint/{logs,patches} exists.
- Confirm container gate log exists and shows 200 on /health; if missing, build+run image, probe /health (or /healthz) and save docker_health_response.json.
- Confirm DocAI smoke at remediation/pylint/logs/docai_smoke.json; if missing, run the 1-page smoke and save JSON.

B) Obtain /process/drive result (ID token → Cloud Run)
- IDTOKEN=$(gcloud auth print-identity-token --audiences="$RUN_URL" 2>/dev/null || true)
- curl -sS -w "\n%{http_code}" -H "Authorization: Bearer ${IDTOKEN}" \
  "$RUN_URL/process/drive?file_id=${SRC_FILE_ID}" \
  | tee remediation/pylint/logs/process_drive_status_latest.txt \
  | { read body; read code; printf "%s" "$body" > run.json; echo "$code" > remediation/pylint/logs/process_http.txt; }
- Extract RFID=$(jq -r '.report_file_id // empty' run.json). If empty, tail Cloud Run logs and continue loop.

C) Drive metadata + download (ACCESS vs ID token)
- Use an ACCESS token with Drive scopes for Drive API calls:
  AT=$(gcloud auth application-default print-access-token 2>/dev/null || true)

- Metadata:
  curl -sS -H "Authorization: Bearer ${AT}" -H "X-Goog-User-Project: ${PROJECT_ID}" \
    "https://www.googleapis.com/drive/v3/files/${RFID}?supportsAllDrives=true&fields=id,name,resourceKey" \
    | tee meta.json | jq . >/dev/null || true

- If metadata returns 403 with "ACCESS_TOKEN_SCOPE_INSUFFICIENT", PRINT:
  gcloud auth application-default login --scopes=https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/drive.file,https://www.googleapis.com/auth/cloud-platform && gcloud auth application-default set-quota-project ${PROJECT_ID}
  Continue to step E and retry step C on the next loop.

- Else (metadata ok): set RK=$(jq -r '.resourceKey // empty' meta.json)
  Build headers:
    HDR=(-H "Authorization: Bearer ${AT}" -H "X-Goog-User-Project: ${PROJECT_ID}")
    [ -n "$RK" ] && HDR+=( -H "X-Goog-Drive-Resource-Keys: ${RFID}/${RK}" )
  Download:
    curl -sS -L "${HDR[@]}" "https://www.googleapis.com/drive/v3/files/${RFID}?supportsAllDrives=true&alt=media" > report.pdf

D) Validate the PDF (must conclude green)
- pdftotext -eol unix -nopgbrk -enc UTF-8 -nodiag report.pdf report.txt
- python - <<'PY'
import json,re
t=open("report.txt","r",encoding="utf-8",errors="ignore").read()
hdrs=["Intro Overview","Key Points","Detailed Findings","Care Plan & Follow-Up"]
res={"ok":all(h in t for h in hdrs) and not re.search(r"(\(Condensed\)|Structured Indices|Summary Notes)",t) and len(t)>=500,
     "sections_ok":all(h in t for h in hdrs),
     "noise_found":bool(re.search(r"(\(Condensed\)|Structured Indices|Summary Notes)",t)),
     "length":len(t)}
open("validator.json","w").write(json.dumps(res))
print(res)
PY
- If validator.ok != true, log and continue loop (re-run /process/drive and download on next pass).

E) AGENT DOCS (optional, non-blocking)
- If AGENTS.md lacks a Drive 403 handler, append the one-liner scope fix and open PR “docs(agent): add Drive scope handler”; squash-merge (auto).

F) Evidence & PR (when validator ok:true)
- REV=$(gcloud run services describe "$SERVICE_NAME" --region "$REGION" --format='value(status.latestReadyRevisionName)')
- SHA=$(git rev-parse --short=12 HEAD)
- MIN=$(jq -r '.min' remediation/pylint/pylint_report.json 2>/dev/null || echo null)
- MEAN=$(jq -r '.mean' remediation/pylint/pylint_report.json 2>/dev/null || echo null)
- MAX=$(jq -r '.max' remediation/pylint/pylint_report.json 2>/dev/null || echo null)
- COV=$(grep -Eo '([0-9]+\.[0-9]+)%' remediation/pylint/logs/pytest_phase2.txt | tail -1 || true)

Append to docs/audit/HARDENING_LOG.md:
"""
### $(date -u +%FT%TZ) — Pylint ≥ 9.5 + Final Verification (mcc-ocr-summary)
- revision: `${REV}`  commit: `${SHA}`
- pylint: {"min": ${MIN}, "mean": ${MEAN}, "max": ${MAX}}
- coverage: ${COV}
- validator: $(jq -c . validator.json || cat validator.json)
"""

- Create branch `ops/pylint-final-evidence`, add:
  docs/audit/HARDENING_LOG.md remediation/pylint/* run.json meta.json validator.json
- Commit, push, open PR “Pylint ≥9.5 + final verification evidence”, **squash-merge** (auto).

G) GIT SYNC & SQUASH again; print a compact summary table and exit 0.

BEGIN NOW
