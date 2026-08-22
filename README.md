# Go Gauge HA

**The gauge for your OpenCode Go budget — usage, limits & cost-ratio right on your dashboard.**

![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5) ![Version](https://img.shields.io/badge/version-0.3.0-blue)

Eigenständige [HACS](https://hacs.xyz)-Custom-Integration, die **direkt** gegen die
OpenCode-Go-API (`opencode.ai`) spricht — kein Monitor, kein Dashboard, keine
Zusatzkomponente. Nur deine Workspace-API-Tokens.

## Features

- 🔢 **Dynamische Workspaces** — ein Token pro Workspace (`ws1..wsN`), beliebig viele
- 📈 **Prozentuale Nutzung** je Workspace × Fenster (5h rolling / Woche / Monat)
- ⏰ **Konkrete Reset-Zeiten** als Timestamp-Entitäten — automationstauglich
- 🚦 **Rate-Limit-Warnung** als Binary Sensor je Workspace × Fenster
- 📦 **Modell-Katalog als EINEN Sensor** (`sensor.go_gauge_modelle`): komplettes,
  dynamisches Verzeichnis in den Attributen (`catalog_json`, `ranking_by_cost`, …) —
  neue Modelle erscheinen automatisch, ohne neue Entitäten
- 🏆 Kompakt-Sensoren: Live-Anzahl · Günstigstes Modell · Free-Modelle
- 🔘 **Refresh-Button** „Go Gauge Aktualisieren" — holt sofort beides (Nutzung + Modelle),
  unabhängig von den Auto-Zyklen
- ⚙️ **Getrennte Auto-Update-Zyklen** (Optionen):
  | Zyklus | Ein/Aus | Default |
  |--------|---------|---------|
  | Nutzung (Usage) | ✅ an | alle **10 Min** |
  | Modell-Katalog | ✅ an | alle **60 Min** |
  Beide abschaltbar; dann aktualisiert nur der Refresh-Button.
- 🛟 **Offline-freundliches Setup**: Tokens werden beim Speichern live geprüft, aber bei
  Netzproblemen kannst du die Prüfung überspringen und trotzdem speichern.

## Installation (HACS)

1. HACS → Integrations → ⋮ → *Custom repositories*
2. `https://github.com/Popoboxxo/ha-go-gauge` · Kategorie: `Integration`
3. Installieren → Home Assistant neu starten

## Einrichtung

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen* → **Go Gauge HA**
2. OpenCode-Go-API-Tokens einfügen — einer pro Zeile, einer pro Workspace
   (Keys: OpenCode-UI → dein Workspace → Go → API Keys)
3. Optionen (Zahnrad auf dem Gerät): Refresh-Zyklen nach Geschmack einstellen

> **Cloudflare-Hinweis:** opencode.ai blockt Nicht-Browser-Agenten (HTTP 403 Error 1010).
> Die Integration sendet vollständige Browser-Header mit jeder Anfrage — eingebaut,
> nichts zu konfigurieren.

## Entitäten (Beispiel: 4 Tokens)

| Entity | Inhalt |
|--------|--------|
| `sensor.go_gauge_ws_1_month_nutzung` | 0 (%) — Attribute: status, resets_at_iso |
| `sensor.go_gauge_ws_3_month_reset` | `2026-09-17T08:38:28+00:00` (Timestamp) |
| `sensor.go_gauge_ws_2_week_nutzung` | 73 (%) |
| `binary_sensor.go_gauge_ws_2_month_rate_limited` | ON = rate-limited |
| `binary_sensor.go_gauge_api_erreichbar` | API-Status |
| `sensor.go_gauge_modelle` | 29 — Attribute: `catalog_json`, `ranking_by_cost`, … |
| `sensor.go_gauge_live_modelle` | 29 |
| `sensor.go_gauge_gunstigstes_modell` | z.B. `muse-spark-1.2-contributor` |
| `sensor.go_gauge_free_modelle` | `ox-alpha-free` |
| `button.go_gauge_aktualisieren` | sofortiger Gesamt-Refresh |

## Beispiel-Automation

```yaml
automation:
  - alias: "Go Budget Warnung"
    trigger:
      - platform: numeric_state
        entity_id: sensor.go_gauge_ws_3_month_nutzung
        above: 80
    action:
      - service: notify.persistent_notification
        data:
          message: "Workspace 3 >80% Monatsbudget!"
```

## Sicherheit

Tokens liegen ausschließlich verschlüsselt im HA-Speicher (`.storage`), werden nie geloggt
und nie in Diagnose-Dumps ausgegeben.

## Lizenz

MIT © Popoboxxo
