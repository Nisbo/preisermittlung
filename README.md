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
./scripts/install_debian.sh
```

Beim interaktiven Start schlägt der Installer Port `5151` vor. Du kannst ihn
ändern, zum Beispiel auf `80`, wenn dort sicher kein anderer Dienst läuft.
Der Installer muss als root laufen. Wenn du nicht per `su -` als root angemeldet
bist, nutze `sudo ./scripts/install_debian.sh`.
Ohne Interaktion kann der Port per Umgebungsvariable gesetzt werden:

```bash
PREISERMITTLUNG_PUBLIC_PORT=5151 ./scripts/install_debian.sh
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
ist. Der Installer legt nur eine zusätzliche nginx-Site für Preisermittlung an.
Wenn nginx bereits installiert ist, wird die vorhandene Installation
weiterverwendet.

## Lokale Daten

Diese Dateien und Ordner entstehen erst auf deiner Installation und gehören
nicht ins Git-Repo:

- `config.yaml`
- `state.json`
- `price_history.jsonl`
- `generated/`
- `manual_pdfs/`
- `.browser-cache/`
- `.pdf-cache/`
- `.playwright-browsers/`
- `tmp/`

Updates dürfen diese Dateien nicht überschreiben. `config.yaml`, `state.json`,
`price_history.jsonl` und `manual_pdfs/` enthalten Nutzerdaten. `generated/`,
`.browser-cache/`, `.pdf-cache/`, `.playwright-browsers/` und `tmp/` sind
Cache- oder Laufzeitdaten und können bei Bedarf neu erzeugt werden.

## Aufbau der `config.yaml`

Die Konfiguration ist absichtlich so aufgebaut, dass sie auch manuell
bearbeitet werden kann:

- `settings`: globale Einstellungen der App
- `markets`: gespeicherte Märkte, zum Beispiel REWE-Filialen
- `categories`: Kategorien für Artikel
- `products`: überwachte Artikel, Prospekt-Suchwörter oder Webseitenpreise

Einen separaten `store`- oder Default-Markt gibt es im aktuellen Format nicht
mehr. Wenn ein Artikel einen Markt braucht, steht direkt am Artikel der
`provider` und die passende `market_id`. Dadurch ist eindeutig, welcher Artikel
zu welchem Markt gehört.

Alte Configs mit `store:` werden beim Lesen noch automatisch verstanden und beim
nächsten Speichern in die `markets`/`products`-Struktur übernommen.

## Update per Git

Empfohlen ist das Update-Script auf dem Server:

```bash
cd /opt/preisermittlung
./scripts/update.sh
```

Das Script führt `git pull --ff-only` aus, aktualisiert die Python-Abhängigkeiten
und startet den systemd-Service neu. Lokale Runtime-Daten werden nicht gelöscht
oder überschrieben.
Auch das Update-Script muss als root laufen. Falls du nicht bereits root bist,
nutze `sudo ./scripts/update.sh`.

In der Weboberfläche gibt es unter `Settings > Update` zusätzlich ein
Serverupdate per Klick. Die Web-App startet dabei nur den vordefinierten
systemd-Job `preisermittlung-update.service`; dieser läuft als root und ruft
intern `scripts/update.sh` auf. Dafür legt der Installer eine eng begrenzte
sudoers-Regel an, die nur das Starten dieses einen Update-Jobs erlaubt.

Wenn eine bestehende Installation diesen Update-Job noch nicht hat, führe einmal
manuell das Server-Script aus. Danach ist der Button in der GUI verfügbar:

```bash
cd /opt/preisermittlung
./scripts/update.sh
```

## Deinstallation

Das Deinstallationsscript stoppt den Dienst, entfernt die systemd-Datei und die
nginx-Site dieser App. Danach fragt es, ob das komplette App-Verzeichnis
gelöscht werden soll.

```bash
cd /opt/preisermittlung
./scripts/uninstall_debian.sh
```

Wenn das App-Verzeichnis gelöscht wird, werden auch Konfiguration, Status,
hochgeladene PDFs, generierte Bilder, `.venv`, `.playwright-browsers` und
Cache-Dateien entfernt. Systempakete wie nginx, Python, poppler-utils oder
Playwright-Bibliotheken werden nicht automatisch deinstalliert, weil sie schon
vorher installiert gewesen sein oder von anderen Anwendungen gebraucht werden
können. Nicht mehr benötigte apt-Pakete kann man danach bei Bedarf manuell mit
`sudo apt autoremove` prüfen.
Auch das Deinstallationsscript muss als root laufen. Falls du nicht bereits root
bist, nutze `sudo ./scripts/uninstall_debian.sh`.

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
- `scripts/uninstall_debian.sh`: Deinstallationsscript für die App-Konfiguration
  und optional das App-Verzeichnis.
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

Für PDF- und Backup-Uploads ist nginx im Installer mit
`client_max_body_size 512M` vorbereitet. Bei Bedarf kann der Wert vor
Installation oder Update mit `PREISERMITTLUNG_CLIENT_MAX_BODY_SIZE` angepasst
werden.
