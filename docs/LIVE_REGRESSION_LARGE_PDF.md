# Live Large-PDF Regression Runbook

## Purpose
Validate large-PDF summarization behavior against known Drive regression inputs without changing cloud infrastructure.

## Safety Boundaries
- No cloud writes from tooling in this repo.
- This runbook is human-invoked only.
- Do not run these commands from CI automation.

## Inputs Required
- `SERVICE_URL`: Cloud Run URL for `mcc-ocr-summary`.
- `INTERNAL_EVENT_TOKEN`: token expected by the service.
- A vetted list of Drive file IDs for known large-PDF regression samples.
- An expected IDs manifest file (newline-delimited IDs) for deterministic run validation.

Reference template:
- `docs/LIVE_REGRESSION_EXPECTED_IDS.example.txt`

Example historical sample ID (from prior audit evidence):
- `1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS`

## Step 0: Prepare Run Directory
```bash
export RUN_ID="$(date +%Y%m%d-%H%M%S)"
export RUN_DIR="outputs/live_regression/${RUN_ID}"
mkdir -p "${RUN_DIR}/responses"
cp docs/LIVE_REGRESSION_EXPECTED_IDS.example.txt "${RUN_DIR}/expected_ids.txt"
# Replace placeholder IDs in ${RUN_DIR}/expected_ids.txt with approved live IDs.
```

## Step 1: Capture Live Responses
```bash
export SERVICE_URL="https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app"
export INTERNAL_EVENT_TOKEN="<token>"
while IFS= read -r file_id; do
  [[ -z "${file_id}" || "${file_id}" =~ ^# ]] && continue
  curl -sS -f \
    -H "X-Internal-Event-Token: ${INTERNAL_EVENT_TOKEN}" \
    -H "X-Request-ID: live-regression-${file_id}" \
    "${SERVICE_URL}/process/drive?file_id=${file_id}" \
    | tee "${RUN_DIR}/responses/${file_id}.json"
done < "${RUN_DIR}/expected_ids.txt"
```

## Step 2: Validate Response Contract
```bash
.venv/bin/python scripts/verify_live_regression.py \
  --responses-dir "${RUN_DIR}/responses" \
  --expected-file-ids-file "${RUN_DIR}/expected_ids.txt" \
  --strict-keys \
  --min-success-rate 1.0 \
  --scorecard-out "${RUN_DIR}/scorecard.json"
```

Pass criteria:
- expected and actual source file IDs match exactly
- every response has exactly these keys: `report_file_id`, `supervisor_passed`, `request_id`
- every response has a valid `report_file_id`
- every response has `supervisor_passed=true`
- every response has a non-empty `request_id`
- no duplicate `report_file_id` values
- no duplicate `request_id` values
- `success_rate` in `scorecard.json` is `1.0`

## Step 3: Optional PDF Structure Check
If you download generated report PDFs from Drive, run:

```bash
.venv/bin/python scripts/validate_summary.py \
  --pdf-path <downloaded_report.pdf> \
  --expected-pages <expected_page_count>
```

## Step 4: Capture Evidence
Use `docs/LIVE_REGRESSION_EVIDENCE_TEMPLATE.md` and fill it with:
- exact run directory
- expected IDs file used
- scorecard path and key metrics
- any structural/PDF validation failures
- operator and timestamp

## Scoring Rubric
- `100`:
  - all expected IDs present with no extras
  - `supervisor_passed=true` for every response
  - no duplicate report IDs or request IDs
  - scorecard `success_rate=1.0`
- `90`:
  - one non-critical failure accepted by reviewer with documented rationale
- `<90`:
  - any unresolved contract, supervisor, or ID-set mismatch
  - must be treated as a regression candidate requiring follow-up

## Evidence To Record
- `RUN_DIR` path and timestamp
- exact expected IDs manifest used
- service URL used
- output of `scripts/verify_live_regression.py`
- `scorecard.json` artifact path
- any supervisor or structure failures with response JSON paths
