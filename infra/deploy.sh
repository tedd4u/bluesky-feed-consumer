#!/usr/bin/env bash
# Deploy application to the CE instance.
# Pulls latest code, syncs deps, writes .env from Secret Manager, restarts service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/.env.infra" 2>/dev/null || { echo "ERROR: .env.infra not found."; exit 1; }

: "${PROJECT_ID:?Set PROJECT_ID in .env.infra}"
: "${ZONE:=us-central1-a}"

echo "==> Deploying to bsky-server..."

# Get Cloud SQL private IP for the DATABASE_URL
DB_IP=$(gcloud sql instances describe bsky-db --format='value(ipAddresses[0].ipAddress)' --project="${PROJECT_ID}")

gcloud compute ssh bsky-server --zone="${ZONE}" --project="${PROJECT_ID}" --command="
set -e

# Ensure deploy key exists for GitHub access
if [ ! -f ~/.ssh/deploy_key ]; then
    ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N '' -C 'bsky-server-deploy-key'
    cat >> ~/.ssh/config << 'SSHEOF'
Host github.com
    IdentityFile ~/.ssh/deploy_key
    StrictHostKeyChecking accept-new
SSHEOF
    chmod 600 ~/.ssh/config
    echo ''
    echo '========================================='
    echo 'DEPLOY KEY (add to GitHub repo settings):'
    echo '========================================='
    cat ~/.ssh/deploy_key.pub
    echo '========================================='
    echo 'Go to: https://github.com/tedd4u/bluesky-feed-consumer/settings/keys'
    echo 'Click \"Add deploy key\", paste the key above, leave read-only.'
    echo 'Then re-run this script.'
    echo '========================================='
    exit 1
fi

# Ensure repo is cloned
if [ ! -d /opt/bluesky-feed-consumer ]; then
    sudo mkdir -p /opt/bluesky-feed-consumer
    sudo chown \$(whoami) /opt/bluesky-feed-consumer
    git clone git@github.com:tedd4u/bluesky-feed-consumer.git /opt/bluesky-feed-consumer
fi

cd /opt/bluesky-feed-consumer
git pull origin master

# Install/sync dependencies
export PATH=\"\$HOME/.local/bin:\$PATH\"
uv sync --frozen

# Write .env from Secret Manager (secrets never touch git)
cat > .env << ENVEOF
BSKY_DATABASE_URL=postgresql+asyncpg://postgres:$(gcloud secrets versions access latest --secret=bsky-db-password --project=${PROJECT_ID})@${DB_IP}:5432/bsky
BSKY_API_KEY=$(gcloud secrets versions access latest --secret=bsky-api-key --project=${PROJECT_ID})
BSKY_ANTHROPIC_API_KEY=$(gcloud secrets versions access latest --secret=bsky-anthropic-api-key --project=${PROJECT_ID})
ENVEOF
chmod 600 .env

# Run migrations
.venv/bin/alembic upgrade head

# Install and restart systemd service
sudo cp infra/bsky-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable bsky-server
sudo systemctl restart bsky-server

echo 'Deploy complete.'
"

sleep 3
echo "==> Checking health..."
CE_IP=$(gcloud compute instances describe bsky-server --zone="${ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' --project="${PROJECT_ID}")
curl -sf "http://${CE_IP}:8000/health" && echo " OK" || echo " FAILED (service may still be starting)"
