#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Preisermittlung"
APP_SLUG="preisermittlung"
APP_DIR="${PREISERMITTLUNG_APP_DIR:-/opt/preisermittlung}"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="${PREISERMITTLUNG_SERVICE:-preisermittlung}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
UPDATE_SERVICE_NAME="${PREISERMITTLUNG_UPDATE_SERVICE:-${SERVICE_NAME}-update}"
UPDATE_SERVICE_FILE="/etc/systemd/system/${UPDATE_SERVICE_NAME}.service"
SUDOERS_FILE="/etc/sudoers.d/${SERVICE_NAME}-update"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"
INTERNAL_HOST="${PREISERMITTLUNG_HOST:-127.0.0.1}"
INTERNAL_PORT="${PREISERMITTLUNG_PORT:-5050}"
PUBLIC_PORT="${PREISERMITTLUNG_PUBLIC_PORT:-5151}"
RUN_USER="${PREISERMITTLUNG_USER:-www-data}"
CLIENT_MAX_BODY_SIZE="${PREISERMITTLUNG_CLIENT_MAX_BODY_SIZE:-512M}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run this installer as root."
  exit 1
fi

if [[ -t 0 && -z "${PREISERMITTLUNG_PUBLIC_PORT:-}" ]]; then
  echo
  echo "${APP_NAME} will be exposed through nginx."
  echo "Suggested public port: ${PUBLIC_PORT}"
  echo "You can use another port, for example 80, if no other service uses it."
  read -r -p "Public nginx port [${PUBLIC_PORT}]: " answer
  PUBLIC_PORT="${answer:-${PUBLIC_PORT}}"
fi

if [[ "${INTERNAL_HOST}" == "127.0.0.1" && "${INTERNAL_PORT}" == "${PUBLIC_PORT}" ]]; then
  echo "Internal and public ports cannot both be ${INTERNAL_PORT} on 127.0.0.1."
  echo "Using public port 5151 to avoid an nginx proxy loop."
  PUBLIC_PORT="5151"
fi

echo "Installing ${APP_NAME}"
echo "Source: ${SOURCE_DIR}"
echo "Target: ${APP_DIR}"
echo "Internal Gunicorn: ${INTERNAL_HOST}:${INTERNAL_PORT}"
echo "Public nginx port: ${PUBLIC_PORT}"
echo "nginx upload limit: ${CLIENT_MAX_BODY_SIZE}"

apt update
apt install -y \
  git \
  nginx \
  rsync \
  sudo \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev \
  build-essential \
  libjpeg-dev \
  zlib1g-dev \
  libopenjp2-7 \
  libtiff6 \
  poppler-utils

install -d -m 0755 "${APP_DIR}"

if [[ "${SOURCE_DIR}" != "${APP_DIR}" ]]; then
  rsync -a \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.DS_Store' \
    --exclude 'config.yaml' \
    --exclude 'state.json' \
    --exclude 'price_history.jsonl' \
    --exclude 'generated' \
    --exclude 'manual_pdfs' \
    --exclude '.browser-cache' \
    --exclude '.pdf-cache' \
    --exclude '.playwright-browsers' \
    --exclude 'tmp' \
    "${SOURCE_DIR}/" "${APP_DIR}/"
fi

install -d -m 0755 "${APP_DIR}/generated" "${APP_DIR}/manual_pdfs" "${APP_DIR}/tmp"
install -d -m 0755 "${APP_DIR}/.browser-cache" "${APP_DIR}/.pdf-cache" "${APP_DIR}/.playwright-browsers"

if [[ ! -f "${APP_DIR}/config.yaml" ]]; then
  cat > "${APP_DIR}/config.yaml" <<'EOF'
settings:
  refresh_delay_seconds: "5"
  auto_refresh_interval_hours: "6"
  auto_refresh_enabled: "false"
  theme: "light"
  api_enabled: "true"

markets:

categories:
  - id: "allgemein"
    name: "Allgemein"

products:
EOF
  echo "Created empty local config: ${APP_DIR}/config.yaml"
else
  echo "Keeping existing config: ${APP_DIR}/config.yaml"
fi

if [[ ! -f "${APP_DIR}/state.json" ]]; then
  printf '{}\n' > "${APP_DIR}/state.json"
fi
if [[ ! -f "${APP_DIR}/price_history.jsonl" ]]; then
  : > "${APP_DIR}/price_history.jsonl"
fi

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip wheel
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

if "${APP_DIR}/.venv/bin/python" -m playwright --version >/dev/null 2>&1; then
  PLAYWRIGHT_BROWSERS_PATH="${APP_DIR}/.playwright-browsers" \
    "${APP_DIR}/.venv/bin/python" -m playwright install --with-deps chromium
fi

chown -R "${RUN_USER}:${RUN_USER}" \
  "${APP_DIR}/config.yaml" \
  "${APP_DIR}/state.json" \
  "${APP_DIR}/price_history.jsonl" \
  "${APP_DIR}/generated" \
  "${APP_DIR}/manual_pdfs" \
  "${APP_DIR}/tmp" \
  "${APP_DIR}/.browser-cache" \
  "${APP_DIR}/.pdf-cache" \
  "${APP_DIR}/.playwright-browsers"

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=${APP_NAME}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
Environment=PLAYWRIGHT_BROWSERS_PATH=${APP_DIR}/.playwright-browsers
ExecStart=${APP_DIR}/.venv/bin/gunicorn --bind ${INTERNAL_HOST}:${INTERNAL_PORT} --workers 1 --threads 4 --timeout 300 --access-logfile - --error-logfile - app:app
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

chmod +x "${APP_DIR}/scripts/gui_update.sh" "${APP_DIR}/scripts/update.sh"

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

cat > "${NGINX_SITE}" <<EOF
server {
    listen ${PUBLIC_PORT};
    server_name _;

    client_max_body_size ${CLIENT_MAX_BODY_SIZE};
    proxy_connect_timeout 60s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://${INTERNAL_HOST}:${INTERNAL_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;
    }
}
EOF

ln -sf "${NGINX_SITE}" "${NGINX_LINK}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo
  echo "${SERVICE_NAME} failed to start."
  systemctl status "${SERVICE_NAME}" --no-pager -l || true
  journalctl -u "${SERVICE_NAME}" -n 100 --no-pager || true
  exit 1
fi

nginx -t
systemctl reload nginx || systemctl restart nginx

echo
echo "${APP_NAME} installed."
echo "Open: http://YOUR-SERVER-IP:${PUBLIC_PORT}"
echo "App directory: ${APP_DIR}"
echo "Service: ${SERVICE_NAME}"
echo "Local config: ${APP_DIR}/config.yaml"
echo
echo "Use scripts/update.sh from the app directory for git based updates."
