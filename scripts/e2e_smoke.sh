#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/e2e_smoke.sh [--dry-run] [--project-id ID] [--region REGION]
                            [--workflow-name NAME] [--scheduler-job NAME]
                            [--drive-folder-id ID] [--service-account EMAIL]
                            [--output-bucket BUCKET] [--timeout-seconds N]
                            [--poll-seconds N]

Safety:
  - Real cloud actions are blocked unless CONFIRM_LIVE_RUN=1 is set.
  - --dry-run prints planned commands and exits without cloud actions.
  - The uploaded PDF is generated locally and marked NON-PHI synthetic.
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 2
  fi
}

resolve_python() {
  if [[ -x ".venv/bin/python" ]]; then
    echo ".venv/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  echo "python"  # fallback for environments without python3 symlink
}

PROJECT_ID="quantify-agent"
REGION="us-central1"
WORKFLOW_NAME="docai-pipeline"
SCHEDULER_JOB="mcc-drive-poller"
DRIVE_FOLDER_ID="1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE"
SERVICE_ACCOUNT_EMAIL="mcc-orch-sa@quantify-agent.iam.gserviceaccount.com"
OUTPUT_BUCKET="mcc-output"
TIMEOUT_SECONDS=600
POLL_SECONDS=10
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --project-id)
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --workflow-name)
      WORKFLOW_NAME="$2"
      shift 2
      ;;
    --scheduler-job)
      SCHEDULER_JOB="$2"
      shift 2
      ;;
    --drive-folder-id)
      DRIVE_FOLDER_ID="$2"
      shift 2
      ;;
    --service-account)
      SERVICE_ACCOUNT_EMAIL="$2"
      shift 2
      ;;
    --output-bucket)
      OUTPUT_BUCKET="$2"
      shift 2
      ;;
    --timeout-seconds)
      TIMEOUT_SECONDS="$2"
      shift 2
      ;;
    --poll-seconds)
      POLL_SECONDS="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

RUN_TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
RUN_START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

PDF_PATH="${TMP_DIR}/synthetic-non-phi-${RUN_TS}.pdf"
PDF_NAME="$(basename "${PDF_PATH}")"
PYTHON_BIN="$(resolve_python)"

if [[ "${DRY_RUN}" == "1" ]]; then
  cat <<EOF
DRY RUN ONLY (no cloud actions):
  - Generate synthetic NON-PHI PDF: ${PDF_PATH}
  - Mint Drive-scoped token:
      gcloud auth print-access-token
      curl -X POST https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${SERVICE_ACCOUNT_EMAIL}:generateAccessToken
  - Upload to Drive folder ${DRIVE_FOLDER_ID} via files.create multipart
  - Trigger scheduler once:
      gcloud scheduler jobs run ${SCHEDULER_JOB} --location ${REGION} --project ${PROJECT_ID}
  - Poll workflow executions:
      gcloud workflows executions list ${WORKFLOW_NAME} --location ${REGION} --project ${PROJECT_ID}
      gcloud workflows executions describe <execution-id> --workflow ${WORKFLOW_NAME} --location ${REGION} --project ${PROJECT_ID}
  - Verify artifacts:
      gs://${OUTPUT_BUCKET}/summaries/<job_id>.json
      gs://${OUTPUT_BUCKET}/pdf/<job_id>.pdf
EOF
  exit 0
fi

if [[ "${CONFIRM_LIVE_RUN:-0}" != "1" ]]; then
  echo "Blocked: set CONFIRM_LIVE_RUN=1 to run cloud actions." >&2
  exit 3
fi

require_cmd gcloud
require_cmd curl
require_cmd jq

echo "Generating synthetic NON-PHI PDF at ${PDF_PATH}"
"${PYTHON_BIN}" - "${PDF_PATH}" "${RUN_TS}" <<'PY'
import sys
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

path = sys.argv[1]
ts = sys.argv[2]

doc = canvas.Canvas(path, pagesize=letter)
doc.setFont("Helvetica", 12)
doc.drawString(72, 760, "MCC OCR Smoke Test - NON-PHI Synthetic Document")
doc.drawString(72, 736, f"Generated UTC: {ts}")
doc.drawString(72, 712, "Content: synthetic operator proof only; no customer data.")
doc.save()
PY

echo "Minting Drive-scoped token for service account ${SERVICE_ACCOUNT_EMAIL}"
BASE_TOKEN="$(gcloud auth print-access-token | tail -n1)"
DRIVE_TOKEN_RESPONSE="$(
  curl -sS -X POST \
    -H "Authorization: Bearer ${BASE_TOKEN}" \
    -H "Content-Type: application/json" \
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/${SERVICE_ACCOUNT_EMAIL}:generateAccessToken" \
    -d '{"scope":["https://www.googleapis.com/auth/drive"]}'
)"
DRIVE_TOKEN="$(echo "${DRIVE_TOKEN_RESPONSE}" | jq -r '.accessToken // empty')"
if [[ -z "${DRIVE_TOKEN}" ]]; then
  echo "Unable to mint Drive-scoped access token." >&2
  echo "${DRIVE_TOKEN_RESPONSE}" >&2
  exit 4
fi

echo "Uploading synthetic PDF to Drive input folder ${DRIVE_FOLDER_ID}"
UPLOAD_RESPONSE="$(
  curl -sS -X POST \
    -H "Authorization: Bearer ${DRIVE_TOKEN}" \
    -F "metadata={\"name\":\"${PDF_NAME}\",\"parents\":[\"${DRIVE_FOLDER_ID}\"]};type=application/json;charset=UTF-8" \
    -F "file=@${PDF_PATH};type=application/pdf" \
    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&supportsAllDrives=true&includeItemsFromAllDrives=true&fields=id,name,createdTime"
)"
DRIVE_FILE_ID="$(echo "${UPLOAD_RESPONSE}" | jq -r '.id // empty')"
DRIVE_FILE_NAME="$(echo "${UPLOAD_RESPONSE}" | jq -r '.name // empty')"
if [[ -z "${DRIVE_FILE_ID}" ]]; then
  echo "Drive upload failed." >&2
  echo "${UPLOAD_RESPONSE}" >&2
  exit 5
fi

echo "Triggering scheduler job ${SCHEDULER_JOB} once in ${REGION}"
gcloud scheduler jobs run "${SCHEDULER_JOB}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null

echo "Searching workflow execution for drive_file_id=${DRIVE_FILE_ID}"
DEADLINE=$((SECONDS + TIMEOUT_SECONDS))
WORKFLOW_EXECUTION=""
WORKFLOW_STATE=""
WORKFLOW_START=""
WORKFLOW_END=""
JOB_ID=""

while [[ ${SECONDS} -lt ${DEADLINE} ]]; do
  mapfile -t EXECUTION_NAMES < <(
    gcloud workflows executions list "${WORKFLOW_NAME}" \
      --location "${REGION}" \
      --project "${PROJECT_ID}" \
      --limit 20 \
      --format='value(name)'
  )

  for execution_name in "${EXECUTION_NAMES[@]}"; do
    execution_id="${execution_name##*/}"
    execution_json="$(
      gcloud workflows executions describe "${execution_id}" \
        --workflow "${WORKFLOW_NAME}" \
        --location "${REGION}" \
        --project "${PROJECT_ID}" \
        --format=json
    )"

    start_time="$(echo "${execution_json}" | jq -r '.startTime // empty')"
    if [[ -n "${start_time}" && "${start_time}" < "${RUN_START_TS}" ]]; then
      continue
    fi

    argument_raw="$(echo "${execution_json}" | jq -r '.argument // empty')"
    if [[ -z "${argument_raw}" ]]; then
      continue
    fi

    matches_drive_id="$(
      echo "${argument_raw}" | jq -r --arg drive_file_id "${DRIVE_FILE_ID}" '
        try (fromjson | ((.metadata.drive_file_id // "") == $drive_file_id)) catch false
      '
    )"
    if [[ "${matches_drive_id}" != "true" ]]; then
      continue
    fi

    WORKFLOW_EXECUTION="$(echo "${execution_json}" | jq -r '.name')"
    WORKFLOW_STATE="$(echo "${execution_json}" | jq -r '.state // empty')"
    WORKFLOW_START="$(echo "${execution_json}" | jq -r '.startTime // empty')"
    WORKFLOW_END="$(echo "${execution_json}" | jq -r '.endTime // empty')"
    JOB_ID="$(
      echo "${argument_raw}" | jq -r '
        try (fromjson | .job_id // empty) catch ""
      '
    )"
    break
  done

  if [[ -n "${WORKFLOW_EXECUTION}" && "${WORKFLOW_STATE}" == "SUCCEEDED" ]]; then
    break
  fi
  if [[ -n "${WORKFLOW_EXECUTION}" && "${WORKFLOW_STATE}" == "FAILED" ]]; then
    echo "Matched workflow execution failed: ${WORKFLOW_EXECUTION}" >&2
    exit 6
  fi

  sleep "${POLL_SECONDS}"
done

if [[ -z "${WORKFLOW_EXECUTION}" ]]; then
  echo "Timed out waiting for matching workflow execution." >&2
  exit 7
fi
if [[ "${WORKFLOW_STATE}" != "SUCCEEDED" ]]; then
  echo "Workflow execution did not succeed before timeout: ${WORKFLOW_EXECUTION} (${WORKFLOW_STATE})" >&2
  exit 8
fi
if [[ -z "${JOB_ID}" ]]; then
  echo "Matched execution missing job_id in argument payload." >&2
  exit 9
fi

SUMMARY_URI="gs://${OUTPUT_BUCKET}/summaries/${JOB_ID}.json"
PDF_URI="gs://${OUTPUT_BUCKET}/pdf/${JOB_ID}.pdf"

echo "Verifying output artifacts exist"
gcloud storage ls "${SUMMARY_URI}" "${PDF_URI}" >/dev/null

echo "SMOKE_E2E_OK"
echo "drive_file_id=${DRIVE_FILE_ID}"
echo "drive_file_name=${DRIVE_FILE_NAME}"
echo "workflow_execution=${WORKFLOW_EXECUTION}"
echo "workflow_start=${WORKFLOW_START}"
echo "workflow_end=${WORKFLOW_END}"
echo "job_id=${JOB_ID}"
echo "summary_uri=${SUMMARY_URI}"
echo "pdf_uri=${PDF_URI}"
