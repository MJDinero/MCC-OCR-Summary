# AGENTS.md — MCC‑OCR‑Summary

## Purpose
Guidance for automation agents hacking on this repo. Follow these conventions so changes stay Bible-compliant and CI passes first try.

## Environment & Setup
- Python 3.11+ (CI uses 3.11).
- Install deps + tooling:
  ```bash
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt -c constraints.txt
  python -m pip install -r requirements-dev.txt -c constraints.txt
  ```
- Most tests assume `STUB_MODE=true` with mock credentials. When running locally set:
  ```bash
  export STUB_MODE=true
  export GOOGLE_APPLICATION_CREDENTIALS=/path/to/mock-sa.json
  ```

## Primary Commands
| Purpose | Command |
| --- | --- |
| Format/lint | `python -m ruff check src tests scripts && python -m pylint --rcfile=.pylintrc src` |
| Type check | `python -m mypy --strict` |
| Unit + integration tests | `python -m pytest` |
| Drive validator (263-page) | `python scripts/validate_summary.py --base-url "$(gcloud run services describe mcc-ocr-summary --region us-central1 --format='value(status.url)')" --source-file-id 1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS --expected-pages 263 --credentials ~/Downloads/mcc_orch_sa_key.json --impersonate Matt@moneymediausa.com` |
| PDF contract validator (local fixture) | `python scripts/validate_summary.py --pdf-path <pdf> --expected-pages <n>` |
| Full CI parity (sequence) | `ruff check && black --check . && python -m mypy --strict && python -m pytest` |

## Acceptance Criteria
1. **Bible contract:** Summaries and PDFs must emit headings in this exact order:
   `Provider Seen → Reason for Visit → Clinical Findings → Treatment / Follow-up Plan → Diagnoses → Healthcare Providers → Medications / Prescriptions`.
2. **Noise-free:** No consent/billing phrases (`Structured Indices`, `Summary Notes`, `Document processed in…`, `I understand that…`, etc.) in any section lists or PDF body.
3. **Validator green:** `scripts/validate_summary.py` passes against both the CI sample PDF and the 263-page regression PDF post-deploy (Cloud Build runs this automatically using `_VALIDATION_BASE_URL`, `_VALIDATION_SOURCE_FILE_ID`, `_VALIDATION_CREDENTIALS_SECRET`, and optional `_VALIDATION_IMPERSONATE` substitutions).
4. **CI:** Lint, type check, tests, security scans, and validator jobs in `.github/workflows/ci.yml` succeed.

## Guidelines
- Never downgrade schema versions; keep `SUMMARY_SCHEMA_VERSION=2025-11-16`.
- Honor `SUMMARY_COMPOSE_MODE=refactored`, `PDF_WRITER_MODE=rich`, and `PDF_GUARD_ENABLED=true`. Fail fast if new modes are introduced without implementation.
- When modifying summariser prompts/filters, update both formatter tests and the validator script.
- Keep the Cloud Build validator substitutions pointing at a real service + 263-page Drive file before triggering builds; update `_VALIDATION_*` when targeting a new environment.
- Document any new operational commands or quality gates here so future agents inherit the same runbook.
- When touching dependencies, edit `requirements.in` / `requirements-dev.in` and regenerate the pinned `.txt` files with `pip-compile`.
- Canonical MCC Bible headings + forbidden phrases now live in `src/services/summarization/bible.py`; update that module instead of redefining strings in individual services or scripts.
- Alert JSON files under `infra/monitoring/` include templated PagerDuty/email channels plus runbook URLs—keep the `${ENV}` / `${PROJECT_ID}` placeholders intact so `infra/monitoring/apply_monitoring.py --project <proj> --environment <env>` can render the right destinations.
- Dependabot opens weekly PRs for `requirements.in*`; pull those branches locally and run `pip-compile` before merging to keep the lockfiles accurate.
