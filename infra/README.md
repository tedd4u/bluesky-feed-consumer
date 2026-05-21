# Infrastructure

Shell scripts to provision, deploy, and tear down the GCP environment. All scripts source `lib-log.sh` which tees output to timestamped log files in `logs/`.

## Prerequisites

- `gcloud` CLI authenticated (`gcloud auth login`)
- Billing account with project-creator permissions
- Local `.env.infra` (copy from `.env.infra.example`) with infra config
- Local `../.env` with application secrets (API keys)

## One-Time Setup

### 1. Deploy Key (GitHub → CE instance)

The first run of `deploy.sh` generates an ed25519 SSH key on the CE instance and prints it. Add it to the GitHub repo:

1. Run `./deploy.sh` — it will exit after printing the public key
2. Go to your repo's **Settings > Deploy keys**
3. Click **"Add deploy key"**
4. Title: `bsky-server`
5. Paste the public key
6. Leave **"Allow write access" unchecked** (read-only is sufficient)
7. Re-run `./deploy.sh`

The deploy key persists on the CE instance's disk across restarts and project undelete/recreate cycles. It is also used by GitHub Actions CD (the action SSHes to CE which then does `git pull`).

### 2. DNS Delegation

If `DNS_SUBDOMAIN` is set in `.env.infra`, `setup.sh` creates a Cloud DNS zone and prints NS records. You need to add those NS records in the parent domain's DNS:

1. Open the DNS management for the parent domain (e.g., Cloud DNS in another GCP project, or your registrar's DNS panel)
2. Add an NS record set:
   - **Name**: the subdomain configured in `DNS_SUBDOMAIN`
   - **Type**: NS
   - **TTL**: 300
   - **Data**: The 4 `ns-cloud-*.googledomains.com.` records printed by `setup.sh`
3. Wait for propagation (~5 minutes): `dig <your-subdomain>`

The NS records are also logged in `logs/latest-setup.log`.

On subsequent runs, `setup.sh` will update the A record if the CE instance's IP has changed.

## Scripts

| Script | Purpose | Idempotent |
|--------|---------|:----------:|
| `create-project.sh` | Create GCP project, link billing, enable APIs | Yes |
| `setup.sh` | Secret Manager, VPC peering, Cloud SQL, CE, firewall, DNS | Yes |
| `deploy.sh` | SSH to CE, pull code, sync deps, write .env, migrate, restart | Yes |
| `monitoring.sh` | Uptime check, Slack alerts, alert policies, dashboard | Yes |
| `teardown.sh` | Delete entire GCP project (interactive confirmation) | N/A |

## Logs

All script output is logged to `logs/` (gitignored):

```
logs/
├── 2026-05-21_184500_create-project.log
├── 2026-05-21_184700_setup.log
├── latest-create-project.log → ...
└── latest-setup.log → ...
```

Quick access to most recent run: `cat logs/latest-deploy.log`

## Monitoring & Alerting

### Prerequisites

Add `SLACK_WEBHOOK_URL` to `.env.infra`:

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
```

### What it creates

| Resource | Purpose |
|----------|---------|
| Uptime check | Hits `/health` every 60s from multiple regions |
| Notification channel | Slack webhook for alert delivery |
| Alert: Service Down | Fires when health check fails for > 60s |
| Alert: CE CPU High | Fires when CE CPU > 80% for 5 min |
| Alert: SQL CPU High | Fires when Cloud SQL CPU > 80% for 5 min |
| Alert: SQL Storage High | Fires when Cloud SQL disk > 80% for 5 min |
| Dashboard | 6-panel view: CE CPU/network, SQL CPU/disk/connections, uptime latency |

### Application metrics

The service exposes a `/metrics` endpoint (no auth required) returning:

- `firehose_events_total` — total events processed since boot
- `firehose_events_per_second` — recent throughput
- `api_request_count` — total API requests served
- `api_request_errors` — 5xx responses
- `api_avg_latency_ms` — mean request latency
- `sse_connections` — active SSE client count

### Running

```bash
./monitoring.sh
```

Safe to re-run. Skips resources that already exist.

## Secrets Flow

```
.env (local)          →  setup.sh stores in Secret Manager
.env.infra (local)    →  setup.sh uses for infra config (DB password also stored in SM)
Secret Manager (GCP)  →  deploy.sh reads at deploy time, writes .env on CE instance
```

Secrets never appear in git. The CE instance's `.env` is written with `chmod 600` at deploy time.

## Troubleshooting

All commands below use variables from `.env.infra`. Source it first or substitute your values:

```bash
source .env.infra
```

### SSH to the instance

```bash
gcloud compute ssh bsky-server --zone="${ZONE}" --project="${PROJECT_ID}"
```

### Check service status

```bash
gcloud compute ssh bsky-server --zone="${ZONE}" --project="${PROJECT_ID}" \
    --command="sudo systemctl status bsky-server"
```

### View application logs

```bash
gcloud compute ssh bsky-server --zone="${ZONE}" --project="${PROJECT_ID}" \
    --command="sudo journalctl -u bsky-server -f"
```

### Rotate a secret

```bash
echo "new-value" | gcloud secrets versions add bsky-api-key --data-file=- --project="${PROJECT_ID}"
./deploy.sh   # re-writes .env on CE from Secret Manager
```

## Continuous Deployment (GitHub Actions)

Merges to `master` automatically deploy via the `deploy` job in `.github/workflows/ci.yml`. CI (lint + typecheck + tests) must pass first.

### One-Time Setup

1. **Create a GCP service account** for GitHub Actions:

```bash
source .env.infra
SA_NAME=github-actions-deploy

gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="GitHub Actions Deploy" \
    --project="${PROJECT_ID}"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Grant SSH access to CE and Cloud SQL describe (for DB IP lookup)
for ROLE in roles/compute.instanceAdmin.v1 roles/iam.serviceAccountUser roles/cloudsql.viewer; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${ROLE}"
done

# Download key (store as GitHub secret, then delete the local file)
gcloud iam service-accounts keys create sa-key.json \
    --iam-account="${SA_EMAIL}" \
    --project="${PROJECT_ID}"
```

2. **Add GitHub repository secrets and variables** (Settings > Secrets and variables > Actions):

| Type | Name | Value |
|------|------|-------|
| Secret | `GCP_SA_KEY` | Contents of `sa-key.json` |
| Variable | `GCP_PROJECT_ID` | Your GCP project ID |
| Variable | `GCP_ZONE` | e.g. `us-central1-a` |

3. **Create the `production` environment** (Settings > Environments > New environment > "production"). Optional: add reviewers for manual approval before deploy.

4. **Delete the local key file**: `rm sa-key.json`

### Triggering a Deploy

- **Automatic**: push or merge to `master`
- **Manual**: Actions tab > CI > Run workflow

## Nuke and Recreate Notes

`teardown.sh` deletes the entire GCP project. GCP retains deleted projects for 30 days.

If you **undelete** the same project ID (`gcloud projects undelete <id>`), be aware:
- Resources (CE, Cloud SQL, secrets) survive but may be in a stale state (e.g., CE instance TERMINATED, APIs returning CONSUMER_INVALID)
- API propagation after undelete can take 2-3 minutes — `setup.sh` may need a retry
- The CE disk persists, so the deploy key and cloned repo survive
- The CE external IP will change — `setup.sh` updates the DNS A record automatically

For a truly clean start, use a new project ID in `.env.infra`.
