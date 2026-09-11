# Go Gauge — Dashboards

Fertige Dashboard-Vorlagen (YAML), mit denen sich Nutzung, Limits, Pace und
Modell-Katalog eines Go-Gauge-Workspaces direkt in Home Assistant anzeigen
lassen. Beide Vorlagen sind reine YAML-Dateien ohne Python-Bezug; die
Integration selbst wird dadurch nicht verändert.

## 1. Überblick

| Datei | Layout | Custom-Ressourcen |
|---|---|---|
| [`workspace-standard.yaml`](workspace-standard.yaml) | Nur Home-Assistant-Bordmittel (markdown, grid, gauge, entities, history-graph) | keine |
| [`workspace-mushroom.yaml`](workspace-mushroom.yaml) | Mushroom-Karten (`custom:mushroom-*`) | Mushroom (via HACS) |
| [`entity-map.yaml`](entity-map.yaml) | maschinenlesbare Entity-/Bindungs-Map | keine |

Beide Aufsätze liefern denselben Informationsumfang:

- Kopfzeile mit Abo-Status, API-Status und Status/Note je Fenster,
- pro Fenster (5h rolling / Weekly / Monthly) Nutzung, Pace, Prognose,
  Restbudget, Restzeit, Burn-Rate und Reset bzw. Rate-Limit,
- einen Nutzungsverlauf,
- eine Steuerungs-Sektion (Warnschwelle, Ampel Rot-Grenze, Refresh-Intervalle,
  Auto-Update-Schalter, manueller Refresh),
- eine Katalog-/Konto-Sektion (Modelle, Live-Anzahl, günstigstes Modell,
  Free-Modelle, API-Erreichbarkeit).

Screenshots:

![Standard-Dashboard (Platzhalter)](docs/dashboards/screenshots/standard.png)
Standard-Dashboard — Screenshot folgt (Platzhalter).

![Mushroom-Dashboard (Platzhalter)](docs/dashboards/screenshots/mushroom.png)
Mushroom-Dashboard — Screenshot folgt (Platzhalter).

## 2. Voraussetzungen

### Standard (`workspace-standard.yaml`)

- Keine zusätzliche Integration und keine Custom-Ressourcen nötig.
- Home Assistant **>= 2024.6.0** (Mindestversion aus [`hacs.json`](../../hacs.json)).
- Die verwendeten Karten `grid`, `gauge`, `entities`, `markdown` und
  `history-graph` sind Bordmittel und in dieser Version verfügbar.

### Mushroom (`workspace-mushroom.yaml`)

- HACS installieren (falls nicht vorhanden).
- In HACS die Frontend-Integration **Mushroom** installieren
  (HACS → Frontend → Mushroom), danach Home Assistant neu laden.
- Mushroom stellt die Ressourcen `custom:mushroom-*` bereit. `card-mod` ist
  ausdrücklich **nicht** erforderlich.

### Optional / weiterführend (NICHT Pflicht)

Diese Ressourcen sind für die Vorlagen nicht nötig, können den Komfort aber
erhöhen:

| Ressource | Nutzen |
|---|---|
| `auto-entities` | Listen (z. B. Fenster- oder Modell-Zeilen) automatisch aus Filterregeln erzeugen, statt jede Entity einzeln zu pflegen. |
| `card-mod` | Feinere optische Anpassungen (Farben, Abstände). Für die mitgelieferten Vorlagen nicht erforderlich. |
| `config-template-card` | Entity-Listen in Templates dynamisch auflösen. Für die mitgelieferten Vorlagen nicht erforderlich. |

## 3. Installation

1. In Home Assistant ein neues Dashboard anlegen bzw. ein vorhandenes öffnen.
2. Das Dashboard in den **Rohkonfigurationseditor** wechseln.
3. Den Inhalt der gewünschten Vorlage
   (`workspace-standard.yaml` oder `workspace-mushroom.yaml`) einfügen.
4. Den Platzhalter `WS` durch den echten Workspace-Slug ersetzen
   (siehe Abschnitt 4 und 5).
5. Speichern.

## 4. Platzhalter- und Bindungskonzept

**`entity_id` ist NICHT stabil.** Home Assistant bildet die `entity_id` aus
dem *Friendly Name* — und der enthält den Workspace-Namen sowie ggf. die
Systemsprache. Ändert sich Name oder Sprache, ändert sich die `entity_id`.
Automatisierungen und Dashboards dürfen sich deshalb nicht auf die `entity_id`
verlassen.

**Stabil ist der `unique_id`-Suffix.** Jede Entity trägt eine `unique_id`
nach dem Muster `{entry_id}_{workspace_key}_{window}{suffix}` (fensterspezifisch)
bzw. `{entry_id}_{workspace_key}{suffix}` / `{entry_id}{suffix}`
(Workspace-/Konto-Ebene). Diese Suffixe sind die Wahrheit; siehe
[`entity-map.yaml`](entity-map.yaml).

**Die `WS`-Konvention:** In den Dashboard-Vorlagen steht `WS` als Platzhalter
für den Workspace-Slug *innerhalb der `entity_id`*. Vor der Verwendung muss
`WS` durch den realen Slug ersetzt werden. Den Slug niemals aus dem Friendly
Name raten, sondern die reale `entity_id` über den `unique_id`-Suffix aus der
Entity-Registry ermitteln (Abschnitt 5 b).

Fenster:

| `key` | `label` (Anzeige) | `entity_id_part` | `unique_id`-Fenster |
|---|---|---|---|
| `5h` | `5h rolling` | `5h_rolling` | `5h` |
| `week` | `Weekly` | `weekly` | `week` |
| `month` | `Monthly` | `monthly` | `month` |

## 5. KI-Anleitung (Dashboard an einen Workspace binden)

Schrittfolge für eine KI oder einen Menschen, die/der ein Dashboard zuverlässig
an eine reale Go-Gauge-Instanz bindet:

**a) Wahrheitsquelle im Repo lesen.** Welche Entity welchen `unique_id`-Suffix,
Namen und welche Attribute hat, steht im Integrationscode:

- `custom_components/go_gauge/sensor.py:108-109` (`usage_percent`)
- `custom_components/go_gauge/sensor.py:160-161` (`reset`)
- `custom_components/go_gauge/sensor.py:193-194` (`forecast`)
- `custom_components/go_gauge/sensor.py:215-216` (`pace`)
- `custom_components/go_gauge/sensor.py:255-256` (`remaining`)
- `custom_components/go_gauge/sensor.py:275-276` (`time_to_reset`)
- `custom_components/go_gauge/sensor.py:296-297` (`burn_rate`)
- `custom_components/go_gauge/binary_sensor.py:53-55` (`limited`)
- `custom_components/go_gauge/binary_sensor.py:90-91` (`subscription_active`)
- `custom_components/go_gauge/number.py:24-26,66-69` (`warn_percent`)
- `custom_components/go_gauge/switch.py:38-40,66-68`
  (`auto_update_usage`, `auto_update_models`)
- `custom_components/go_gauge/button.py:32,36` (`refresh`)
- `custom_components/go_gauge/entity.py:24-29` (Device-Info: Workspace-Gerät)

Die vollständige Liste samt Konto-Entities steht in
[`entity-map.yaml`](entity-map.yaml).

**b) Zur Laufzeit die Entity-Registry abfragen.** Per WebSocket
(`config/entity_registry/list`) oder REST (`/api/states`) die Entities der
Go-Gauge-Config-Entries auslesen und **über den `unique_id`-Suffix** matchen
(Tabelle in `entity-map.yaml`). Niemals `entity_id`s aus dem Friendly Name
raten.

**c) Workspace und Konto bestimmen.** Ein Workspace gehört zum Gerät
`Go Gauge {ws_name}` mit Identifier `go_gauge`/`<entry_id>`. Das Konto-Gerät
hat den Identifier `go_gauge`/`account` und existiert genau einmal
(Catalog Owner); seine Entities (Katalog, Live-Modelle, Günstigstes,
Free-Modelle, API erreichbar) existieren entsprechend genau einmal.

**d) Substitution.** `WS` durch den realen Workspace-Slug ersetzen. Alternativ
in Jinja-Templates dynamisch auflösen, indem über die Attribute
`workspace_key` und `window` der Nutzungs-Sensoren der passende Sensor
gesucht wird (z. B. per `states.sensor`-Schleife) — das ist robuster gegen
umbenannte Workspaces.

**e) Katalog lesen.** Modell-Informationen gibt es **nur** über die Attribute
des `_model_catalog`-Sensors (`catalog_json`, `ranking_by_cost`,
`free_models`, `cheapest_model`, `cheapest_overall`, `count`, `live_count`,
`models_updated_at`). Es existieren **keine** Einzel-Entities pro Modell.

## 6. FAQ / Known Gotchas

- **Button-Name kollidiert bei mehreren Workspaces.** Der Refresh-Button heißt
  ohne Workspace-Namen `Go Gauge Aktualisieren`; bei mehreren Workspaces wird
  seine `entity_id` von Home Assistant zu `..._2` (und so weiter) abgeändert.
  Die reale `entity_id` deshalb immer über den `unique_id`-Suffix `_refresh`
  aus der Registry holen.
- **`_pace`-State ist ein englischer String.** Mögliche Werte: `green`,
  `yellow`, `red` (nicht übersetzt). Die Schwellen kommen aus den
  Number-Entities Warnschwelle und Ampel Rot-Grenze.
- **`_forecast` kann größer als 100 sein.** Die Prognose rechnet die aktuelle
  Nutzung linear auf das Fensterende hoch — Überschreitung ist gewollt und
  zeigt, dass das Tempo das Budget sprengt. Die `gauge`-Karte in der
  Standard-Vorlage ist trotzdem auf `max: 100` begrenzt; den Prognose-Wert
  deshalb zusätzlich als Entity anzeigen.
- **Ohne Abo sind Nutzungs-Sensoren `unknown` (nicht 0).** Bei fehlendem Abo
  liefern die prozentualen Sensoren bewusst `unknown`, und der Binary Sensor
  `... Abo aktiv` steht auf `off`. `unknown` nicht als 0 interpretieren.
- **Mushroom-Karten: „Entity not available".** `custom:mushroom-*`-Karten
  lösen die Entity bei mehreren Workspaces über die `entity_id` auf; fehlt
  sie, zeigt die Karte „Entity not available". Dann die reale `entity_id`
  eintragen (unique_id-Suffix-Match).
