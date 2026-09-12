# Go Gauge — Dashboards

Fertige Dashboard-Vorlagen (YAML), mit denen sich Nutzung, Limits, Pace und
Modell-Katalog eines Go-Gauge-Workspaces direkt in Home Assistant anzeigen
lassen. Alle Vorlagen sind reine YAML-Dateien ohne Python-Bezug; die
Integration selbst wird dadurch nicht verändert.

## 1. Überblick

| Datei | Layout | Custom-Ressourcen |
|---|---|---|
| [`workspace-standard.yaml`](workspace-standard.yaml) | Nur Home-Assistant-Bordmittel (markdown, grid, gauge, entities, history-graph) | keine |
| [`workspace-mushroom.yaml`](workspace-mushroom.yaml) | Mushroom-Karten (`custom:mushroom-*`) | Mushroom (via HACS) |
| [`workspace-matrix.yaml`](workspace-matrix.yaml) | Multi-Workspace-Matrix: `decluttering-card-plus`-Kachel je Workspace im `layout-card`-Grid, Kacheln im Mushroom-Look | decluttering-card-plus, layout-card, Mushroom, vertical-stack-in-card |
| [`entity-map.yaml`](entity-map.yaml) | maschinenlesbare Entity-/Bindungs-Map | keine |

Die Standard- und die Mushroom-Vorlage liefern denselben Informationsumfang
(die Matrix-Vorlage zeigt dieselben Fenster- und Steuerungswerte je Workspace
als Kachel; der **Nutzungsverlauf** fehlt dort bewusst, der **Modell-Katalog**
ist als Konto-Panel vorhanden):

- Kopfzeile mit Abo-Status, API-Status und Status/Note je Fenster,
- pro Fenster (5h rolling / Weekly / Monthly) Nutzung, Pace, Prognose,
  Restbudget, Restzeit, Burn-Rate und Reset bzw. Rate-Limit,
- einen Nutzungsverlauf,
- eine Steuerungs-Sektion (Warnschwelle, Ampel Rot-Grenze, Refresh-Intervalle,
  Auto-Update-Schalter, manueller Refresh),
- eine Katalog-/Konto-Sektion (Models, Live Models, Cheapest Model,
  Free Models, API Reachable).

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

### Matrix (`workspace-matrix.yaml`)

- HACS installieren (falls nicht vorhanden) und im HACS-Frontend **alle vier**
  Ressourcen installieren, danach Home Assistant neu laden:
  - **decluttering-card-plus** (`custom:decluttering-card-plus`) — Kachel-Template
    `go_gauge_workspace`. Nicht im HACS-Default-Katalog, daher als HACS
    **Custom Repository** (Typ „Dashboard", URL
    `https://github.com/tempus2016/decluttering-card-plus`) hinzufügen —
    alternativ die Ressource `decluttering-card-plus.js` manuell einbinden,
  - **layout-card** (`custom:layout-card`) — responsives Grid-Layout,
  - **Mushroom** (`custom:mushroom-*`) — Karten der Kachel,
  - **vertical-stack-in-card** (`custom:vertical-stack-in-card`) — Kachel-Container.
- Home Assistant **>= 2024.7** (Badges ab 2024.8). Die Karte registriert optional
  auch den Alt-Typ `custom:decluttering-card`; die Vorlage nutzt ausschließlich
  `custom:decluttering-card-plus`.
- `card-mod` ist ausdrücklich **nicht** erforderlich.

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
3. Den Inhalt der gewünschten Vorlage (`workspace-standard.yaml`,
   `workspace-mushroom.yaml` oder `workspace-matrix.yaml`) einfügen.
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
(Catalog Owner); seine Entities (Models, Live Models, Cheapest Model,
Free Models, API Reachable) existieren entsprechend genau einmal.

**d) Substitution.** `WS` durch den realen Workspace-Slug ersetzen. Alternativ
in Jinja-Templates dynamisch auflösen, indem über die Attribute
`workspace_key` und `window` der Nutzungs-Sensoren der passende Sensor
gesucht wird (z. B. per `states.sensor`-Schleife) — das ist robuster gegen
umbenannte Workspaces.

**e) Katalog lesen.** Modell-Informationen gibt es **nur** über die Attribute
des `_model_catalog`-Sensors (`catalog_json`, `ranking_by_cost`,
`free_models`, `cheapest_model`, `cheapest_overall`, `count`, `live_count`,
`models_updated_at`). Es existieren **keine** Einzel-Entities pro Modell.

## 6. `workspace-matrix.yaml` — Variablen & Bindung

`workspace-matrix.yaml` zeigt **mehrere Workspaces nebeneinander**: ein
`custom:decluttering-card-plus`-Template (`go_gauge_workspace`) wird pro
Workspace einmal instanziiert, das Grid kommt von `custom:layout-card`.

### Benötigte Custom-Ressourcen

Alle vier via HACS installieren (decluttering-card-plus als **Custom
Repository**, Typ „Dashboard", siehe Abschnitt 2), danach Home Assistant neu
laden:

| Ressource | Aufgabe |
|---|---|
| `decluttering-card-plus` (`custom:decluttering-card-plus`) | Kachel-Template `go_gauge_workspace` inkl. `[[variablen]]` |
| `layout-card` | responsives Grid (`grid-template-columns`) |
| Mushroom | Karten der Kachel (`custom:mushroom-*`) |
| `vertical-stack-in-card` | Container, der die Karten einer Kachel zusammenfasst |

### `[[...]]`-Variablen

Die Kachel ist über sieben Variablen parametrisiert. Sie werden pro
`custom:decluttering-card-plus` im `variables:`-Block gebunden:

| Variable | Bedeutung | Beispiel |
|---|---|---|
| `[[ws_name]]` | Anzeigename der Kachel (frei wählbar) | `"App"` |
| `[[slug_a]]` | `entity_id`-Präfix für Entities mit **trailing** Fragment: `usage`, `reset`, `rate_limited`, `subscription_active`, `auto_update_usage`, `auto_update_models` | `"go_gauge_ha_go_gauge_app"` |
| `[[slug_b]]` | `entity_id`-Präfix für Entities mit **trailing** Fragment: `forecast`, `pace`, `remaining`, `time_to_reset`, `burn_rate` | `"software_go_gauge_app"` |
| `[[num_slug]]` | `entity_id`-Präfix für `number`-Entities mit **leading** Fragment: `<device>_go_gauge` | `"go_gauge_ha_go_gauge"` |
| `[[num_suffix]]` | Workspace-Suffix der `number`-Entities | `"_app"` |
| `[[ampel_id]]` | **volle** `entity_id` der Pace-Red-Limit-Number: `number.<device>_go_gauge_pace_red_limit_<workspace>` | `"number.software_go_gauge_app_go_gauge_pace_red_limit_app"` |
| `[[refresh_btn]]` | **volle** `entity_id` des Refresh-Buttons: `button.<...>_refresh` | `"button.go_gauge_ha_go_gauge_refresh_3"` |

In der Kachel ergibt sich damit z. B. `sensor.[[slug_a]]_5h_rolling_usage`,
`sensor.[[slug_b]]_weekly_forecast` oder
`number.[[num_slug]]_warning_threshold[[num_suffix]]`. Pro Fenster
(5h rolling / Weekly / Monthly) gibt es genau **eine** kompakte
`custom:mushroom-template-card`: `primary` zeigt die Nutzung mit `%`,
`secondary` die Werte mehrzeilig (`Prog`, `Pace`, `Rem`, `Reset`, `Restzeit`
in `h`, `Burn` in `%/h`; unbekannte/`unavailable`-States werden als `–`
gerendert). Das Rate-Limit wird **nicht** mehr über eine separate Chip,
sondern über Icon (Warn-Icon), rote `icon_color` und den Hinweis
„RATE-LIMITED" im `secondary` signalisiert, sobald
`binary_sensor.[[slug_a]]_<fenster>_rate_limited` den State `on` hat. `tap_action`
bleibt `more-info`.

Der Abo-/API-Status ist aufgeteilt: Der Abo-Status (`subscription_active`)
bleibt icon-only im Kachel-Header, die **API-Reachability**
(`binary_sensor.go_gauge_api_reachable`) liegt als konto-weite Kopfzeile
**über** dem Grid (siehe unten). Unterhalb des Grids steht das Konto-Panel
„Modell-Katalog / Konto" mit `sensor.go_gauge_models`,
`sensor.go_gauge_live_models`, `sensor.go_gauge_cheapest_model` und
`sensor.go_gauge_free_models` — diese Entities existieren nur einmal
(Catalog Owner).

> **Hinweis — Account-`entity_id`s illustrativ:** Die hier und in der globalen
> Kopfzeile genannten Account-`entity_id`s (`sensor.go_gauge_models`,
> `sensor.go_gauge_live_models`, `sensor.go_gauge_cheapest_model`,
> `sensor.go_gauge_free_models`, `binary_sensor.go_gauge_api_reachable`) sind
> **illustrativ** und können je nach Device-Prefix/Systemsprache abweichen.
> Verbindlich ist der `unique_id`-Suffix-Match aus der Entity-Registry:
> `_model_catalog`, `_models_live_count`, `_cheapest_model`, `_free_models`,
> `_api_reachable` (siehe [`entity-map.yaml`](entity-map.yaml)).

### Bindungs-Caveat

`slug_a` und `slug_b` können sich unterscheiden: Nach der v7-Migration tragen
bereits registrierte Entities ein anderes Device-Präfix als neu registrierte
(siehe [`E2E-v7-entity-id-migration-2026-09-12.md`](../E2E-v7-entity-id-migration-2026-09-12.md),
Befund ND-1). Die Slugs deshalb **nie aus dem Friendly Name raten**, sondern
die reale `entity_id` über den `unique_id`-Suffix aus der Entity-Registry
auflösen ([`entity-map.yaml`](entity-map.yaml), Abschnitt 5 b). Der
Refresh-Button `button.<...>_refresh` ist workspace-unabhängig benannt und
kollidiert bei mehreren Workspaces (`..._2`, `..._3`, …) — seine reale
`entity_id` daher ebenfalls aus der Registry holen (unique_id-Suffix
`_refresh`).

> **Slug-Sprache:** Seit v1.5.0 sind die `entity_id`-Slugs englisch
> (`*_usage`, `*_forecast`, `*_remaining`, `*_time_to_reset`, …;
> Quelle: `custom_components/go_gauge/const.py`, `ENTITY_ID_MIGRATION`).
> Deutsche Legacy-Slugs (`*_nutzung`, `*_prognose`, `*_restbudget`,
> `*_restzeit`) sind in Dashboards **nicht** mehr gültig.

## 7. FAQ / Known Gotchas

- **Button-Name kollidiert bei mehreren Workspaces.** Der Refresh-Button heißt
  ohne Workspace-Namen `Go Gauge Refresh`; bei mehreren Workspaces wird
  seine `entity_id` von Home Assistant zu `..._2` (und so weiter) abgeändert.
  Die reale `entity_id` deshalb immer über den `unique_id`-Suffix `_refresh`
  aus der Registry holen.
- **`_pace`-State ist ein englischer String.** Mögliche Werte: `green`,
  `yellow`, `red` (nicht übersetzt). Die Schwellen kommen aus den
  Number-Entities Warning Threshold und Pace Red Limit.
- **`_forecast` kann größer als 100 sein.** Die Prognose rechnet die aktuelle
  Nutzung linear auf das Fensterende hoch — Überschreitung ist gewollt und
  zeigt, dass das Tempo das Budget sprengt. Die `gauge`-Karte in der
  Standard-Vorlage ist trotzdem auf `max: 100` begrenzt; den Prognose-Wert
  deshalb zusätzlich als Entity anzeigen.
- **Ohne Abo sind Nutzungs-Sensoren `unknown` (nicht 0).** Bei fehlendem Abo
  liefern die prozentualen Sensoren bewusst `unknown`, und der Binary Sensor
  `... Subscription Active` steht auf `off`. `unknown` nicht als 0 interpretieren.
- **Mushroom-Karten: „Entity not available".** `custom:mushroom-*`-Karten
  lösen die Entity bei mehreren Workspaces über die `entity_id` auf; fehlt
  sie, zeigt die Karte „Entity not available". Dann die reale `entity_id`
  eintragen (unique_id-Suffix-Match).
