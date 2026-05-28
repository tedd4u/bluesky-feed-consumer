#!/usr/bin/env bash
# Deploy application to the CE instance.
# Pulls latest code, syncs deps, writes .env from Secret Manager, restarts service.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib-log.sh"
source "${SCRIPT_DIR}/.env.infra" 2>/dev/null || { echo "ERROR: .env.infra not found."; exit 1; }

: "${PROJECT_ID:?Set PROJECT_ID in .env.infra}"
: "${ZONE:=us-central1-a}"
: "${DNS_SUBDOMAIN:=}"

echo "==> Deploying to bsky-server..."

# Get Cloud SQL private IP for the DATABASE_URL
DB_IP=$(gcloud sql instances describe bsky-db --format='value(ipAddresses[0].ipAddress)' --project="${PROJECT_ID}")

gcloud compute ssh bsky-server --zone="${ZONE}" --project="${PROJECT_ID}" --command="
set -e

# Ensure deploy key exists for GitHub access (system-wide path so any OS user works)
DEPLOY_KEY=/opt/bluesky-feed-consumer/.ssh/deploy_key
if [ ! -f \"\${DEPLOY_KEY}\" ]; then
    sudo mkdir -p /opt/bluesky-feed-consumer/.ssh
    sudo ssh-keygen -t ed25519 -f \"\${DEPLOY_KEY}\" -N '' -C 'bsky-server-deploy-key'
    sudo chmod 644 /opt/bluesky-feed-consumer/.ssh
    sudo chmod 600 \"\${DEPLOY_KEY}\"
    sudo chmod 644 \"\${DEPLOY_KEY}.pub\"
    echo ''
    echo '========================================='
    echo 'DEPLOY KEY (add to GitHub repo settings):'
    echo '========================================='
    cat \"\${DEPLOY_KEY}.pub\"
    echo '========================================='
    echo 'Add at: repo Settings > Deploy keys > Add deploy key'
    echo 'Leave read-only. Then re-run this script.'
    echo '========================================='
    exit 1
fi

# Configure SSH to use the deploy key for GitHub
mkdir -p ~/.ssh
cat > ~/.ssh/config << SSHEOF
Host github.com
    IdentityFile \${DEPLOY_KEY}
    StrictHostKeyChecking accept-new
SSHEOF
chmod 600 ~/.ssh/config

# Ensure repo is cloned
if [ ! -d /opt/bluesky-feed-consumer ]; then
    sudo mkdir -p /opt/bluesky-feed-consumer
    sudo chown \$(whoami) /opt/bluesky-feed-consumer
    git clone git@github.com:tedd4u/bluesky-feed-consumer.git /opt/bluesky-feed-consumer
fi

cd /opt/bluesky-feed-consumer
git config --global --add safe.directory /opt/bluesky-feed-consumer
sudo chown -R \$(whoami) /opt/bluesky-feed-consumer
git pull origin master

# Install/sync dependencies
export PATH=\"/usr/local/bin:\$HOME/.local/bin:\$PATH\"
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH=\"\$HOME/.local/bin:\$PATH\"
fi
uv sync --frozen

# Write .env from Secret Manager (secrets never touch git)
cat > .env << ENVEOF
BSKY_DATABASE_URL=postgresql+asyncpg://postgres:$(gcloud secrets versions access latest --secret=bsky-db-password --project=${PROJECT_ID})@${DB_IP}:5432/bsky
BSKY_SERVICE_API_KEY=$(gcloud secrets versions access latest --secret=bsky-api-key --project=${PROJECT_ID})
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

# --- Caddy reverse proxy (HTTPS) ---
if [[ -n '${DNS_SUBDOMAIN}' ]] && [[ '${DNS_SUBDOMAIN}' != '' ]]; then
    echo '==> Setting up Caddy reverse proxy for HTTPS'

    # Install Caddy if not present
    if ! command -v caddy &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null 2>&1
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
        sudo apt-get update -qq
        sudo apt-get install -y -qq caddy >/dev/null 2>&1
        echo '    Caddy installed.'
    fi

    # Write Caddyfile with the actual domain
    sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDYEOF
${DNS_SUBDOMAIN} {
    reverse_proxy localhost:8000
}
CADDYEOF

    sudo systemctl enable caddy
    sudo systemctl reload caddy 2>/dev/null || sudo systemctl restart caddy
    echo '    Caddy configured for ${DNS_SUBDOMAIN}'
else
    echo '==> Skipping Caddy setup (DNS_SUBDOMAIN not set)'
fi

echo 'Deploy complete.'
"

sleep 3
echo "==> Checking health..."
if [[ -n "${DNS_SUBDOMAIN}" ]]; then
    curl -sf "https://${DNS_SUBDOMAIN}/health" && echo " OK (HTTPS)" || {
        echo " HTTPS not ready yet, trying direct..."
        CE_IP=$(gcloud compute instances describe bsky-server --zone="${ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' --project="${PROJECT_ID}")
        curl -sf "http://${CE_IP}:8000/health" && echo " OK (direct)" || echo " FAILED (service may still be starting)"
    }
else
    CE_IP=$(gcloud compute instances describe bsky-server --zone="${ZONE}" --format='value(networkInterfaces[0].accessConfigs[0].natIP)' --project="${PROJECT_ID}")
    curl -sf "http://${CE_IP}:8000/health" && echo " OK" || echo " FAILED (service may still be starting)"
fi
