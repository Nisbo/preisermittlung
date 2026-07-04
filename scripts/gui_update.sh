#!/usr/bin/env bash
set -uo pipefail

APP_DIR="${PREISERMITTLUNG_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG_FILE="${PREISERMITTLUNG_UPDATE_LOG:-${APP_DIR}/tmp/update.log}"

mkdir -p "$(dirname "${LOG_FILE}")"
: > "${LOG_FILE}"
chmod 0644 "${LOG_FILE}"

{
  echo "Preisermittlung Serverupdate"
  echo "Start: $(date -Is)"
  echo "App-Verzeichnis: ${APP_DIR}"
  echo

  "${APP_DIR}/scripts/update.sh"
  status=$?

  echo
  echo "Ende: $(date -Is)"
  echo "Exit-Code: ${status}"
  exit "${status}"
} >> "${LOG_FILE}" 2>&1
