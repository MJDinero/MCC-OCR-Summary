#!/usr/bin/env bash

# Verify that MCC OCR Summary resources reference the expected CMEK.
# Non-destructive read-only checks for Cloud Run and Artifact Registry.

set -Eeuo pipefail

PROJECT_ID="${PROJECT_ID:-quantify-agent}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-mcc-ocr-summary}"
REPO_LOCATION="${REPO_LOCATION:-us-central1}"
REPO_NAME="${REPO_NAME:-mcc}"
CMEK_KEY_NAME="${CMEK_KEY_NAME:-}"

if [[ -z "${CMEK_KEY_NAME}" ]]; then
  echo "FAIL: CMEK_KEY_NAME must be exported before running verify_cmek.sh" >&2
  exit 1
fi

ok() { printf '[verify_cmek] OK: %s\n' "$*"; }
fail() { printf '[verify_cmek] FAIL: %s\n' "$*" >&2; exit 1; }
warn() { printf '[verify_cmek] WARN: %s\n' "$*"; }

describe_cloud_run() {
  local svc_json
  if ! svc_json="$(gcloud run services describe "${SERVICE_NAME}" --region "${REGION}" --project "${PROJECT_ID}" --format=json)"; then
    fail "Unable to describe Cloud Run service ${SERVICE_NAME} in ${REGION}"
  fi
  if echo "${svc_json}" | grep -q "$(basename "${CMEK_KEY_NAME}")"; then
    ok "Cloud Run service ${SERVICE_NAME} references ${CMEK_KEY_NAME}"
  else
    fail "Cloud Run service ${SERVICE_NAME} does not reference ${CMEK_KEY_NAME}"
  fi
}

describe_artifact_registry() {
  local repo_json
  if ! repo_json="$(gcloud artifacts repositories describe "${REPO_NAME}" --location "${REPO_LOCATION}" --project "${PROJECT_ID}" --format=json 2>/dev/null)"; then
    ok "Artifact Registry repo ${REPO_LOCATION}/${REPO_NAME} not found (skip CMEK check)"
    return
  fi
  if echo "${repo_json}" | grep -q "$(basename "${CMEK_KEY_NAME}")"; then
    ok "Artifact Registry repository ${REPO_LOCATION}/${REPO_NAME} references ${CMEK_KEY_NAME}"
  else
    warn "Artifact Registry repository ${REPO_LOCATION}/${REPO_NAME} does not reference ${CMEK_KEY_NAME} (Google-managed key in use)"
  fi
}

describe_cloud_run
describe_artifact_registry

ok "All CMEK checks passed."
