#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Preisermittlung"
APP_DIR="${PREISERMITTLUNG_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_NAME="${PREISERMITTLUNG_SERVICE:-preisermittlung}"
UPDATE_SERVICE_NAME="${PREISERMITTLUNG_UPDATE_SERVICE:-${SERVICE_NAME}-update}"
UPDATE_SERVICE_FILE="/etc/systemd/system/${UPDATE_SERVICE_NAME}.service"
SUDOERS_FILE="/etc/sudoers.d/${SERVICE_NAME}-update"
RUN_USER="${PREISERMITTLUNG_USER:-www-data}"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
CLIENT_MAX_BODY_SIZE="${PREISERMITTLUNG_CLIENT_MAX_BODY_SIZE:-512M}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this updater as root."
  exit 1
fi

cd "${APP_DIR}"

echo "Updating ${APP_NAME} in ${APP_DIR}"

if ! command -v sudo >/dev/null 2>&1; then
  apt update
  apt install -y sudo
fi

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
chmod +x scripts/update.sh scripts/gui_update.sh
if [[ ! -f state.json ]]; then
  printf '{}\n' > state.json
fi
if id "${RUN_USER}" >/dev/null 2>&1; then
  chown -R "${RUN_USER}:${RUN_USER}" generated manual_pdfs tmp .browser-cache .pdf-cache .playwright-browsers
  [[ -f config.yaml ]] && chown "${RUN_USER}:${RUN_USER}" config.yaml
  [[ -f state.json ]] && chown "${RUN_USER}:${RUN_USER}" state.json
fi

cat > "${UPDATE_SERVICE_FILE}" <<EOF
[Unit]
Description=${APP_NAME} Serverupdate
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${APP_DIR}
Environment=PREISERMITTLUNG_APP_DIR=${APP_DIR}
Environment=PREISERMITTLUNG_SERVICE=${SERVICE_NAME}
Environment=PREISERMITTLUNG_UPDATE_SERVICE=${UPDATE_SERVICE_NAME}
Environment=PREISERMITTLUNG_USER=${RUN_USER}
ExecStart=${APP_DIR}/scripts/gui_update.sh
TimeoutStartSec=1800
EOF

SYSTEMCTL_BIN="$(command -v systemctl)"
cat > "${SUDOERS_FILE}" <<EOF
${RUN_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} start --no-block ${UPDATE_SERVICE_NAME}.service
EOF
chmod 0440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}" >/dev/null

systemctl daemon-reload

systemctl restart "${SERVICE_NAME}"

if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo
  echo "${SERVICE_NAME} failed to start after update."
  systemctl status "${SERVICE_NAME}" --no-pager -l || true
  journalctl -u "${SERVICE_NAME}" -n 100 --no-pager || true
  exit 1
fi

if [[ -f "${NGINX_SITE}" ]]; then
  if grep -q "client_max_body_size" "${NGINX_SITE}"; then
    sed -i "s/client_max_body_size .*/client_max_body_size ${CLIENT_MAX_BODY_SIZE};/" "${NGINX_SITE}"
  else
    sed -i "/server_name _;/a\\    client_max_body_size ${CLIENT_MAX_BODY_SIZE};" "${NGINX_SITE}"
  fi
  if nginx -t; then
    systemctl reload nginx || systemctl restart nginx
  else
    echo "nginx configuration test failed after updating client_max_body_size."
    exit 1
  fi
fi

echo
echo "${APP_NAME} updated and ${SERVICE_NAME} restarted."
