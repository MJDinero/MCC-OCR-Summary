#!/usr/bin/env bash
set -euo pipefail

# End-to-end smoke test for the MCC OCR Summary pipeline.
# 1. Uploads a sample PDF to the intake bucket.
# 2. Calls the ingestion endpoint.
# 3. Polls /status until the job reaches UPLOADED.
# 4. Confirms the signed URL responds with HTTP 200.

PROJECT_ID="${PROJECT_ID:?PROJECT_ID is required}"
REGION="${REGION:?REGION is required}"
INTAKE_BUCKET="${INTAKE_BUCKET:?INTAKE_BUCKET is required}"
SERVICE_URL="${SERVICE_URL:?SERVICE_URL (https://host) is required}"
PDF_PATH="${PDF_PATH:-tests/fixtures/sample.pdf}"
TRACE_ID="smoke-$(date +%s)"

echo "🚀 Starting E2E smoke test (trace ${TRACE_ID})"

OBJECT_NAME="smoke-tests/${TRACE_ID}.pdf"
GS_URI="gs://${INTAKE_BUCKET}/${OBJECT_NAME}"

echo "📤 Uploading ${PDF_PATH} to ${GS_URI}"
gcloud storage cp "${PDF_PATH}" "${GS_URI}" --project "${PROJECT_ID}"

INGEST_PAYLOAD=$(cat <<JSON
{
  "object": {
    "bucket": "${INTAKE_BUCKET}",
    "name": "${OBJECT_NAME}",
    "generation": "1"
  },
  "trace_id": "${TRACE_ID}",
  "source": "e2e-smoke"
}
JSON
)

echo "🔔 Invoking /ingest"
INGEST_RESPONSE=$(curl -sS -X POST "${SERVICE_URL}/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Cloud-Trace-Context: ${TRACE_ID}/0;o=1" \
  -d "${INGEST_PAYLOAD}")

JOB_ID=$(echo "${INGEST_RESPONSE}" | jq -r '.job_id')
if [[ -z "${JOB_ID}" || "${JOB_ID}" == "null" ]]; then
  echo "❌ Failed to parse job_id from response: ${INGEST_RESPONSE}"
  exit 1
fi

echo "🕐 Polling /status/${JOB_ID}"
DEADLINE=$((SECONDS + 900))
SIGNED_URL=""
while [[ ${SECONDS} -lt ${DEADLINE} ]]; do
  STATUS_RESPONSE=$(curl -sS "${SERVICE_URL}/status/${JOB_ID}")
  STATUS=$(echo "${STATUS_RESPONSE}" | jq -r '.status')
  SIGNED_URL=$(echo "${STATUS_RESPONSE}" | jq -r '.signed_url // empty')
  echo "   -> status=${STATUS}"
  if [[ "${STATUS}" == "UPLOADED" && -n "${SIGNED_URL}" ]]; then
    break
  fi
  sleep 10
done

if [[ "${STATUS}" != "UPLOADED" || -z "${SIGNED_URL}" ]]; then
  echo "❌ Pipeline did not reach UPLOADED in time."
  exit 2
fi

echo "🔐 Validating signed URL"
HTTP_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "${SIGNED_URL}")
if [[ "${HTTP_STATUS}" != "200" ]]; then
  echo "❌ Signed URL check failed with status ${HTTP_STATUS}"
  exit 3
fi

echo "🔁 Verifying idempotent ingest handling"
DUP_STATUS=$(curl -s -o /tmp/dup_ingest.json -w '%{http_code}' -X POST "${SERVICE_URL}/ingest" \
  -H "Content-Type: application/json" \
  -H "X-Cloud-Trace-Context: ${TRACE_ID}/0;o=1" \
  -d "${INGEST_PAYLOAD}")
if [[ "${DUP_STATUS}" != "412" ]]; then
  echo "❌ Expected duplicate ingest to return HTTP 412, got ${DUP_STATUS}"
  cat /tmp/dup_ingest.json
  exit 4
fi

echo "🪵 Fetching structured logs for trace ${TRACE_ID}"
LOG_TMP=$(mktemp)
gcloud logging read \
  "jsonPayload.trace_id=\"${TRACE_ID}\"" \
  --project "${PROJECT_ID}" \
  --format=json \
  --freshness="30m" \
  --order="asc" \
  --limit=200 > "${LOG_TMP}"

python3 - <<'PY'
import json, sys
from pathlib import Path
log_path = Path(sys.argv[1])
if not log_path.exists():
    print("❌ No logs retrieved for trace", file=sys.stderr)
    sys.exit(5)
data = json.loads(log_path.read_text() or "[]")
sequence = ["ingest_received","split_done","ocr_lro_finished","summary_done","pdf_writer_complete","drive_upload_complete"]
messages = [entry.get("jsonPayload", {}).get("message") for entry in data]
missing = [stage for stage in sequence if stage not in messages]
if missing:
    print(f"❌ Missing expected log markers: {', '.join(missing)}", file=sys.stderr)
    sys.exit(6)
indices = [messages.index(stage) for stage in sequence]
if indices != sorted(indices):
    print("❌ Log markers out of order", file=sys.stderr)
    print("Observed order:", messages, file=sys.stderr)
    sys.exit(7)
print("✅ Log markers present in order:")
for stage in sequence:
    print(f"   - {stage}")
PY
rm -f "${LOG_TMP}"

echo "✅ Smoke test succeeded for job ${JOB_ID}"
