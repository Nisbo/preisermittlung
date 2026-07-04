# Preisermittlung

Preisermittlung ist eine kleine Web-App zur Preisüberwachung von Shops,
Prospekten und beliebigen Webseiten. Die App kann Preise manuell oder
automatisch aktualisieren und Daten per JSON-API oder MQTT für Home Assistant
bereitstellen.

## Status

Früher Entwicklungsstand. Für den ersten Test ist eine lokale Debian-VM oder
ein Proxmox-LXC empfohlen.

## Anbieter

Aktuell vorbereitet oder implementiert:

- REWE mit Marktauswahl und Abholservice
- Müller
- MediaMarkt
- ALDI Süd
- Rossmann
- PDF-Prospekte, inklusive manuell hochgeladener PDFs
- generische Webseitenauswertung für einzelne Preise

## Debian-Installation

Die Installation nutzt Gunicorn intern und nginx für den Zugriff aus dem LAN.
Standardmäßig lauscht nginx auf Port `5151`. Der interne Gunicorn-Port bleibt
auf `127.0.0.1:5050`.

```bash
cd /opt
git clone https://github.com/Nisbo/preisermittlung.git
cd preisermittlung
sudo bash scripts/install_debian.sh
```

Beim interaktiven Start schlägt der Installer Port `5151` vor. Du kannst ihn
ändern, zum Beispiel auf `80`, wenn dort sicher kein anderer Dienst läuft.
Ohne Interaktion kann der Port per Umgebungsvariable gesetzt werden:

```bash
sudo PREISERMITTLUNG_PUBLIC_PORT=5151 bash scripts/install_debian.sh
```

Nach der Installation:

```text
http://SERVER-IP:5151
```

Der Port `5050` ist nur der interne App-Port auf dem Server. Im normalen
LAN-Betrieb rufst du die App über nginx auf, also standardmäßig über Port
`5151`.

## Was der Installer macht

- installiert Systempakete wie Python, nginx, rsync, poppler-utils und Build-Abhängigkeiten
- erstellt oder aktualisiert `/opt/preisermittlung`
- erstellt `.venv`
- installiert nur Runtime-Abhängigkeiten aus `requirements.txt`
- installiert Playwright Chromium für Anbieter, die echte Browserausführung brauchen
- erstellt lokale Runtime-Ordner
- erstellt eine leere lokale `config.yaml`, falls noch keine existiert
- erstellt `preisermittlung.service`
- erstellt eine nginx-Site auf Port `5151`
- startet Service und nginx neu

nginx selbst wird als Debian-Paket installiert, falls es noch nicht vorhanden
ist. Dieses Paket bringt den systemd-Service `nginx.service` mit. Der Installer
legt also keinen eigenen nginx-Service an, sondern nur eine zusätzliche
nginx-Site für Preisermittlung. Wenn nginx bereits installiert ist, wird die
vorhandene Installation weiterverwendet.

## Lokale Daten

Diese Dateien und Ordner sind lokale Runtime-Daten und gehören nicht ins Git-Repo:

- `config.yaml`
- `state.json`
- `generated/`
- `manual_pdfs/`
- `.browser-cache/`
- `.pdf-cache/`
- `.playwright-browsers/`
- `tmp/`

Updates dürfen diese Dateien nicht überschreiben.

## Update per Git

Wenn die App per Git installiert wurde:

```bash
cd /opt/preisermittlung
sudo bash scripts/update.sh
```

Das Script führt `git pull --ff-only` aus, aktualisiert die Python-Abhängigkeiten
und startet den systemd-Service neu. Lokale Runtime-Daten werden nicht gelöscht
oder überschrieben.

## Lokales Entwickeln

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Danach:

- Webansicht: `http://127.0.0.1:5050/`
- JSON: `http://127.0.0.1:5050/api/prices`
- Healthcheck: `http://127.0.0.1:5050/health`

## Home Assistant Card

Die Custom Card liegt unter:

```text
deploy/home-assistant/preisermittlung-card.js
```

Die App kann per MQTT Home-Assistant-Discovery-Payloads und Statusdaten senden.
Die eigentliche MQTT-Konfiguration befindet sich in der Settings-Seite.

## Hinweise zu `requirements.txt`

`requirements.txt` enthält nur Laufzeit-Abhängigkeiten für den Serverbetrieb.
Lokale Probe-Scripts und Entwicklungstools gehören nicht hinein.

## Wichtige Dateien

- `app.py`: Flask-Web-App mit GUI, Settings, JSON-API, Scheduler,
  MQTT-Testbereich, MQTT-Payloads und den meisten Web-Routen.
- `providers.py`: zentrale Anbieter-Registry. Hier wird entschieden, welches
  Reader-Modul für welchen Anbieter zuständig ist.
- `config_io.py`: Lesen und Schreiben der lokalen `config.yaml`.
- `readers/`: Paket mit den eingebauten Auslesemodulen. Das trennt die
  Anbieterlogik vom Rest der App und ist die vorbereitete Stelle für spätere
  zusätzliche Reader. Aktuell müssen neue Reader noch in `providers.py`
  registriert werden.
- `readers/rewe_reader.py`: REWE-Auslesemodul.
- `readers/mueller_reader.py`: Müller-Auslesemodul.
- `readers/mediamarkt_reader.py`: MediaMarkt-Auslesemodul.
- `readers/aldi_sued_reader.py`: ALDI-Süd-Auslesemodul mit Playwright/Chromium.
- `readers/rossmann_reader.py`: Rossmann-Auslesemodul.
- `readers/generic_reader.py`: generische Webseitenauswertung für beliebige URLs,
  inklusive einfacher HTTP-Methode und erweiterter Playwright-Methode.
- `readers/aez_pdf_reader.py`: PDF-Prospektmodul für online bereitgestellte
  Wochenprospekte.
- `readers/manual_pdf_reader.py`: Modul für manuell hochgeladene PDF-Prospekte.
- `core/price_details.py`: gemeinsame Normalisierung von Packungsgröße und
  Grundpreis für alle Reader.
- `price_reader.py`: einfache CLI-Ausgabe als JSON, nützlich für Tests.
- `gunicorn.conf.py`: Gunicorn-Konfiguration für lokalen oder manuellen Start.
  Der Debian-Service nutzt aktuell eigene Gunicorn-Parameter in der
  systemd-Datei.
- `requirements.txt`: Python-Laufzeitabhängigkeiten für die Installation.
- `scripts/install_debian.sh`: Installer für Debian/Proxmox/LXC.
- `scripts/update.sh`: Update-Script für Git-basierte Installationen.
- `deploy/preisermittlung.service`: Vorlage für den systemd-Service.
- `deploy/nginx-preisermittlung.conf`: Vorlage für die nginx-Site.
- `deploy/home-assistant/preisermittlung-card.js`: Home-Assistant-Custom-Card.

## Was wird installiert?

- Python und `.venv`: Die App läuft in einer eigenen Python-Umgebung, damit die
  Abhängigkeiten nicht global ins System gemischt werden.
- Flask: Das Web-Framework der App.
- Gunicorn: Der interne Produktionsserver für Flask. Er läuft nur lokal auf dem
  Server unter `127.0.0.1:5050`.
- nginx: Der Webserver davor. nginx nimmt die Anfragen aus dem LAN an,
  standardmäßig auf `http://SERVER-IP:5151`, und leitet sie intern an Gunicorn
  weiter.
- systemd: Startet und überwacht `preisermittlung.service`, damit die App nach
  einem Neustart automatisch wieder läuft.
- Playwright/Chromium: Ein echter Browser für Anbieter oder Webseiten, die ohne
  JavaScript-Ausführung keine brauchbaren Preise liefern.
- poppler-utils und pdfplumber: Werkzeuge zum Lesen und Auswerten von
  PDF-Prospekten.
- Pillow: Bildverarbeitung für Produktbilder und PDF-Treffer-Vorschauen.
- paho-mqtt: MQTT-Client für Home Assistant Discovery und Statusmeldungen.

Für PDF-Uploads ist nginx im Installer mit `client_max_body_size 100M`
vorbereitet.
