# AGENTS.md — MCC-OCR-Summary (GPT-5 Codex · Production)

This file is read by GPT-5 Codex in Agent Mode when working in this repo.

## 0. Operating Mode

- You are GPT-5 Codex running inside VS Code with terminal access.
- You may run git, python, gcloud, and tests.
- Default: **do not ask questions**; prefer small, safe changes. Ask only when blocked by IAM or missing secrets.
- The goal is to keep the production pipeline healthy, not to do large speculative refactors.

## 1. Core System Overview

Pipeline (production MVP):

1. **Intake**: PDFs arrive in the Google Drive **MCC Input Folder**.
2. **Trigger**: Cloud Scheduler job `mcc-drive-poller` calls `/process/drive/poll` on Cloud Run every minute (OIDC auth with `mcc-orch-sa`).
3. **Drive Poller**:
   - Lists PDFs from the MCC shared drive using `src/services/drive_client.py`.
   - Skips files with `appProperties.mccStatus in {completed, processing, failed}`.
   - Skips files whose names start with `summary-`.
   - Marks new files as `processing` → `completed` or `failed`, and stores `mccReportId`.
4. **Processing**: For each new intake file, the pipeline runs:
   - Drive download → DocAI OCR (processor `21c8becfabc49de6` in `us`) → OpenAI summariser → PDF writer → Drive upload to **MCC Output Folder**.
5. **Output**: Summary PDFs land in the MCC Output Folder, with 7 MCC Bible headings and no forbidden phrases.

Key constants (do not change lightly):

- **PROJECT_ID**: `quantify-agent`
- **REGION**: `us-central1`
- **Cloud Run service**: `mcc-ocr-summary`
- **DOC_AI_PROCESSOR_ID**: `21c8becfabc49de6`
- **DOC_AI processor version**: `pretrained-ocr-v2.0-2023-06-02` (rc alias `pretrained-ocr-v2.1-2024-08-07` exists—see HARDENING_LOG for the upgrade plan before switching)
- **DRIVE_INPUT_FOLDER_ID** (MCC Input Folder): `1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`
- **DRIVE_REPORT_FOLDER_ID** (MCC Output Folder): `130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`
- Real 263-page intake file: `1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS`

## 2. MCC Bible Contract (Summary Format)

The summary PDF must contain exactly these headings, in this order:

1. Provider Seen  
2. Reason for Visit  
3. Clinical Findings  
4. Treatment / Follow-up Plan  
5. Diagnoses  
6. Healthcare Providers  
7. Medications / Prescriptions  

Rules:

- Use concise, clinically relevant prose.
- No intake-form boilerplate (checkboxes, ROS lists, “drinks caffeine”, etc.).
- No consent/legal language (“I voluntarily request…”, “I understand that…”).
- No internal meta or logging text.
- Always rely on `src/services/bible.py` for headings and forbidden phrases. Do not re-define or duplicate them.

## 3. Build, Test, and Validate

Before deploying or making non-trivial changes:

```bash
pip-compile requirements.in
pip-compile requirements-dev.in
pip install -r requirements-dev.txt -c constraints.txt

pytest --cov=src -q
ruff check src tests
mypy --strict src

python3 scripts/validate_summary.py --pdf-path tests/fixtures/validator_sample.pdf --expected-pages 1
All of the above must pass.

3.1 263-page End-to-End Validator
There is a separate Cloud Build step that runs the full 263-page regression against the deployed service using:

The real intake file: 1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS

Secret Manager secret `validator-sa-key` that stores the `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com` JSON key (Drive reader + DocAI access) and is referenced by `_VALIDATION_CREDENTIALS_SECRET`.

If `_VALIDATION_CREDENTIALS_SECRET` is missing, the Cloud Build validator is allowed to skip gracefully.
For production hardening, ensure:

The validator secret exists and contains a fresh `mcc-orch-sa` key (or a dedicated validator SA) with Drive read + DocAI permissions scoped to the MCC folders.

`_VALIDATION_CREDENTIALS_SECRET` in `cloudbuild.yaml` points to `validator-sa-key` (update substitutions when renaming the secret).

The 263-page validator step completes successfully before declaring the system “ready”.

4. Cloud Run & IAM
Runtime service account: mcc-orch-sa@quantify-agent.iam.gserviceaccount.com

Cloud Build service account must have:

roles/run.builder

roles/artifactregistry.reader on the image repo

roles/iam.serviceAccountUser on mcc-orch-sa

Runtime SA should not have broad roles like Editor. It needs only:

Document AI client roles (for OCR processors)

Drive access scoped to the MedCostContain shared drive and MCC folders

Logging/Monitoring writer roles

5. Auto-Trigger Expectations
Cloud Scheduler job `mcc-drive-poller` runs every minute and POSTs to `/process/drive/poll` using an OIDC token issued for `mcc-orch-sa@quantify-agent.iam.gserviceaccount.com` plus the `X-Internal-Event-Token` header sourced from Secret Manager. Do not remove or disable any of the following without a very good reason:

Cloud Scheduler job: mcc-drive-poller

/process/drive/poll endpoint

Drive appProperties bookkeeping (mccStatus, mccReportId)

The system should:

Discover new intake PDFs within roughly 1 minute.

Process them exactly once (idempotent behavior).

Mark them as completed/failed in appProperties.

If you need to reduce polling overhead in the future, you may:

Introduce an Eventarc + GCS-based trigger.

But keep /process/drive/poll as a supported path for diagnostics.

6. Cost Awareness
Cloud Scheduler charges per job, not per execution. One job is ≈ $0.10/month after the first 3 jobs; executions are free.
Cloud Run has a generous free tier (vCPU-seconds, GiB-seconds, and requests). The once-per-minute poller is well under that for typical workloads, so its incremental cost is negligible.

7. Evidence & Logging
When making significant changes (deployments, trigger edits, OCR upgrades):

Append an evidence block to docs/audit/HARDENING_LOG.md with:

Timestamp (UTC)

Cloud Build ID

Cloud Run revision

OCR processor ID

Trigger details (Scheduler / Eventarc)

Latest report_file_id and validator JSON summary

Keep log messages structured and PHI-free.

8. When Work Is “Done”
For any hardening task, consider the work complete when:

All tests and validators pass.

263-page regression passes with Bible-compliant output.

The auto-trigger path (MCC Input Folder → MCC Output Folder) has been exercised and logged.

Any IAM / secret changes are reflected in this file or in the audit docs.
