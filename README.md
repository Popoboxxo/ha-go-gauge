# Go Gauge HA

**The gauge for your OpenCode Go budget — usage, limits & cost-ratio right on your dashboard.**

[![Release](https://img.shields.io/github/v/release/Popoboxxo/ha-go-gauge?sort=semver&display_name=tag)](https://github.com/Popoboxxo/ha-go-gauge/releases) [![Validate](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/validate.yml) [![Release Check](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/release-check.yml/badge.svg?branch=main)](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/release-check.yml) [![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/use/repositories/) [![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8%2B-41BDF5.svg)](https://www.home-assistant.io/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Eigenständige [HACS](https://hacs.xyz)-Custom-Integration, die **direkt** gegen die
OpenCode-Go-API (`opencode.ai`) spricht — kein Monitor, kein Dashboard, keine
Zusatzkomponente. Nur dein Workspace-API-Token.

## Features

- 🏷️ **Eine Workspace-Instanz pro Workspace** — sprechender Name + ein Token; mehrere
  Workspaces bedeuten mehrere Integrations-Instanzen (siehe [Mehrere Workspaces](#mehrere-workspaces))
- 🧾 **Genau eine Konto-Instanz** — wird beim ersten Workspace automatisch angelegt
  (`Go Gauge Konto`, `unique_id = go_gauge_account`), **nie manuell**: Modell-Katalog,
  Erreichbarkeit, die globalen Konto-Einstellungen (Katalog-Auto-Refresh + Intervall)
  und ein eigener Katalog-Refresh. Die Konto-Daten sind workspace-unabhängig und laufen
  weiter, auch wenn der letzte Workspace entfernt wird.
- 📈 **Prozentuale Nutzung** je Fenster (5h rolling / Weekly / Monthly)
- ⏰ **Konkrete Reset-Zeiten** als Timestamp-Entitäten — automationstauglich
- 🔮 **Prognose je Fenster**: lineare Hochrechnung der aktuellen Nutzung auf das
  Fensterende (kann über 100 % gehen — zeigt, ob das Tempo das Budget sprengt)
- 🚦 **Pace-Ampel je Fenster**: `green`/`yellow`/`red` je nach Prognose, Grenzen
  einstellbar über **Warning Threshold** (Grün/Gelb) und **Pace Red Limit** (Gelb/Rot,
  Default 100 %) — beides Number-Entities, live anpassbar
- 🚦 **Rate-Limit-Warnung** als Binary Sensor je Fenster + **Abo-Status** als eigener
  Binary Sensor (403 „EntitlementError" = kein aktives Abo, klar von einem
  Rate-Limit/Netzfehler unterschieden)
- 📦 **Modell-Katalog als EINEN Sensor** (`sensor.go_gauge_models` auf dem Konto-Gerät):
  komplettes, dynamisches Verzeichnis in den Attributen (`catalog_json`, `ranking_by_cost`, …) —
  neue Modelle erscheinen automatisch, ohne neue Entitäten.
- 🏆 Kompakt-Sensoren (Konto-Gerät): Live Models · Cheapest Model · Free Models
- 🛰️ **Evidenz-basierte Erreichbarkeit** — `api_reachable` (Konto-Gerät) ist ON bei
  mindestens einem frischen Erfolg, OFF nur bei ausschließlich frischen Fehlschlägen und
  `unknown`, wenn die API zwar antwortet, aber keinen Erfolg belegt (z. B. 401 oder kein Abo).
  „Frisch" heißt innerhalb der TTL: Katalog 2× Konto-Intervall (Fallback 120 min bei
  Auto-Refresh aus), Nutzung 3× Workspace-Intervall.
- 🔘 **Zwei Refresh-Buttons**: der Workspace-Button „Refresh" lädt **nur die Nutzung**,
  der Konto-Button auf dem Konto-Gerät lädt **nur den Modell-Katalog** — beide unabhängig
  von den Auto-Zyklen.
- ⚙️ **Getrennte Auto-Update-Zyklen**, live umschaltbar über eigene Switch-/Number-Entities
  (kein Neustart/Reload nötig) **oder** über die Optionen:
  | Zyklus | Ebene | Ein/Aus | Default |
  |--------|-------|---------|---------|
  | Nutzung (Usage) | Workspace | ✅ an | alle **10 Min** |
  | Modell-Katalog | Konto (global) | ✅ an | alle **60 Min** (1–1440 einstellbar) |
  Beide abschaltbar; dann aktualisiert nur der jeweilige Refresh-Button.
- 🔔 **Warning Threshold** (Default 80 %) als Number-Entity — live anpassbar, z. B. für
  eigene Automationen oberhalb des eingebauten Prozent-Sensors
- 🛟 **Offline-freundliches Setup**: Token wird beim Speichern live geprüft, aber bei
  Netzproblemen kannst du die Prüfung überspringen und trotzdem speichern. Schlägt der
  allererste Abruf beim HA-Start fehl, bricht das Setup nicht ab — HA versucht es im
  Hintergrund automatisch erneut.
- 🩺 **Tiefen-Diagnose**: Der Diagnostics-Export zeigt sofort Ursache und Handlungsempfehlung
  (z. B. „kein aktives Abo" vs. „Cloudflare-Rate-Limit"), ohne Rückfragen im Chat.

## Installation (HACS)

1. HACS → Integrations → ⋮ → *Custom repositories*
2. `https://github.com/Popoboxxo/ha-go-gauge` · Kategorie: `Integration`
3. Installieren → Home Assistant neu starten

## Einrichtung

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen* → **Go Gauge HA**
2. Sprechenden **Namen** für diesen Workspace vergeben (z. B. `Team`, `Privat`) sowie den
   OpenCode-Go-API-**Token** einfügen (Keys: OpenCode-UI → Workspace → Go → API Keys)
3. Optionen (Zahnrad auf dem Gerät) **oder** die Switch-/Number-Entities des Geräts:
   Nutzungs-Zyklus und Warning Threshold nach Geschmack einstellen. Die globalen
   Konto-Einstellungen (Katalog-Auto-Refresh + Intervall) liegen auf dem Konto-Gerät.

> **Cloudflare-Hinweis:** opencode.ai blockt Nicht-Browser-Agenten (HTTP 403 Error 1010).
> Die Integration sendet vollständige Browser-Header mit jeder Anfrage — eingebaut,
> nichts zu konfigurieren.

### Mehrere Workspaces

Jede Workspace-Instanz verwaltet genau **einen** Workspace (ein Name + ein Token).
Für weitere Workspaces die Integration einfach ein weiteres Mal hinzufügen, mit einem
eigenen Namen und Token.

Zusätzlich legt die Integration **automatisch genau eine Konto-Instanz** an
(`Go Gauge Konto`, `unique_id = go_gauge_account`) — beim ersten Workspace-Setup bzw.
beim nächsten Home-Assistant-Start. Sie ist **nicht** über „Integration hinzufügen"
erreichbar und darf **nie manuell** angelegt werden (sie wird über einen Import-Flow
erzeugt). Modell-Katalog, Erreichbarkeit und die globalen Konto-Einstellungen sind
workspace-unabhängig; sie laufen mit den eigenen Konto-Settings weiter, auch wenn der
letzte Workspace entfernt wird.

### Geräte

Pro Workspace-Instanz ein Gerät „Go Gauge {Workspace-Name}" (Nutzung, Forecast, Pace,
Reset-Zeiten, Warnschwelle/Pace-Rot-Grenze und Usage-Refresh-Intervall). Die
workspace-unabhängigen Entities (Model Catalog, Live Models, Cheapest Model, Free Models,
API Reachable) sowie der Katalog-Refresh-Button und die globalen Konto-Einstellungen
(Auto-Update Models, Models-Refresh-Intervall, Konto-Options-Flow) liegen auf dem
eigenen Gerät **„Go Gauge Konto"**. Dieses Gerät gehört der Konto-Entry, nicht einem
Workspace; es existiert genau einmal pro Home-Assistant-Instanz.

### Konto-Entry löschen/deaktivieren

Die Konto-Entry ist eine eigene Integrations-Instanz und wird automatisch verwaltet:

- **Löschen** (Geräte & Dienste → Go Gauge Konto → Löschen): Entities und Konto-Gerät
  verschwinden. Die Entry wird beim **nächsten** Workspace-Setup (Reload, Options-Änderung
  oder HA-Neustart) automatisch neu angelegt. War eine `entity_id` zuvor dedupliziert
  (`…_2`), kann die neu erzeugte Entity eine andere `entity_id` bekommen — die History
  bleibt in der Recorder-Datenbank, die Bindung läuft dann über die neue `entity_id`.
- **Deaktivieren**: Die Konto-Entities bleiben als Registry-Einträge und werden
  `unavailable`; es gibt **kein** automatisches Re-Create und **kein** Re-Enable. Nach dem
  Reaktivieren (Integration neu laden oder HA-Neustart) läuft das Setup wie gewohnt.
- Beides betrifft **nur** das Konto: Workspaces, deren Entities und der Usage-Abruf laufen
  unverändert weiter.

## Dashboards

Fertige Dashboard-Vorlagen für deinen Workspace liegen in
[docs/dashboards/README.md](docs/dashboards/README.md): eine **Standard**-Variante
nur mit Home-Assistant-Bordmitteln (keine Zusatzintegration nötig) und eine
kompakte **Mushroom**-Variante (Frontend-Ressource via HACS). Beide zeigen
Nutzung, Pace, Forecast, Remaining/Time to Reset, Steuerung und Model Catalog.

Ergänzend gibt es eine **Matrix**-Variante
([`workspace-matrix.yaml`](docs/dashboards/workspace-matrix.yaml)), die mehrere
Workspaces nebeneinander im responsiven Grid anordnet und dafür die
HACS-Ressourcen decluttering-card-plus, layout-card, Mushroom und
vertical-stack-in-card benötigt.

## Entitäten (Beispiel: Workspace „Team")

Die Entity-Namen enthalten den beim Einrichten vergebenen Workspace-Namen — die
tatsächlichen `entity_id`-Suffixe hängen davon ab (Home Assistant slugifiziert den
Namen automatisch, z. B. Umlaute → Basisvokal). Konto-Entities heißen dagegen nach dem
Konto-Gerät; bei einem Upgrade behalten bestehende Installationen ihre bisherige
`entity_id`. Verbindlich ist die `unique_id` (Konto-Entities kanonisch `account_*`).

| Entity | Inhalt |
|--------|--------|
| `sensor.go_gauge_team_5h_rolling_usage` | 0 (%) — Attribute: status, note, resets_at_iso |
| `sensor.go_gauge_team_weekly_usage` | 73 (%) |
| `sensor.go_gauge_team_monthly_usage` | 0 (%) — bei fehlendem Abo: `Kein Abo` |
| `sensor.go_gauge_team_monthly_reset` | `2026-09-17T08:38:28+00:00` (Timestamp) |
| `sensor.go_gauge_team_weekly_forecast` | z. B. 92.4 (%) — Hochrechnung aufs Fensterende, kann >100 sein |
| `sensor.go_gauge_team_weekly_pace` | `green` / `yellow` / `red` — Attribute: forecast_percent, green_below, red_above |
| `sensor.go_gauge_team_weekly_remaining` | 27 (%) — Restbudget = 100 − Nutzung |
| `sensor.go_gauge_team_weekly_time_to_reset` | z. B. `62.5` (h) — Restzeit bis zum Fenster-Reset |
| `sensor.go_gauge_team_weekly_burn_rate` | z. B. `4.2` (%/h) — Verbrauchstempo der letzten 2 h |
| `binary_sensor.go_gauge_team_weekly_rate_limited` | ON = für dieses Fenster rate-limited |
| `binary_sensor.go_gauge_team_subscription_active` | ON = aktives Abo, OFF + `note` = kein Abo |
| `sensor.go_gauge_team_api_status` | `ok` / `no_subscription` / `rate_limited` / `auth_error` / `api_error` / `unknown` — **nennt die Ursache**, wenn keine Daten kommen. Attribute: `raw_status`, `note` (Rohursache inkl. URL/Statuscode) |
| `button.go_gauge_refresh` | Workspace-Refresh — lädt **nur die Nutzung** (unique_id `<entry_id>_refresh`) |
| `number.go_gauge_warning_threshold_team` | Warning Threshold in % (Default 80) — zugleich Grün/Gelb-Grenze der Pace-Ampel |
| `number.go_gauge_pace_red_limit_team` | Pace Red Limit der Pace-Ampel in % (Default 100) |
| `number.go_gauge_usage_refresh_min_team` | Usage-Refresh-Intervall in Minuten |
| `switch.go_gauge_team_auto_update_usage` | Auto-Refresh Nutzung ein/aus |
| `sensor.go_gauge_models` | **Konto-Device** — 29; Attribute: `catalog_json`, `ranking_by_cost`, `account_auto_update_models`, `account_fetch_interval_minutes`, `account_models_refresh_minutes`, `account_last_models_outcome` (unique_id `account_model_catalog`) |
| `sensor.go_gauge_live_models` | **Konto-Device** — 29 (unique_id `account_models_live_count`) |
| `sensor.go_gauge_cheapest_model` | **Konto-Device** — z. B. `muse-spark-1.2-contributor` (unique_id `account_cheapest_model`) |
| `sensor.go_gauge_free_models` | **Konto-Device** — `ox-alpha-free` (unique_id `account_free_models`) |
| `binary_sensor.go_gauge_api_reachable` | **Konto-Device** — Evidenz-/TTL-basiert (ON nur bei frischem Erfolg, `unknown` ohne Erfolgsbeleg). Attribute: `reachability_reason`, `evidence`, `models_ttl_s`, `usage_ttl_s`, `checked_at` (unique_id `account_api_reachable`) |
| `button.go_gauge_konto_refresh` | **Konto-Device** — lädt **nur den Modell-Katalog** (unique_id `account_refresh`) |
| `switch.go_gauge_konto_auto_update_models` | **Konto-Device** — globaler Katalog-Auto-Refresh, Default **ON** (unique_id `account_auto_update_models`) |
| `number.go_gauge_konto_models_refresh_min` | **Konto-Device** — Katalog-Intervall **1–1440 min**, Default **60** (unique_id `account_models_refresh_minutes`) |

Die konkreten `entity_id`s der Konto-Entities sind illustrativ (Device-Prefix und
Systemsprache können abweichen); verbindlich ist die `unique_id`. Bestehende
Installationen behalten ihre `entity_id` auch nach dem Upgrade auf 2.0.0.

### Update von 1.x

Einfach updaten — `entity_id`, History, Area und Labels der fünf Konto-Entities bleiben
erhalten (Registry-Adoption; die `unique_id` wird einmalig auf `account_*` normalisiert).
Zwischen dem ersten Start und dem Plattform-Forward sind diese Entities kurz
`unavailable` — erwartet, kein Fehler. Die globalen Konto-Einstellungen bitte einmal
prüfen (Default: Auto-Refresh **ON**, Katalog-Intervall **60 min**); die früheren
Workspace-Modell-Regler (`auto_update_models`, `models_refresh_minutes`) entfallen
ersatzlos. Voraussetzung ist Home Assistant **2026.8.0** oder neuer.

## Beispiel-Automation

```yaml
automation:
  - alias: "Go Budget Warnung"
    trigger:
      - platform: numeric_state
        entity_id: sensor.go_gauge_team_monthly_usage
        above: 80
    action:
      - service: notify.persistent_notification
        data:
          message: "Team-Workspace >80% Monatsbudget!"
```

## Sicherheit

Der Token liegt ausschließlich im Home-Assistant-Config-Entry-Speicher (`.storage`) und
wird nie geloggt. Im Diagnose-Export (Einstellungen → Geräte & Dienste → Go Gauge HA →
Diagnose herunterladen) erscheint nur ein maskierter Fingerprint (erste 8 Zeichen) zur
Zuordnung — der Token selbst wird dort per `async_redact_data` entfernt.

## Lizenz

MIT © Popoboxxo
