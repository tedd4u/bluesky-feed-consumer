#!/usr/bin/env bash
# Shared logging setup for infra scripts.
# Source this at the top of each script AFTER setting SCRIPT_DIR.
#
# On first invocation, re-runs the calling script piped through tee so that
# all output goes to both the terminal and a timestamped log file.
# Uses a pipe (not process substitution) to avoid breaking gcloud compute ssh.

if [[ -z "${_LOGGING_ACTIVE:-}" ]]; then
    LOGS_DIR="${SCRIPT_DIR}/logs"
    mkdir -p "${LOGS_DIR}"

    _SCRIPT_NAME="$(basename "${BASH_SOURCE[1]}" .sh)"
    _TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
    export LOG_FILE="${LOGS_DIR}/${_TIMESTAMP}_${_SCRIPT_NAME}.log"
    ln -sf "$(basename "${LOG_FILE}")" "${LOGS_DIR}/latest-${_SCRIPT_NAME}.log"

    export _LOGGING_ACTIVE=1
    bash "${BASH_SOURCE[1]}" "$@" 2>&1 | tee "${LOG_FILE}"
    exit "${PIPESTATUS[0]}"
fi

echo "=== $(basename "${BASH_SOURCE[1]}" .sh) started at $(date +%Y-%m-%d_%H%M%S) ==="
echo ""
