#!/usr/bin/env bash
# Creates the GCP project from scratch.
# Idempotent: safe to re-run.
# Requires: gcloud CLI authenticated with an account that has billing admin + project creator roles.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.env.infra" 2>/dev/null || { echo "ERROR: Copy .env.infra.example to .env.infra and fill in values."; exit 1; }

: "${BILLING_ACCOUNT_ID:?Set BILLING_ACCOUNT_ID in .env.infra}"
: "${PROJECT_ID:?Set PROJECT_ID in .env.infra}"
: "${REGION:=us-central1}"

echo "==> Creating project: ${PROJECT_ID}"
gcloud projects create "${PROJECT_ID}" --name="Bluesky Feed Consumer" 2>/dev/null || echo "    (project already exists)"

echo "==> Linking billing account"
gcloud billing projects link "${PROJECT_ID}" --billing-account="${BILLING_ACCOUNT_ID}"

echo "==> Setting default project"
gcloud config set project "${PROJECT_ID}"

echo "==> Enabling required APIs"
gcloud services enable \
    compute.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    dns.googleapis.com \
    monitoring.googleapis.com \
    logging.googleapis.com \
    --project="${PROJECT_ID}"

echo ""
echo "==> Project ${PROJECT_ID} is ready."
echo "    Next: run ./setup.sh to create infrastructure."
