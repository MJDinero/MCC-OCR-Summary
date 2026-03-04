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

Example historical sample ID (from prior audit evidence):
- `1ZFra9EN0jS8wTS4dcW7deypxnVggb8vS`

## Step 1: Capture Live Responses
```bash
export SERVICE_URL="https://mcc-ocr-summary-6vupjpy5la-uc.a.run.app"
export INTERNAL_EVENT_TOKEN="<token>"
export RUN_DIR="outputs/live_regression/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"

# Replace with your approved regression IDs.
FILE_IDS=(
  "<drive_file_id_1>"
  "<drive_file_id_2>"
)

for file_id in "${FILE_IDS[@]}"; do
  curl -sS -f \
    -H "X-Internal-Event-Token: ${INTERNAL_EVENT_TOKEN}" \
    -H "X-Request-ID: live-regression-${file_id}" \
    "${SERVICE_URL}/process/drive?file_id=${file_id}" \
    | tee "${RUN_DIR}/${file_id}.json"
done
```

## Step 2: Validate Response Contract
```bash
.venv/bin/python scripts/verify_live_regression.py \
  --responses-dir "$RUN_DIR" \
  --expect-count "${#FILE_IDS[@]}"
```

Pass criteria:
- every response has a valid `report_file_id`
- every response has `supervisor_passed=true`
- every response has a non-empty `request_id`

## Step 3: Optional PDF Structure Check
If you download generated report PDFs from Drive, run:

```bash
.venv/bin/python scripts/validate_summary.py \
  --pdf-path <downloaded_report.pdf> \
  --expected-pages <expected_page_count>
```

## Evidence To Record
- `RUN_DIR` path and timestamp
- exact file IDs tested
- service URL used
- output of `scripts/verify_live_regression.py`
- any supervisor or structure failures with response JSON paths
