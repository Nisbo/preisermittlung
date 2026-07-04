#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Preisermittlung"
APP_DIR="${PREISERMITTLUNG_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_NAME="${PREISERMITTLUNG_SERVICE:-preisermittlung}"
RUN_USER="${PREISERMITTLUNG_USER:-www-data}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this updater as root."
  exit 1
fi

cd "${APP_DIR}"

echo "Updating ${APP_NAME} in ${APP_DIR}"

if [[ -d ".git" ]]; then
  git fetch --all --tags
  git pull --ff-only
else
  echo "This installation is not a git checkout. Skipping git pull."
fi

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

".venv/bin/python" -m pip install --upgrade pip wheel
".venv/bin/pip" install -r requirements.txt

if ".venv/bin/python" -m playwright --version >/dev/null 2>&1; then
  PLAYWRIGHT_BROWSERS_PATH="${APP_DIR}/.playwright-browsers" \
    ".venv/bin/python" -m playwright install --with-deps chromium
fi

install -d -m 0755 generated manual_pdfs tmp .browser-cache .pdf-cache .playwright-browsers
if [[ ! -f state.json ]]; then
  printf '{}\n' > state.json
fi
if id "${RUN_USER}" >/dev/null 2>&1; then
  chown -R "${RUN_USER}:${RUN_USER}" generated manual_pdfs tmp .browser-cache .pdf-cache .playwright-browsers
  [[ -f config.yaml ]] && chown "${RUN_USER}:${RUN_USER}" config.yaml
  [[ -f state.json ]] && chown "${RUN_USER}:${RUN_USER}" state.json
fi

systemctl restart "${SERVICE_NAME}"

if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo
  echo "${SERVICE_NAME} failed to start after update."
  systemctl status "${SERVICE_NAME}" --no-pager -l || true
  journalctl -u "${SERVICE_NAME}" -n 100 --no-pager || true
  exit 1
fi

echo
echo "${APP_NAME} updated and ${SERVICE_NAME} restarted."
