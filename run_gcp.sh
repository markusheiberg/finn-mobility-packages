#!/bin/bash
# Deploy and run the scraper as a Cloud Run Job.
# Usage: ./run_gcp.sh <project-id> <gcs-bucket> [finn|blocket]
#
# Prerequisites:
#   gcloud auth login
#   gcloud auth configure-docker <region>-docker.pkg.dev
#   Artifact Registry repo created (or use gcr.io)

set -e

PROJECT="${1:?Usage: $0 <project-id> <gcs-bucket> [finn|blocket]}"
BUCKET="${2:?Usage: $0 <project-id> <gcs-bucket> [finn|blocket]}"
SCRIPT="${3:-finn}"
REGION="europe-west1"
JOB_NAME="mobility-scraper-${SCRIPT}"
IMAGE="gcr.io/${PROJECT}/${JOB_NAME}:latest"

echo "==> Building image: $IMAGE"
gcloud builds submit \
  --project "$PROJECT" \
  --tag "$IMAGE" \
  .

echo "==> Creating/updating Cloud Run Job: $JOB_NAME"
gcloud run jobs create "$JOB_NAME" \
  --project "$PROJECT" \
  --region "$REGION" \
  --image "$IMAGE" \
  --set-env-vars "SCRIPT=${SCRIPT},GCS_BUCKET=${BUCKET}" \
  --memory 512Mi \
  --task-timeout 3600 \
  2>/dev/null \
|| gcloud run jobs update "$JOB_NAME" \
  --project "$PROJECT" \
  --region "$REGION" \
  --image "$IMAGE" \
  --set-env-vars "SCRIPT=${SCRIPT},GCS_BUCKET=${BUCKET}" \
  --memory 512Mi \
  --task-timeout 3600

echo "==> Running job (streaming logs)..."
gcloud run jobs execute "$JOB_NAME" \
  --project "$PROJECT" \
  --region "$REGION" \
  --wait

echo ""
echo "==> Output files:"
gsutil ls "gs://${BUCKET}/${SCRIPT}_mobility*" 2>/dev/null || true

echo ""
echo "==> Download locally:"
echo "    gsutil cp 'gs://${BUCKET}/${SCRIPT}_mobility_packages_summary.csv' ."
echo "    gsutil cp 'gs://${BUCKET}/${SCRIPT}_mobility_debug.csv' ."
