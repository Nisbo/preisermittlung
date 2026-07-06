# Preisermittlung Home Assistant Card

`preisermittlung-card.js` ist eine Lovelace Custom Card für die MQTT-Sensoren
der Preisermittlung. Die Card braucht kein Build-System und keine externen
Abhängigkeiten.

## Installation

1. Datei nach Home Assistant kopieren:

   ```bash
   cp preisermittlung-card.js /config/www/preisermittlung-card.js
   ```

2. In Home Assistant unter `Einstellungen > Dashboards > Ressourcen` eintragen:

   - URL: `/local/preisermittlung-card.js`
   - Typ: `JavaScript-Modul`

3. Dashboard bearbeiten und Card hinzufügen:

   ```yaml
   type: custom:preisermittlung-card
   title: Katzenfutter Preise
   service_url: http://192.168.178.10:5050
   category_id: katzenfutter
   columns:
     - image
     - name
     - provider
     - shop
     - price
     - last_changed
   sort_by: name
   sort_dir: asc
   ```

## Beispiel mit Gruppierung

```yaml
type: custom:preisermittlung-card
title: Preisübersicht
service_url: http://192.168.178.10:5050
group_by_category: true
show_category_filter: true
show_search: true
show_target_price_filter: true
extra_matches_display: slider
extra_matches_expanded: true
columns:
  - image
  - name
  - provider
  - shop
  - shop_detail
  - price
  - unit_price
  - last_changed
sort_by: category
sort_dir: asc
```

## Wichtige Optionen

- `entity_prefix`: Prefix der Sensoren, Standard `sensor.preisermittlung_`
- `service_url`: Basis-URL der Preisermittlung-App für relative Bilder/PDF-Links, z.B. `http://192.168.178.10:5050`
- `category_id`: feste Kategorie, leer bedeutet alle
- `show_category_filter`: Kategorieauswahl in der Card anzeigen
- `show_search`: Suche in der Card anzeigen
- `show_target_price_filter`: Button für erreichte Wunschpreise anzeigen
- `target_price_filter_active`: Wunschpreis-Filter beim Laden direkt aktivieren
- `group_by_category`: Artikel nach Kategorie gruppieren
- `extra_matches_display`: Zusatztreffer als `wrap`, `slider` oder `off` anzeigen
- `extra_matches_expanded`: Zusatztreffer standardmäßig ausgeklappt anzeigen
- `target_price_highlight_enabled`: erreichte Wunschpreise farblich markieren
- `target_price_missed_display`: nicht erreichte Wunschpreise `hide`, `normal` oder `muted` anzeigen
- `target_price_extra_matches_enabled`: Wunschpreis auch bei Prospekt-Zusatztreffern anzeigen
- `columns`: sichtbare Spalten
- `sort_by`: `name`, `provider`, `shop`, `shop_detail`, `price`, `category`, `last_checked`, `last_changed`, `status`
- `sort_dir`: `asc` oder `desc`
- `image_size`: Bildgröße von 24 bis 96 Pixel
- `compact`: kompaktere Tabellenzeilen

## Spalten und MQTT-Attribute

| Spalte | MQTT-Attribut |
|---|---|
| `Bild` | `image_url` |
| `Artikel` | `name` |
| `Anbieter` | `provider_name` |
| `Shop` | `shop` |
| `Shopdetails` | `shop_detail` |
| `Preis` | Sensor-State, formatiert als Euro |
| `Grundpreis` | `unit_price` |
| `Packung` | `package_size` |
| `Kategorie` | `category` |
| `Status` | `status` |
| `Geprüft` | `last_checked` |
| `Geändert` | `last_changed` |
