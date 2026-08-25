# Go Gauge HA

**The gauge for your OpenCode Go budget — usage, limits & cost-ratio right on your dashboard.**

![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5) ![Version](https://img.shields.io/badge/version-0.6.1-blue)

Eigenständige [HACS](https://hacs.xyz)-Custom-Integration, die **direkt** gegen die
OpenCode-Go-API (`opencode.ai`) spricht — kein Monitor, kein Dashboard, keine
Zusatzkomponente. Nur dein Workspace-API-Token.

## Features

- 🏷️ **Eine Instanz pro Workspace** — sprechender Name + ein Token; mehrere Workspaces
  bedeuten mehrere Integrations-Instanzen (siehe [Mehrere Workspaces](#mehrere-workspaces))
- 📈 **Prozentuale Nutzung** je Fenster (5h rolling / Weekly / Monthly)
- ⏰ **Konkrete Reset-Zeiten** als Timestamp-Entitäten — automationstauglich
- 🚦 **Rate-Limit-Warnung** als Binary Sensor je Fenster + **Abo-Status** als eigener
  Binary Sensor (403 „EntitlementError" = kein aktives Abo, klar von einem
  Rate-Limit/Netzfehler unterschieden)
- 📦 **Modell-Katalog als EINEN Sensor** (`sensor.go_gauge_modelle`): komplettes,
  dynamisches Verzeichnis in den Attributen (`catalog_json`, `ranking_by_cost`, …) —
  neue Modelle erscheinen automatisch, ohne neue Entitäten. Bei mehreren Instanzen
  legt nur die erste („Catalog Owner") diese Entities an — keine Duplikate.
- 🏆 Kompakt-Sensoren: Live-Anzahl · Günstigstes Modell · Free-Modelle
- 🔘 **Refresh-Button** „Go Gauge Aktualisieren" — holt sofort beides (Nutzung + Modelle),
  unabhängig von den Auto-Zyklen
- ⚙️ **Getrennte Auto-Update-Zyklen**, live umschaltbar über eigene Switch-/Number-Entities
  (kein Neustart/Reload nötig) **oder** über die Optionen:
  | Zyklus | Ein/Aus | Default |
  |--------|---------|---------|
  | Nutzung (Usage) | ✅ an | alle **10 Min** |
  | Modell-Katalog | ✅ an | alle **60 Min** |
  Beide abschaltbar; dann aktualisiert nur der Refresh-Button.
- 🔔 **Warnschwelle** (Default 80 %) als Number-Entity — live anpassbar, z. B. für
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
   Refresh-Zyklen und Warnschwelle nach Geschmack einstellen

> **Cloudflare-Hinweis:** opencode.ai blockt Nicht-Browser-Agenten (HTTP 403 Error 1010).
> Die Integration sendet vollständige Browser-Header mit jeder Anfrage — eingebaut,
> nichts zu konfigurieren.

### Mehrere Workspaces

Jede Integrations-Instanz verwaltet genau **einen** Workspace (ein Name + ein Token).
Für weitere Workspaces die Integration einfach ein weiteres Mal hinzufügen, mit einem
eigenen Namen und Token. Der Modell-Katalog ist workspace-unabhängig und wird nur von
der zuerst geladenen Instanz als Entities angelegt; alle weiteren Instanzen teilen ihn
sich, ohne eigene Katalog-Entities zu duplizieren.

## Entitäten (Beispiel: Workspace „Team")

Die Entity-Namen enthalten den beim Einrichten vergebenen Workspace-Namen — die
tatsächlichen `entity_id`-Suffixe hängen davon ab (Home Assistant slugifiziert den
Namen automatisch, z. B. Umlaute → Basisvokal).

| Entity | Inhalt |
|--------|--------|
| `sensor.go_gauge_team_5h_rolling_nutzung` | 0 (%) — Attribute: status, note, resets_at_iso |
| `sensor.go_gauge_team_weekly_nutzung` | 73 (%) |
| `sensor.go_gauge_team_monthly_nutzung` | 0 (%) — bei fehlendem Abo: `Kein Abo` |
| `sensor.go_gauge_team_monthly_reset` | `2026-09-17T08:38:28+00:00` (Timestamp) |
| `binary_sensor.go_gauge_team_weekly_rate_limited` | ON = für dieses Fenster rate-limited |
| `binary_sensor.go_gauge_team_abo_aktiv` | ON = aktives Abo, OFF + `note` = kein Abo |
| `binary_sensor.go_gauge_api_erreichbar` | API-Status (nur Catalog-Owner-Instanz) |
| `sensor.go_gauge_modelle` | 29 — Attribute: `catalog_json`, `ranking_by_cost`, … (nur Catalog Owner) |
| `sensor.go_gauge_live_modelle` | 29 (nur Catalog Owner) |
| `sensor.go_gauge_gunstigstes_modell` | z. B. `muse-spark-1.2-contributor` (nur Catalog Owner) |
| `sensor.go_gauge_free_modelle` | `ox-alpha-free` (nur Catalog Owner) |
| `button.go_gauge_aktualisieren` | sofortiger Gesamt-Refresh |
| `number.go_gauge_warnschwelle_team` | Warnschwelle in % (Default 80) |
| `number.go_gauge_nutzung_refresh_minuten_team` | Nutzungs-Refresh-Intervall in Minuten |
| `number.go_gauge_modelle_refresh_minuten_team` | Modell-Refresh-Intervall in Minuten (nur Catalog Owner) |
| `switch.go_gauge_team_nutzung_auto_update` | Auto-Refresh Nutzung ein/aus |
| `switch.go_gauge_team_modelle_auto_update` | Auto-Refresh Modell-Katalog ein/aus (nur Catalog Owner) |

## Beispiel-Automation

```yaml
automation:
  - alias: "Go Budget Warnung"
    trigger:
      - platform: numeric_state
        entity_id: sensor.go_gauge_team_monthly_nutzung
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
