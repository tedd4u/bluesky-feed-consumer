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
2. Go to https://github.com/tedd4u/bluesky-feed-consumer/settings/keys
3. Click **"Add deploy key"**
4. Title: `bsky-server`
5. Paste the public key
6. Leave **"Allow write access" unchecked** (read-only is sufficient)
7. Re-run `./deploy.sh`

This key is also used by GitHub Actions CD (the action SSHes to CE which then does `git pull`).

### 2. DNS Delegation

After `setup.sh` creates the Cloud DNS zone, it prints NS records. Add them in the parent domain's DNS project:

1. Go to the Cloud DNS zone for `berbs.com` (in the "berbs" GCP project)
2. Add an NS record set:
   - **Name**: `api.bsky` (or whatever subdomain `setup.sh` created)
   - **Type**: NS
   - **TTL**: 300
   - **Data**: The 4 `ns-cloud-*.googledomains.com.` records printed by `setup.sh`
3. Wait for propagation (~5 minutes): `dig api.bsky.berbs.com`

The NS records are also logged in `logs/latest-setup.log`.

## Scripts

| Script | Purpose | Idempotent |
|--------|---------|:----------:|
| `create-project.sh` | Create GCP project, link billing, enable APIs | Yes |
| `setup.sh` | Secret Manager, VPC peering, Cloud SQL, CE, firewall, DNS | Yes |
| `deploy.sh` | SSH to CE, pull code, sync deps, write .env, migrate, restart | Yes |
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

## Secrets Flow

```
.env (local)          →  setup.sh stores in Secret Manager
.env.infra (local)    →  setup.sh uses for infra config (DB password also stored in SM)
Secret Manager (GCP)  →  deploy.sh reads at deploy time, writes .env on CE instance
```

Secrets never appear in git. The CE instance's `.env` is written with `chmod 600` at deploy time.

## Troubleshooting

### SSH to the instance

```bash
gcloud compute ssh bsky-server --zone=us-central1-a --project=bsky-feed-consumer-tm
```

### Check service status

```bash
gcloud compute ssh bsky-server --zone=us-central1-a --project=bsky-feed-consumer-tm \
    --command="sudo systemctl status bsky-server"
```

### View application logs

```bash
gcloud compute ssh bsky-server --zone=us-central1-a --project=bsky-feed-consumer-tm \
    --command="sudo journalctl -u bsky-server -f"
```

### Rotate a secret

```bash
echo "new-value" | gcloud secrets versions add bsky-api-key --data-file=- --project=bsky-feed-consumer-tm
./deploy.sh   # re-writes .env on CE from Secret Manager
```
