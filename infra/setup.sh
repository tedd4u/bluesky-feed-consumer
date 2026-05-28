#!/usr/bin/env bash
# Creates all infrastructure within the GCP project.
# Idempotent: safe to re-run.
# Run create-project.sh first.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib-log.sh"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

source "${SCRIPT_DIR}/.env.infra" 2>/dev/null || { echo "ERROR: .env.infra not found."; exit 1; }
source "${REPO_ROOT}/.env" 2>/dev/null || { echo "ERROR: .env not found (needed for API keys)."; exit 1; }

: "${PROJECT_ID:?Set PROJECT_ID in .env.infra}"
: "${REGION:=us-central1}"
: "${ZONE:=us-central1-a}"
: "${DB_PASSWORD:?Set DB_PASSWORD in .env.infra}"
: "${BSKY_SERVICE_API_KEY:?Set BSKY_SERVICE_API_KEY in .env}"
: "${BSKY_ANTHROPIC_API_KEY:?Set BSKY_ANTHROPIC_API_KEY in .env}"

gcloud config set project "${PROJECT_ID}"

# --- Secret Manager ---
echo "==> Creating secrets in Secret Manager"
for SECRET in bsky-api-key bsky-anthropic-api-key bsky-db-password; do
    gcloud secrets create "${SECRET}" --replication-policy=automatic 2>/dev/null || true
done

echo "${BSKY_SERVICE_API_KEY}" | gcloud secrets versions add bsky-api-key --data-file=-
echo "${BSKY_ANTHROPIC_API_KEY}" | gcloud secrets versions add bsky-anthropic-api-key --data-file=-
echo "${DB_PASSWORD}" | gcloud secrets versions add bsky-db-password --data-file=-

# --- VPC Peering for Cloud SQL private IP ---
echo "==> Setting up private services access (VPC peering)"
gcloud compute addresses describe google-managed-services-default --global --project="${PROJECT_ID}" &>/dev/null || \
gcloud compute addresses create google-managed-services-default \
    --global \
    --purpose=VPC_PEERING \
    --prefix-length=16 \
    --network=default \
    --project="${PROJECT_ID}"

gcloud services vpc-peerings list --network=default --project="${PROJECT_ID}" 2>/dev/null | grep -q servicenetworking.googleapis.com || \
gcloud services vpc-peerings connect \
    --service=servicenetworking.googleapis.com \
    --ranges=google-managed-services-default \
    --network=default \
    --project="${PROJECT_ID}"

# --- Cloud SQL ---
echo "==> Creating Cloud SQL instance"
gcloud sql instances describe bsky-db --project="${PROJECT_ID}" &>/dev/null || \
gcloud sql instances create bsky-db \
    --database-version=POSTGRES_16 \
    --edition=ENTERPRISE \
    --tier=db-f1-micro \
    --region="${REGION}" \
    --storage-size=10GB \
    --storage-auto-increase \
    --no-assign-ip \
    --network=default \
    --availability-type=zonal \
    --project="${PROJECT_ID}"

echo "==> Setting database user password"
gcloud sql users set-password postgres \
    --instance=bsky-db \
    --password="${DB_PASSWORD}" \
    --project="${PROJECT_ID}" 2>/dev/null || true

echo "==> Creating database"
gcloud sql databases create bsky \
    --instance=bsky-db \
    --project="${PROJECT_ID}" 2>/dev/null || true

# Get the private IP for connection string
DB_IP=$(gcloud sql instances describe bsky-db --format='value(ipAddresses[0].ipAddress)' --project="${PROJECT_ID}")
echo "    Cloud SQL private IP: ${DB_IP}"

# --- Static External IP ---
echo "==> Reserving static external IP"
gcloud compute addresses describe bsky-server-ip --region="${REGION}" --project="${PROJECT_ID}" &>/dev/null || \
gcloud compute addresses create bsky-server-ip --region="${REGION}" --project="${PROJECT_ID}"

CE_STATIC_IP=$(gcloud compute addresses describe bsky-server-ip --region="${REGION}" --format='value(address)' --project="${PROJECT_ID}")
echo "    Static IP: ${CE_STATIC_IP}"

# --- Compute Engine ---
echo "==> Creating CE instance"
if gcloud compute instances describe bsky-server --zone="${ZONE}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "    Instance already exists."
    # Ensure the static IP is attached (handles migration from ephemeral IP)
    CURRENT_IP=$(gcloud compute instances describe bsky-server --zone="${ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' --project="${PROJECT_ID}")
    if [[ "${CURRENT_IP}" != "${CE_STATIC_IP}" ]]; then
        echo "    Attaching static IP (swapping from ${CURRENT_IP}, brief interruption)..."
        ACCESS_CONFIG_NAME=$(gcloud compute instances describe bsky-server --zone="${ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].name)' --project="${PROJECT_ID}")
        gcloud compute instances delete-access-config bsky-server --zone="${ZONE}" --access-config-name="${ACCESS_CONFIG_NAME}" --project="${PROJECT_ID}"
        gcloud compute instances add-access-config bsky-server --zone="${ZONE}" --address="${CE_STATIC_IP}" --access-config-name="${ACCESS_CONFIG_NAME}" --project="${PROJECT_ID}"
        echo "    Static IP attached."
    fi
else
    gcloud compute instances create bsky-server \
        --zone="${ZONE}" \
        --machine-type=e2-small \
        --image-family=debian-12 \
        --image-project=debian-cloud \
        --boot-disk-size=20GB \
        --scopes=cloud-platform \
        --tags=bsky-server \
        --address=bsky-server-ip \
        --metadata=startup-script='#!/bin/bash
# Install Python 3.12+ and uv
apt-get update && apt-get install -y python3 python3-venv git
curl -LsSf https://astral.sh/uv/install.sh | sh
' \
        --project="${PROJECT_ID}"
fi

# --- Firewall ---
echo "==> Creating firewall rules"
gcloud compute firewall-rules describe allow-bsky-api --project="${PROJECT_ID}" &>/dev/null || \
gcloud compute firewall-rules create allow-bsky-api \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:8000 \
    --target-tags=bsky-server \
    --source-ranges=0.0.0.0/0 \
    --project="${PROJECT_ID}"

echo "==> Creating firewall rule for HTTPS (443) and HTTP (80, for ACME challenges)"
gcloud compute firewall-rules describe allow-bsky-https --project="${PROJECT_ID}" &>/dev/null || \
gcloud compute firewall-rules create allow-bsky-https \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:80,tcp:443 \
    --target-tags=bsky-server \
    --source-ranges=0.0.0.0/0 \
    --project="${PROJECT_ID}"

# --- DNS Zone (optional) ---
if [[ -n "${DNS_SUBDOMAIN:-}" ]]; then
    DNS_ZONE_NAME="bsky-zone"
    echo "==> Creating Cloud DNS zone for ${DNS_SUBDOMAIN}"
    gcloud dns managed-zones describe "${DNS_ZONE_NAME}" --project="${PROJECT_ID}" &>/dev/null || \
    gcloud dns managed-zones create "${DNS_ZONE_NAME}" \
        --dns-name="${DNS_SUBDOMAIN}." \
        --description="Bluesky Feed Consumer subdomain" \
        --project="${PROJECT_ID}"

    # Create or update A record using the static IP
    if gcloud dns record-sets describe "${DNS_SUBDOMAIN}." --zone="${DNS_ZONE_NAME}" --type=A --project="${PROJECT_ID}" &>/dev/null; then
        gcloud dns record-sets update "${DNS_SUBDOMAIN}." \
            --zone="${DNS_ZONE_NAME}" \
            --type=A \
            --ttl=300 \
            --rrdatas="${CE_STATIC_IP}" \
            --project="${PROJECT_ID}"
    else
        gcloud dns record-sets create "${DNS_SUBDOMAIN}." \
            --zone="${DNS_ZONE_NAME}" \
            --type=A \
            --ttl=300 \
            --rrdatas="${CE_STATIC_IP}" \
            --project="${PROJECT_ID}"
    fi

    echo ""
    echo "==> DNS delegation required!"
    echo "    Add these NS records in your parent domain's DNS project:"
    gcloud dns managed-zones describe "${DNS_ZONE_NAME}" --format='value(nameServers)' --project="${PROJECT_ID}" | tr ';' '\n' | sed 's/^/      /'
    echo ""
    echo "    Point: ${DNS_SUBDOMAIN} NS → the above nameservers"
fi

# --- Summary ---
echo ""
echo "==> Infrastructure ready!"
echo "    CE instance: bsky-server (${CE_STATIC_IP})"
echo "    Cloud SQL:   bsky-db (${DB_IP})"
if [[ -n "${DNS_SUBDOMAIN:-}" ]]; then
    echo "    API URL:     https://${DNS_SUBDOMAIN}  (HTTPS via Caddy, provisioned on first deploy)"
else
    echo "    API URL:     http://${CE_STATIC_IP}:8000"
fi
echo ""
echo "    Next: run ./deploy.sh to deploy the application."
