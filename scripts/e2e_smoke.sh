#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/e2e_smoke.sh [--dry-run] [--project-id ID] [--region REGION]
                            [--workflow-name NAME] [--scheduler-job NAME]
                            [--drive-folder-id ID] [--drive-output-folder-id ID]
                            [--service-account EMAIL]
                            [--state-bucket BUCKET] [--state-prefix PREFIX]
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
  echo "python"
}

build_summary_uri() {
  printf 'gs://%s/summaries/%s.json\n' "$1" "$2"
}

build_pdf_uri() {
  printf 'gs://%s/pdf/%s.pdf\n' "$1" "$2"
}

build_report_name() {
  printf 'summary-%s.pdf\n' "$1"
}

build_state_uri() {
  printf 'gs://%s/%s/jobs/%s.json\n' "$1" "$2" "$3"
}

query_drive_output_files() {
  local drive_token="$1"
  local folder_id="$2"
  local created_after="$3"
  local report_name="$4"

  curl -sS --get \
    -H "Authorization: Bearer ${drive_token}" \
    --data-urlencode "q='${folder_id}' in parents and name='${report_name}' and mimeType='application/pdf' and trashed=false and createdTime > '${created_after}'" \
    --data-urlencode "includeItemsFromAllDrives=true" \
    --data-urlencode "supportsAllDrives=true" \
    --data-urlencode "orderBy=createdTime desc" \
    --data-urlencode "pageSize=10" \
    --data-urlencode "fields=files(id,name,createdTime)" \
    "https://www.googleapis.com/drive/v3/files"
}

workflow_argument_matches_drive_file_id() {
  local argument_raw="$1"
  local drive_file_id="$2"

  echo "${argument_raw}" | jq -r --arg drive_file_id "${drive_file_id}" '
    try (
      (if type == "string" then fromjson else . end)
      | ((.drive_file_id // .metadata.drive_file_id // "") == $drive_file_id)
    ) catch false
  '
}

extract_job_id_from_workflow_argument() {
  local argument_raw="$1"

  echo "${argument_raw}" | jq -r '
    try ((if type == "string" then fromjson else . end) | .job_id // empty) catch ""
  '
}

extract_single_drive_output() {
  local response="$1"
  local file_count

  file_count="$(echo "${response}" | jq -r '.files | length')"
  if [[ "${file_count}" == "0" ]]; then
    return 1
  fi
  if [[ "${file_count}" != "1" ]]; then
    echo "Drive output query was ambiguous (${file_count} files)." >&2
    echo "${response}" | jq -c '.files[] | {id, name, createdTime}' >&2
    return 2
  fi

  echo "${response}" | jq -r '.files[0] | [.id, .name, .createdTime] | @tsv'
}

validate_refactored_summary_json() {
  local summary_json="$1"

  if ! echo "${summary_json}" | jq -e '
    type == "object"
    and (.schema_version | type == "string" and length > 0)
    and (.sections | type == "array" and length > 0)
    and (has("Medical Summary") | not)
    and all(.sections[]; (
      (.slug | type == "string" and length > 0)
      and (.title | type == "string" and length > 0)
      and (.content | type == "string")
      and (.ordinal | type == "number")
    ))
  ' >/dev/null; then
    echo "Summary JSON does not match the refactored contract." >&2
    return 1
  fi

  if echo "${summary_json}" | jq -e '
    [.. | strings | select(test("Document processed in [0-9]+ chunk\\(s\\)"; "i"))]
    | length > 0
  ' >/dev/null; then
    echo "Summary JSON still contains legacy chunk marker text." >&2
    return 1
  fi
}

extract_summary_contract_metrics() {
  local summary_json="$1"

  echo "${summary_json}" | jq -r '
    [
      .schema_version,
      (.sections | length)
    ] | @tsv
  '
}

print_success_summary() {
  local drive_input_file_id="$1"
  local drive_input_file_name="$2"
  local workflow_execution="$3"
  local workflow_state="$4"
  local workflow_start="$5"
  local workflow_end="$6"
  local job_id="$7"
  local summary_uri="$8"
  local pdf_uri="$9"
  local state_uri="${10}"
  local summary_schema_version="${11}"
  local summary_sections="${12}"
  local report_file_id="${13}"
  local drive_output_file_name="${14}"
  local drive_output_created_time="${15}"

  cat <<EOF
status=SMOKE_E2E_OK
drive_input_file_id=${drive_input_file_id}
drive_input_file_name=${drive_input_file_name}
workflow_execution=${workflow_execution}
workflow_state=${workflow_state}
workflow_start=${workflow_start}
workflow_end=${workflow_end}
job_id=${job_id}
summary_uri=${summary_uri}
pdf_uri=${pdf_uri}
state_uri=${state_uri}
summary_schema_version=${summary_schema_version}
summary_sections=${summary_sections}
report_file_id=${report_file_id}
drive_output_file_id=${report_file_id}
drive_output_file_name=${drive_output_file_name}
drive_output_created_time=${drive_output_created_time}
EOF
}

cleanup_tmp_dir() {
  local tmp_dir="${E2E_SMOKE_TMP_DIR:-}"
  if [[ -n "${tmp_dir}" ]]; then
    rm -rf "${tmp_dir}"
  fi
}

main() {
  local PROJECT_ID="quantify-agent"
  local REGION="us-central1"
  local WORKFLOW_NAME="docai-pipeline"
  local SCHEDULER_JOB="mcc-drive-poller"
  local DRIVE_FOLDER_ID="1eyMO0126VfLBK3bBQEpWlVOL6tWxriCE"
  local DRIVE_OUTPUT_FOLDER_ID="130jJzsl3OBzMD8weGfBOaXikfEnD2KVg"
  local SERVICE_ACCOUNT_EMAIL="mcc-orch-sa@quantify-agent.iam.gserviceaccount.com"
  local OUTPUT_BUCKET="mcc-output"
  local STATE_BUCKET="mcc-state-quantify-agent-us-central1-322786"
  local STATE_PREFIX="pipeline-state"
  local TIMEOUT_SECONDS=600
  local POLL_SECONDS=10
  local DRY_RUN=0

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
      --drive-output-folder-id)
        DRIVE_OUTPUT_FOLDER_ID="$2"
        shift 2
        ;;
      --service-account)
        SERVICE_ACCOUNT_EMAIL="$2"
        shift 2
        ;;
      --state-bucket)
        STATE_BUCKET="$2"
        shift 2
        ;;
      --state-prefix)
        STATE_PREFIX="$2"
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

  local RUN_TS
  local RUN_START_TS
  local TMP_DIR
  local PDF_PATH
  local PDF_NAME
  local PYTHON_BIN

  RUN_TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
  RUN_START_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  TMP_DIR="$(mktemp -d)"
  E2E_SMOKE_TMP_DIR="${TMP_DIR}"
  trap cleanup_tmp_dir EXIT

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
  - Upload to Drive input folder ${DRIVE_FOLDER_ID} via files.create multipart
  - Trigger scheduler once:
      gcloud scheduler jobs run ${SCHEDULER_JOB} --location ${REGION} --project ${PROJECT_ID}
  - Poll workflow executions:
      gcloud workflows executions list ${WORKFLOW_NAME} --location ${REGION} --project ${PROJECT_ID}
      gcloud workflows executions describe <execution-id> --workflow ${WORKFLOW_NAME} --location ${REGION} --project ${PROJECT_ID}
  - Verify artifacts:
      $(build_summary_uri "${OUTPUT_BUCKET}" "<job_id>")
      $(build_pdf_uri "${OUTPUT_BUCKET}" "<job_id>")
      $(build_state_uri "${STATE_BUCKET}" "${STATE_PREFIX}" "<job_id>")
  - Validate summary JSON uses the refactored contract:
      gcloud storage cat $(build_summary_uri "${OUTPUT_BUCKET}" "<job_id>") | jq .
  - Verify Drive output in folder ${DRIVE_OUTPUT_FOLDER_ID} created after ${RUN_START_TS}
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
  local BASE_TOKEN
  local DRIVE_TOKEN_RESPONSE
  local DRIVE_TOKEN
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
  local UPLOAD_RESPONSE
  local DRIVE_FILE_ID
  local DRIVE_FILE_NAME
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
  local DEADLINE
  local WORKFLOW_EXECUTION=""
  local WORKFLOW_STATE=""
  local WORKFLOW_START=""
  local WORKFLOW_END=""
  local JOB_ID=""
  DEADLINE=$((SECONDS + TIMEOUT_SECONDS))

  while [[ ${SECONDS} -lt ${DEADLINE} ]]; do
    local EXECUTION_NAMES=()
    local execution_name
    while IFS= read -r execution_name; do
      if [[ -n "${execution_name}" ]]; then
        EXECUTION_NAMES+=("${execution_name}")
      fi
    done < <(
      gcloud workflows executions list "${WORKFLOW_NAME}" \
        --location "${REGION}" \
        --project "${PROJECT_ID}" \
        --limit 20 \
        --format='value(name)'
    )

    for execution_name in "${EXECUTION_NAMES[@]}"; do
      local execution_id
      local execution_json
      local start_time
      local argument_raw
      local matches_drive_id

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

      matches_drive_id="$(workflow_argument_matches_drive_file_id "${argument_raw}" "${DRIVE_FILE_ID}")"
      if [[ "${matches_drive_id}" != "true" ]]; then
        continue
      fi

      WORKFLOW_EXECUTION="$(echo "${execution_json}" | jq -r '.name')"
      WORKFLOW_STATE="$(echo "${execution_json}" | jq -r '.state // empty')"
      WORKFLOW_START="$(echo "${execution_json}" | jq -r '.startTime // empty')"
      WORKFLOW_END="$(echo "${execution_json}" | jq -r '.endTime // empty')"
      JOB_ID="$(extract_job_id_from_workflow_argument "${argument_raw}")"
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

  local SUMMARY_URI
  local PDF_URI
  local STATE_URI
  local SUMMARY_JSON
  local SUMMARY_SCHEMA_VERSION
  local SUMMARY_SECTIONS
  SUMMARY_URI="$(build_summary_uri "${OUTPUT_BUCKET}" "${JOB_ID}")"
  PDF_URI="$(build_pdf_uri "${OUTPUT_BUCKET}" "${JOB_ID}")"
  STATE_URI="$(build_state_uri "${STATE_BUCKET}" "${STATE_PREFIX}" "${JOB_ID}")"

  echo "Verifying output artifacts exist"
  gcloud storage ls "${SUMMARY_URI}" "${PDF_URI}" >/dev/null

  echo "Validating refactored summary JSON structure"
  SUMMARY_JSON="$(gcloud storage cat "${SUMMARY_URI}")"
  if ! validate_refactored_summary_json "${SUMMARY_JSON}"; then
    exit 12
  fi
  IFS=$'\t' read -r SUMMARY_SCHEMA_VERSION SUMMARY_SECTIONS <<< "$(extract_summary_contract_metrics "${SUMMARY_JSON}")"

  echo "Searching Drive output folder ${DRIVE_OUTPUT_FOLDER_ID}"
  local DRIVE_OUTPUT_FILE_ID=""
  local DRIVE_OUTPUT_FILE_NAME=""
  local DRIVE_OUTPUT_CREATED_TIME=""
  local EXPECTED_REPORT_NAME
  EXPECTED_REPORT_NAME="$(build_report_name "${JOB_ID}")"

  while [[ ${SECONDS} -lt ${DEADLINE} ]]; do
    local drive_output_response
    local drive_output_fields
    local drive_status

    drive_output_response="$(
      query_drive_output_files "${DRIVE_TOKEN}" "${DRIVE_OUTPUT_FOLDER_ID}" "${RUN_START_TS}" "${EXPECTED_REPORT_NAME}"
    )"
    if drive_output_fields="$(extract_single_drive_output "${drive_output_response}")"; then
      IFS=$'\t' read -r DRIVE_OUTPUT_FILE_ID DRIVE_OUTPUT_FILE_NAME DRIVE_OUTPUT_CREATED_TIME <<< "${drive_output_fields}"
      break
    fi
    drive_status=$?
    if [[ "${drive_status}" == "2" ]]; then
      exit 10
    fi

    sleep "${POLL_SECONDS}"
  done

  if [[ -z "${DRIVE_OUTPUT_FILE_ID}" ]]; then
    echo "Timed out waiting for unique Drive output file in folder ${DRIVE_OUTPUT_FOLDER_ID}." >&2
    exit 11
  fi

  print_success_summary \
    "${DRIVE_FILE_ID}" \
    "${DRIVE_FILE_NAME}" \
    "${WORKFLOW_EXECUTION}" \
    "${WORKFLOW_STATE}" \
    "${WORKFLOW_START}" \
    "${WORKFLOW_END}" \
    "${JOB_ID}" \
    "${SUMMARY_URI}" \
    "${PDF_URI}" \
    "${STATE_URI}" \
    "${SUMMARY_SCHEMA_VERSION}" \
    "${SUMMARY_SECTIONS}" \
    "${DRIVE_OUTPUT_FILE_ID}" \
    "${DRIVE_OUTPUT_FILE_NAME}" \
    "${DRIVE_OUTPUT_CREATED_TIME}"
}

if [[ "${E2E_SMOKE_SOURCE_ONLY:-0}" != "1" ]]; then
  main "$@"
fi
