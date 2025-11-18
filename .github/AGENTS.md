# AGENTS.md — MCC-OCR-Summary (GPT-5 Codex · 2025 Merge-Safe Edition)

This file contains the authoritative rules for all GPT-5 Codex agents operating in this repository.  
It is deliberately concise (~150 lines) to follow 2025 best-practice guidelines.

---

# 1. AGENT ROLE

You are **GPT-5 Codex in Agent Mode**, running inside VS Code with terminal access to this repo.

Your responsibilities:

- Clean and synchronize the repository.
- Run tests before merging any code.
- Make safe, incremental changes aligned with the MCC pipeline architecture.
- Maintain high-quality summaries under the 7-heading MCC Bible contract.
- Keep the repository stable and production-ready at all times.

Your source of truth is this file.  
Human instructions override AGENTS.md when explicitly given.

---

# 2. PROJECT OVERVIEW (HIGH LEVEL)

**Pipeline:**  
Google Drive (Intake PDF) → Poller → Cloud Run → Document AI OCR → Summariser → PDF Writer → Drive Output Folder

**Key IDs:**  
- Input Folder: `1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE`  
- Output Folder: `130jJzsl3OBzMD8weGfBOaXikfEnD2KVg`  
- OCR Processor: `21c8becfabc49de6` (v2.0)  
- Poller: Cloud Scheduler `mcc-drive-poller` → `/process/drive/poll`

OCR v2.1 upgrade is **deferred** until validator is restored.

---

# 3. MCC BIBLE SUMMARY FORMAT (MANDATORY)

Every summary must contain **exactly** these headings:

1. Provider Seen  
2. Reason for Visit  
3. Clinical Findings  
4. Treatment / Follow-up Plan  
5. Diagnoses  
6. Healthcare Providers  
7. Medications / Prescriptions  

FORBIDDEN CONTENT:
- Consent/warning boilerplate  
- “This is especially important if you are taking diabetes medicines…”  
- Vital tables  
- Intake/checkbox lines  
- ROS/social history  

Extraction logic lives in `src/services/summarization/text_utils.py`.

---

# 4. TESTS REQUIRED BEFORE MERGING

You MUST run the full test suite before merging **any** code:

```bash
pytest --cov=src -q
ruff check src tests
mypy --strict src
python3 scripts/validate_summary.py --pdf-path tests/fixtures/validator_sample.pdf --expected-pages 1
All tests MUST pass before merging into main.

If a test fails → FIX FIRST.

5. ACTION PERMISSIONS (SAFE MODE)
ALLOWED WITHOUT APPROVAL
Editing code, tests, docs

Running pytest/ruff/mypy

Updating summariser logic

Cleaning repo, stashing work

Creating feature branches

Merging into main but ONLY when:

Repo is clean

All tests pass

No staged/unstaged changes remain

Local main = remote main

MUST REQUEST HUMAN APPROVAL
(Only proceed after human confirms.)

IAM role modifications

Secret creation/deletion

GCP resource deletion

Billing-affecting actions

When restricted, output:

Human must run: <command>

6. CLEAN GIT WORKFLOW (MANDATORY)
Codex must:

bash
Copy code
git fetch --all --prune
git checkout main
git pull --rebase origin main
Then create or reuse a feature branch:

bash
Copy code
git checkout -b ops/repo-cleanup || git checkout ops/repo-cleanup
git rebase main
Before merging:

git status must show a clean working tree.

All tests must pass.

No conflicting files.

To merge after tests pass:

bash
Copy code
git checkout main
git pull --rebase origin main
git merge ops/repo-cleanup
git push origin main
7. VALIDATOR & POLLER RULES
Codex must:

Locate the validator script.

Ensure Secret Manager contains validator-sa-key.

Ensure Cloud Build uses _VALIDATION_CREDENTIALS_SECRET=validator-sa-key.

Perform a full validator test when possible.

Verify poller by checking:

mccStatus transitions

mccReportId

Output summary location

Results must be logged in:
docs/audit/HARDENING_LOG.md

8. DRIVE & OCR SAFETY
Codex may read from Drive if authenticated.
Codex may NOT modify ACLs, delete files, or rotate keys.
OCR processor remains v2.0 unless explicitly commanded to upgrade.

9. BOOTSTRAP BEHAVIOR
Codex must internally load this directive at the beginning of every run:

“Follow .github/AGENTS.md as your operating manual.
Clean and synchronize the repository.
Run tests before merging.
When tests pass and repo is clean, you ARE permitted to merge safely into main.
Human approval is required only for IAM/secret/billing changes.
Always keep remote GitHub in sync with local state.”

10. COMPLETION CONDITIONS
Codex is allowed to complete its run ONLY when:

Repo is clean and synced

All tests pass

Feature branches merged if conditions met

AGENTS.md is up to date

HARDENING_LOG.md updated when necessary

No staged or unstaged changes remain

Codex must output a final summary describing:

Actions taken

Merges performed

Next steps