#!/usr/bin/env bash
set -euo pipefail

# IAM bootstrap for the MCC OCR Summary pipeline (October 2025).
# Creates dedicated service accounts for each stage of the event-driven
# pipeline and applies least-privilege bindings based on Google Cloud best
# practices. All identities rely on Workload Identity Federation – no
# user-managed keys are generated.
#
# Required environment:
#   PROJECT_ID       Target Google Cloud project
#   REGION           Default region for Cloud Run/Workflows
#
# Optional overrides:
#   INTAKE_SA_NAME        (default: mcc-intake-sa)
#   WORKFLOW_SA_NAME      (default: mcc-workflow-sa)
#   DOCAI_SA_NAME         (default: mcc-docai-sa)
#   SUMMARISER_SA_NAME    (default: mcc-summariser-sa)
#   PDF_SA_NAME           (default: mcc-pdf-writer-sa)

PROJECT_ID="${PROJECT_ID:?PROJECT_ID is required}"
REGION="${REGION:?REGION is required}"

INTAKE_SA_NAME="${INTAKE_SA_NAME:-mcc-intake-sa}"
WORKFLOW_SA_NAME="${WORKFLOW_SA_NAME:-mcc-workflow-sa}"
DOCAI_SA_NAME="${DOCAI_SA_NAME:-mcc-docai-sa}"
SUMMARISER_SA_NAME="${SUMMARISER_SA_NAME:-mcc-summariser-sa}"
PDF_SA_NAME="${PDF_SA_NAME:-mcc-pdf-writer-sa}"

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
create_sa "${INTAKE_SA_NAME}" "MCC Intake (Cloud Run) service account"
create_sa "${WORKFLOW_SA_NAME}" "MCC Workflow Orchestrator"
create_sa "${DOCAI_SA_NAME}" "MCC Document AI processors"
create_sa "${SUMMARISER_SA_NAME}" "MCC Summariser Cloud Run Job"
create_sa "${PDF_SA_NAME}" "MCC PDF writer Cloud Run Job"

INTAKE_SA="${INTAKE_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKFLOW_SA="${WORKFLOW_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
DOCAI_SA="${DOCAI_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SUMMARISER_SA="${SUMMARISER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PDF_SA="${PDF_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "🔐 Granting principle of least privilege roles"

# Ingestion service (Cloud Run)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INTAKE_SA}" \
  --role="roles/run.invoker"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INTAKE_SA}" \
  --role="roles/workflows.invoker"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INTAKE_SA}" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INTAKE_SA}" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${INTAKE_SA}" \
  --role="roles/monitoring.metricWriter"

# Workflow orchestrator
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${WORKFLOW_SA}" \
  --role="roles/run.invoker"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${WORKFLOW_SA}" \
  --role="roles/documentai.apiUser"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${WORKFLOW_SA}" \
  --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${WORKFLOW_SA}" \
  --role="roles/storage.objectCreator"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${WORKFLOW_SA}" \
  --role="roles/pubsub.publisher"

# Document AI processors (long-running operations)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DOCAI_SA}" \
  --role="roles/documentai.apiUser"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${DOCAI_SA}" \
  --role="roles/storage.objectCreator"

# Summariser job (Cloud Run Job)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SUMMARISER_SA}" \
  --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SUMMARISER_SA}" \
  --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SUMMARISER_SA}" \
  --role="roles/storage.objectCreator"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SUMMARISER_SA}" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SUMMARISER_SA}" \
  --role="roles/monitoring.metricWriter"

# PDF writer job (Cloud Run Job)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PDF_SA}" \
  --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PDF_SA}" \
  --role="roles/storage.objectCreator"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PDF_SA}" \
  --role="roles/logging.logWriter"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PDF_SA}" \
  --role="roles/monitoring.metricWriter"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${PDF_SA}" \
  --role="roles/iam.serviceAccountTokenCreator"

echo "✅ IAM bootstrap complete. Configure Workload Identity by attaching these service accounts to Cloud Run services/jobs and Workflows:"
echo "   - Ingestion: ${INTAKE_SA}"
echo "   - Workflows: ${WORKFLOW_SA}"
echo "   - DocAI operations: ${DOCAI_SA}"
echo "   - Summariser job: ${SUMMARISER_SA}"
echo "   - PDF writer job: ${PDF_SA}"
