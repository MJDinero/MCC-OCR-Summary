#!/usr/bin/env bash

# Verification utility to assert CMEK enforcement across GCS and BigQuery.
# Requires gcloud SDK, gsutil, and bq CLI. All commands are read-only.

set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID env var required}"
: "${CMEK_KEY_NAME:?CMEK_KEY_NAME env var required}"
: "${INTAKE_BUCKET:?INTAKE_BUCKET env var required}"
: "${OUTPUT_BUCKET:?OUTPUT_BUCKET env var required}"
: "${SUMMARY_BUCKET:?SUMMARY_BUCKET env var required}"
: "${SUMMARY_BIGQUERY_DATASET:?SUMMARY_BIGQUERY_DATASET env var required}"
: "${SUMMARY_BIGQUERY_TABLE:?SUMMARY_BIGQUERY_TABLE env var required}"

log() {
  printf '[verify_cmek] %s\n' "$*"
}

check_bucket() {
  local bucket=$1
  log "Checking kmsKeyName on bucket: ${bucket}"
  gsutil stat "gs://${bucket}/" 2>/dev/null | grep "kmsKeyName: ${CMEK_KEY_NAME}" >/dev/null
}

check_bigquery() {
  local dataset=$1
  local table=$2
  log "Checking BigQuery table encryption: ${dataset}.${table}"
  local kms
  kms="$(bq show --project_id="${PROJECT_ID}" --format=prettyjson "${PROJECT_ID}:${dataset}.${table}" | \
    python3 -c 'import json,sys; data=json.load(sys.stdin); print((data.get("encryptionConfiguration") or {}).get("kmsKeyName",""))')"
  if [[ "${kms}" != "${CMEK_KEY_NAME}" ]]; then
    log "Expected ${CMEK_KEY_NAME} but saw ${kms:-<none>}"
    return 1
  fi
}

check_bucket "${INTAKE_BUCKET}"
check_bucket "${OUTPUT_BUCKET}"
check_bucket "${SUMMARY_BUCKET}"

check_bigquery "${SUMMARY_BIGQUERY_DATASET}" "${SUMMARY_BIGQUERY_TABLE}"

log "All CMEK checks passed."
