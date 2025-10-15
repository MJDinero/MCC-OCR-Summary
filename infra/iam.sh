#!/usr/bin/env bash
set -euo pipefail

# IAM bootstrap for the MCC OCR Summary pipeline (October 2025).
# Creates stage-scoped service accounts that align with the event-driven
# pipeline (OCR → Summariser → Storage) and applies least-privilege
# bindings scoped to specific buckets/datasets. Cloud Build is only
# permitted to impersonate these identities when deploying.
#
# Required environment:
#   PROJECT_ID                   Target Google Cloud project
#   REGION                       Default region for Cloud Run / Workflows
#   INTAKE_BUCKET                GCS bucket for intake PDFs
#   OUTPUT_BUCKET                GCS bucket for OCR outputs / intermediates
#   SUMMARY_BUCKET               GCS bucket for final summaries / PDFs
#   STATE_BUCKET                 Pipeline state bucket (GCS)
#   SUMMARY_BIGQUERY_DATASET     BigQuery dataset for summary table
#
# Optional overrides:
#   OCR_SA_NAME            (default: mcc-ocr-sa)
#   SUMMARISER_SA_NAME     (default: mcc-summariser-sa)
#   STORAGE_SA_NAME        (default: mcc-storage-sa)
#   WORKFLOW_SA_NAME       (default: mcc-workflow-sa)

PROJECT_ID="${PROJECT_ID:?PROJECT_ID is required}"
REGION="${REGION:?REGION is required}"
INTAKE_BUCKET="${INTAKE_BUCKET:?INTAKE_BUCKET is required}"
OUTPUT_BUCKET="${OUTPUT_BUCKET:?OUTPUT_BUCKET is required}"
SUMMARY_BUCKET="${SUMMARY_BUCKET:?SUMMARY_BUCKET is required}"
STATE_BUCKET="${STATE_BUCKET:?STATE_BUCKET is required}"
SUMMARY_BIGQUERY_DATASET="${SUMMARY_BIGQUERY_DATASET:?SUMMARY_BIGQUERY_DATASET is required}"

OCR_SA_NAME="${OCR_SA_NAME:-mcc-ocr-sa}"
SUMMARISER_SA_NAME="${SUMMARISER_SA_NAME:-mcc-summariser-sa}"
STORAGE_SA_NAME="${STORAGE_SA_NAME:-mcc-storage-sa}"
WORKFLOW_SA_NAME="${WORKFLOW_SA_NAME:-mcc-workflow-sa}"

create_sa() {
  local sa_name="${1}"
  local display="${2}"
  if gcloud iam service-accounts describe "${sa_name}@${PROJECT_ID}.iam.gserviceaccount.com" >/dev/null 2>&1; then
    echo "Service account ${sa_name} already exists"
  else
    gcloud iam service-accounts create "${sa_name}" \
      --project "${PROJECT_ID}" \
      --display-name "${display}"
  fi
}

echo "⏳ Creating service accounts"
create_sa "${OCR_SA_NAME}" "MCC OCR service"
create_sa "${SUMMARISER_SA_NAME}" "MCC Summariser service"
create_sa "${STORAGE_SA_NAME}" "MCC Storage service"
create_sa "${WORKFLOW_SA_NAME}" "MCC Workflow Orchestrator"

OCR_SA="${OCR_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SUMMARISER_SA="${SUMMARISER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
STORAGE_SA="${STORAGE_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKFLOW_SA="${WORKFLOW_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
CLOUDBUILD_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"

grant_project_role() {
  local member="${1}"
  local role="${2}"
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${member}" \
    --role="${role}" \
    --quiet
}

grant_bucket_role() {
  local bucket="${1}"
  local member="${2}"
  local role="${3}"
  gcloud storage buckets add-iam-policy-binding "gs://${bucket}" \
    --member="serviceAccount:${member}" \
    --role="${role}" \
    --quiet
}

grant_dataset_role() {
  local dataset="${1}"
  local member="${2}"
  local role="${3}"
  gcloud bigquery datasets add-iam-policy-binding "${PROJECT_ID}:${dataset}" \
    --member="serviceAccount:${member}" \
    --role="${role}" \
    --quiet
}

grant_state_bucket_role() {
  grant_bucket_role "${STATE_BUCKET}" "${1}" "${2}"
}

echo "🔐 Granting principle of least privilege roles"

# OCR service account --------------------------------------------------------
grant_project_role "${OCR_SA}" "roles/documentai.apiUser"
grant_project_role "${OCR_SA}" "roles/logging.logWriter"
grant_project_role "${OCR_SA}" "roles/monitoring.metricWriter"
grant_bucket_role "${INTAKE_BUCKET}" "${OCR_SA}" "roles/storage.objectViewer"
grant_bucket_role "${OUTPUT_BUCKET}" "${OCR_SA}" "roles/storage.objectCreator"
grant_state_bucket_role "${OCR_SA}" "roles/storage.objectAdmin"

# Summariser service account -------------------------------------------------
grant_project_role "${SUMMARISER_SA}" "roles/secretmanager.secretAccessor"
grant_project_role "${SUMMARISER_SA}" "roles/logging.logWriter"
grant_project_role "${SUMMARISER_SA}" "roles/monitoring.metricWriter"
grant_bucket_role "${OUTPUT_BUCKET}" "${SUMMARISER_SA}" "roles/storage.objectViewer"
grant_bucket_role "${SUMMARY_BUCKET}" "${SUMMARISER_SA}" "roles/storage.objectCreator"
grant_state_bucket_role "${SUMMARISER_SA}" "roles/storage.objectAdmin"

# Storage service account ----------------------------------------------------
grant_project_role "${STORAGE_SA}" "roles/logging.logWriter"
grant_project_role "${STORAGE_SA}" "roles/monitoring.metricWriter"
grant_bucket_role "${SUMMARY_BUCKET}" "${STORAGE_SA}" "roles/storage.objectAdmin"
grant_dataset_role "${SUMMARY_BIGQUERY_DATASET}" "${STORAGE_SA}" "roles/bigquery.dataEditor"

# Workflow orchestrator ------------------------------------------------------
grant_project_role "${WORKFLOW_SA}" "roles/run.invoker"
grant_project_role "${WORKFLOW_SA}" "roles/documentai.apiUser"
grant_project_role "${WORKFLOW_SA}" "roles/pubsub.publisher"
grant_bucket_role "${INTAKE_BUCKET}" "${WORKFLOW_SA}" "roles/storage.objectViewer"
grant_bucket_role "${OUTPUT_BUCKET}" "${WORKFLOW_SA}" "roles/storage.objectCreator"

# Cloud Build impersonation --------------------------------------------------
for sa in "${OCR_SA}" "${SUMMARISER_SA}" "${STORAGE_SA}"; do
  gcloud iam service-accounts add-iam-policy-binding "${sa}" \
    --member="serviceAccount:${CLOUDBUILD_SA}" \
    --role="roles/iam.serviceAccountUser" \
    --quiet
done

echo "✅ IAM bootstrap complete. Attach these identities to Cloud Run services/jobs:"
echo "   - OCR service:        ${OCR_SA}"
echo "   - Summariser service: ${SUMMARISER_SA}"
echo "   - Storage service:    ${STORAGE_SA}"
echo "   - Workflows:          ${WORKFLOW_SA}"
