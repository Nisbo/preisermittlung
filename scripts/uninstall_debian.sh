#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${PREISERMITTLUNG_APP_DIR:-/opt/preisermittlung}"
SERVICE_NAME="${PREISERMITTLUNG_SERVICE:-preisermittlung}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_SITE="/etc/nginx/sites-available/${SERVICE_NAME}.conf"
NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}.conf"

ask_yes_no() {
  local question="$1"
  local default_answer="${2:-no}"
  local prompt="[y/N]"
  if [[ "$default_answer" == "yes" ]]; then
    prompt="[Y/n]"
  fi

  if [[ ! -t 0 ]]; then
    [[ "$default_answer" == "yes" ]]
    return
  fi

  read -r -p "${question} ${prompt} " answer
  answer="${answer:-$default_answer}"
  case "${answer,,}" in
    y|yes|j|ja) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bitte als root starten, z.B.: sudo ./scripts/uninstall_debian.sh"
  exit 1
fi

echo "Preisermittlung Deinstallation"
echo "App-Verzeichnis: ${APP_DIR}"
echo

if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1; then
  echo "Stoppe und deaktiviere ${SERVICE_NAME}.service ..."
  systemctl stop "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
  systemctl disable "${SERVICE_NAME}.service" >/dev/null 2>&1 || true
fi

if [[ -f "${SERVICE_FILE}" ]]; then
  echo "Entferne ${SERVICE_FILE} ..."
  rm -f "${SERVICE_FILE}"
  systemctl daemon-reload
fi

if [[ -L "${NGINX_LINK}" || -f "${NGINX_LINK}" ]]; then
  echo "Entferne nginx-Aktivierung ${NGINX_LINK} ..."
  rm -f "${NGINX_LINK}"
fi

if [[ -f "${NGINX_SITE}" ]]; then
  echo "Entferne nginx-Konfiguration ${NGINX_SITE} ..."
  rm -f "${NGINX_SITE}"
fi

if command -v nginx >/dev/null 2>&1; then
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx >/dev/null 2>&1 || systemctl restart nginx >/dev/null 2>&1 || true
  else
    echo "Hinweis: nginx-Konfiguration ist nicht valide. Bitte 'nginx -t' manuell pruefen."
  fi
fi

if [[ -d "${APP_DIR}" ]]; then
  echo
  echo "Das App-Verzeichnis enthaelt Konfiguration, Status, hochgeladene PDFs und generierte Bilder."
  if ask_yes_no "App-Verzeichnis wirklich komplett loeschen?" "no"; then
    rm -rf --one-file-system "${APP_DIR}"
    echo "App-Verzeichnis geloescht."
  else
    echo "App-Verzeichnis bleibt erhalten."
  fi
fi

echo
echo "Systempakete wie nginx, Python, Playwright-Bibliotheken und poppler-utils wurden nicht entfernt."
echo "Sie koennen schon vorher installiert gewesen sein oder von anderen Anwendungen benoetigt werden."
echo "Optional kannst du spaeter manuell pruefen: sudo apt autoremove"
echo "Deinstallation abgeschlossen."
