#!/usr/bin/env bash
# Set up Cloud Monitoring: uptime check, notification channel, alert policies, dashboard.
# Idempotent: safe to re-run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib-log.sh"
source "${SCRIPT_DIR}/.env.infra" 2>/dev/null || { echo "ERROR: .env.infra not found."; exit 1; }

: "${PROJECT_ID:?Set PROJECT_ID in .env.infra}"
: "${REGION:=us-central1}"
: "${ZONE:=us-central1-a}"
: "${SLACK_WEBHOOK_URL:?Set SLACK_WEBHOOK_URL in .env.infra}"

gcloud config set project "${PROJECT_ID}"

# Resolve uptime check host: prefer DNS hostname, fall back to static IP
if [[ -n "${DNS_SUBDOMAIN:-}" ]]; then
    UPTIME_HOST="${DNS_SUBDOMAIN}"
    echo "==> Uptime check host (DNS): ${UPTIME_HOST}"
else
    UPTIME_HOST=$(gcloud compute addresses describe bsky-server-ip --region="${REGION}" --format='value(address)' --project="${PROJECT_ID}")
    echo "==> Uptime check host (static IP): ${UPTIME_HOST}"
fi

# --- Notification Channel (Slack) ---
echo "==> Creating Slack notification channel"

# Check if channel already exists (take first match if multiple)
EXISTING_CHANNEL=$(gcloud alpha monitoring channels list \
    --filter="type='webhook_tokenauth' AND displayName='bsky-alerts-slack'" \
    --format='value(name)' \
    --project="${PROJECT_ID}" 2>/dev/null | head -1 || true)

if [[ -n "${EXISTING_CHANNEL}" ]]; then
    CHANNEL_ID="${EXISTING_CHANNEL}"
    echo "    Channel already exists: ${CHANNEL_ID}"
else
    CHANNEL_ID=$(gcloud alpha monitoring channels create \
        --display-name="bsky-alerts-slack" \
        --type="webhook_tokenauth" \
        --channel-labels="url=${SLACK_WEBHOOK_URL}" \
        --format='value(name)' \
        --project="${PROJECT_ID}")
    echo "    Created channel: ${CHANNEL_ID}"
fi

# --- Uptime Check ---
echo "==> Creating uptime check for /health endpoint"

EXISTING_CHECK=$(gcloud monitoring uptime list-configs \
    --filter="displayName='bsky-server-health'" \
    --format='value(name)' \
    --project="${PROJECT_ID}" 2>/dev/null || true)

# Determine desired protocol
if [[ -n "${DNS_SUBDOMAIN:-}" ]]; then
    UPTIME_PORT=443
    UPTIME_PROTO="https"
else
    UPTIME_PORT=8000
    UPTIME_PROTO="http"
fi

if [[ -n "${EXISTING_CHECK}" ]]; then
    # Re-create if the existing check uses a different port (e.g. migrating HTTP→HTTPS)
    EXISTING_PORT=$(gcloud monitoring uptime describe "${EXISTING_CHECK}" \
        --format='value(monitoredResource.labels.port)' \
        --project="${PROJECT_ID}" 2>/dev/null || echo "")
    if [[ "${EXISTING_PORT}" != "${UPTIME_PORT}" ]]; then
        echo "    Uptime check exists on port ${EXISTING_PORT}, migrating to ${UPTIME_PROTO}:${UPTIME_PORT}..."
        gcloud monitoring uptime delete "${EXISTING_CHECK}" --project="${PROJECT_ID}" --quiet
        EXISTING_CHECK=""
    else
        echo "    Uptime check already exists (${UPTIME_PROTO}:${UPTIME_PORT})."
    fi
fi

if [[ -z "${EXISTING_CHECK}" ]]; then
    gcloud monitoring uptime create "bsky-server-health" \
        --resource-type="uptime-url" \
        --resource-labels="host=${UPTIME_HOST},project_id=${PROJECT_ID}" \
        --port="${UPTIME_PORT}" \
        --path="/health" \
        --protocol="${UPTIME_PROTO}" \
        --period=1 \
        --timeout=10 \
        --project="${PROJECT_ID}"
    echo "    Created uptime check (${UPTIME_PROTO}:${UPTIME_PORT})."
fi

# --- Alert Policies ---
echo "==> Creating alert policies"

# Helper: create alert policy from JSON if it doesn't exist
POLICY_TMPFILE=$(mktemp)
trap 'rm -f "${POLICY_TMPFILE}"' EXIT

create_alert_if_missing() {
    local display_name="$1"
    local policy_json="$2"

    existing=$(gcloud alpha monitoring policies list \
        --filter="displayName='${display_name}'" \
        --format='value(name)' \
        --project="${PROJECT_ID}" 2>/dev/null || true)

    if [[ -n "${existing}" ]]; then
        echo "    Policy '${display_name}' already exists."
    else
        echo "${policy_json}" > "${POLICY_TMPFILE}"
        gcloud alpha monitoring policies create \
            --policy-from-file="${POLICY_TMPFILE}" \
            --project="${PROJECT_ID}"
        echo "    Created policy: ${display_name}"
    fi
}

# 1. Uptime check failure (service down for > 60 seconds)
create_alert_if_missing "Service Down" "$(cat <<EOF
{
  "displayName": "Service Down",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "Health check failing",
      "conditionThreshold": {
        "filter": "resource.type=\"uptime_url\" AND metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\"",
        "comparison": "COMPARISON_GT",
        "thresholdValue": 1,
        "duration": "60s",
        "trigger": { "count": 1 },
        "aggregations": [
          {
            "alignmentPeriod": "60s",
            "perSeriesAligner": "ALIGN_NEXT_OLDER",
            "crossSeriesReducer": "REDUCE_COUNT_FALSE",
            "groupByFields": ["resource.label.host"]
          }
        ]
      }
    }
  ],
  "alertStrategy": {
    "autoClose": "604800s"
  },
  "notificationChannels": ["${CHANNEL_ID}"]
}
EOF
)"

# 2. CE CPU > 80% for 5 minutes
create_alert_if_missing "CE CPU High" "$(cat <<EOF
{
  "displayName": "CE CPU High",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "CPU utilization > 80%",
      "conditionThreshold": {
        "filter": "resource.type=\"gce_instance\" AND resource.labels.instance_id=monitoring.regex.full_match(\".*\") AND metric.type=\"compute.googleapis.com/instance/cpu/utilization\"",
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0.8,
        "duration": "300s",
        "trigger": { "count": 1 },
        "aggregations": [
          {
            "alignmentPeriod": "60s",
            "perSeriesAligner": "ALIGN_MEAN"
          }
        ]
      }
    }
  ],
  "alertStrategy": {
    "autoClose": "604800s"
  },
  "notificationChannels": ["${CHANNEL_ID}"]
}
EOF
)"

# 3. Cloud SQL CPU > 80% for 5 minutes
create_alert_if_missing "SQL CPU High" "$(cat <<EOF
{
  "displayName": "SQL CPU High",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "Cloud SQL CPU > 80%",
      "conditionThreshold": {
        "filter": "resource.type=\"cloudsql_database\" AND metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\"",
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0.8,
        "duration": "300s",
        "trigger": { "count": 1 },
        "aggregations": [
          {
            "alignmentPeriod": "60s",
            "perSeriesAligner": "ALIGN_MEAN"
          }
        ]
      }
    }
  ],
  "alertStrategy": {
    "autoClose": "604800s"
  },
  "notificationChannels": ["${CHANNEL_ID}"]
}
EOF
)"

# 4. Cloud SQL storage > 80% for 5 minutes
create_alert_if_missing "SQL Storage High" "$(cat <<EOF
{
  "displayName": "SQL Storage High",
  "combiner": "OR",
  "conditions": [
    {
      "displayName": "Cloud SQL disk > 80%",
      "conditionThreshold": {
        "filter": "resource.type=\"cloudsql_database\" AND metric.type=\"cloudsql.googleapis.com/database/disk/utilization\"",
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0.8,
        "duration": "300s",
        "trigger": { "count": 1 },
        "aggregations": [
          {
            "alignmentPeriod": "60s",
            "perSeriesAligner": "ALIGN_MEAN"
          }
        ]
      }
    }
  ],
  "alertStrategy": {
    "autoClose": "604800s"
  },
  "notificationChannels": ["${CHANNEL_ID}"]
}
EOF
)"

# --- Dashboard ---
echo "==> Creating monitoring dashboard"

EXISTING_DASHBOARD=$(gcloud monitoring dashboards list \
    --filter="displayName='Bluesky Feed Consumer'" \
    --format='value(name)' \
    --project="${PROJECT_ID}" 2>/dev/null || true)

if [[ -n "${EXISTING_DASHBOARD}" ]]; then
    echo "    Dashboard already exists."
else
    gcloud monitoring dashboards create --config-from-file=- --project="${PROJECT_ID}" <<'DASHBOARD'
{
  "displayName": "Bluesky Feed Consumer",
  "gridLayout": {
    "columns": "2",
    "widgets": [
      {
        "title": "CE CPU Utilization",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"gce_instance\" AND metric.type=\"compute.googleapis.com/instance/cpu/utilization\"",
                "aggregation": {
                  "alignmentPeriod": "60s",
                  "perSeriesAligner": "ALIGN_MEAN"
                }
              }
            }
          }],
          "yAxis": { "label": "CPU %", "scale": "LINEAR" }
        }
      },
      {
        "title": "CE Network Traffic",
        "xyChart": {
          "dataSets": [
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"gce_instance\" AND metric.type=\"compute.googleapis.com/instance/network/received_bytes_count\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_RATE"
                  }
                }
              }
            },
            {
              "timeSeriesQuery": {
                "timeSeriesFilter": {
                  "filter": "resource.type=\"gce_instance\" AND metric.type=\"compute.googleapis.com/instance/network/sent_bytes_count\"",
                  "aggregation": {
                    "alignmentPeriod": "60s",
                    "perSeriesAligner": "ALIGN_RATE"
                  }
                }
              }
            }
          ],
          "yAxis": { "label": "bytes/s", "scale": "LINEAR" }
        }
      },
      {
        "title": "Cloud SQL CPU",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"cloudsql_database\" AND metric.type=\"cloudsql.googleapis.com/database/cpu/utilization\"",
                "aggregation": {
                  "alignmentPeriod": "60s",
                  "perSeriesAligner": "ALIGN_MEAN"
                }
              }
            }
          }],
          "yAxis": { "label": "CPU %", "scale": "LINEAR" }
        }
      },
      {
        "title": "Cloud SQL Disk Usage",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"cloudsql_database\" AND metric.type=\"cloudsql.googleapis.com/database/disk/utilization\"",
                "aggregation": {
                  "alignmentPeriod": "60s",
                  "perSeriesAligner": "ALIGN_MEAN"
                }
              }
            }
          }],
          "yAxis": { "label": "Disk %", "scale": "LINEAR" }
        }
      },
      {
        "title": "Uptime Check Latency",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"uptime_url\" AND metric.type=\"monitoring.googleapis.com/uptime_check/request_latency\"",
                "aggregation": {
                  "alignmentPeriod": "60s",
                  "perSeriesAligner": "ALIGN_MEAN"
                }
              }
            }
          }],
          "yAxis": { "label": "Latency (ms)", "scale": "LINEAR" }
        }
      },
      {
        "title": "Cloud SQL Connections",
        "xyChart": {
          "dataSets": [{
            "timeSeriesQuery": {
              "timeSeriesFilter": {
                "filter": "resource.type=\"cloudsql_database\" AND metric.type=\"cloudsql.googleapis.com/database/network/connections\"",
                "aggregation": {
                  "alignmentPeriod": "60s",
                  "perSeriesAligner": "ALIGN_MEAN"
                }
              }
            }
          }],
          "yAxis": { "label": "Connections", "scale": "LINEAR" }
        }
      }
    ]
  }
}
DASHBOARD
    echo "    Created dashboard."
fi

echo ""
echo "==> Monitoring setup complete!"
echo "    Notification channel: Slack webhook"
if [[ -n "${DNS_SUBDOMAIN:-}" ]]; then
    echo "    Uptime check: https://${UPTIME_HOST}/health (every 60s)"
else
    echo "    Uptime check: http://${UPTIME_HOST}:8000/health (every 60s)"
fi
echo "    Alert policies:"
echo "      - Service Down (health check failing > 60s)"
echo "      - CE CPU High (> 80% for 5 min)"
echo "      - SQL CPU High (> 80% for 5 min)"
echo "      - SQL Storage High (> 80% for 5 min)"
echo "    Dashboard: 'Bluesky Feed Consumer' in Cloud Monitoring console"
