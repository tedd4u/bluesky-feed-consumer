#!/usr/bin/env bash
# Delete the entire GCP project. One command to nuke everything.
# All resources (CE, Cloud SQL, secrets, DNS, firewall) are destroyed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.env.infra" 2>/dev/null || { echo "ERROR: .env.infra not found."; exit 1; }

: "${PROJECT_ID:?Set PROJECT_ID in .env.infra}"

echo "WARNING: This will permanently delete project '${PROJECT_ID}' and ALL its resources."
echo "         (Cloud SQL data, secrets, CE instance, DNS zones — everything.)"
echo ""
read -p "Type the project ID to confirm deletion: " CONFIRM

if [[ "${CONFIRM}" != "${PROJECT_ID}" ]]; then
    echo "Aborted."
    exit 1
fi

echo "==> Deleting project ${PROJECT_ID}..."
gcloud projects delete "${PROJECT_ID}" --quiet

echo ""
echo "==> Project deleted."
echo "    Note: GCP retains deleted projects for 30 days. To fully purge:"
echo "    gcloud projects undelete ${PROJECT_ID}  (to recover)"
echo ""
echo "    To recreate from scratch: ./create-project.sh && ./setup.sh && ./deploy.sh"
