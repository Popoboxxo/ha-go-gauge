# Go Gauge HA

**The gauge for your OpenCode Go budget — usage, limits & cost-ratio right on your dashboard.**

![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5) ![Version](https://img.shields.io/badge/version-0.2.0-blue)

Go Gauge HA ist eine eigenständige [HACS](https://hacs.xyz)-Custom-Integration, die
**direkt** gegen die OpenCode-Go-API (`opencode.ai`) spricht — **kein Monitor,
kein Dashboard, keine Zusatzkomponente nötig.** Nur deine Workspace-API-Tokens.

## Features

- 🔢 **Dynamische Workspaces** — ein Token pro Workspace (`ws1..wsN`), beliebig viele
- 📈 **Prozentuale Nutzung** je Workspace × Fenster (5h rolling / Woche / Monat)
- ⏰ **Konkrete Reset-Zeiten** als echte Timestamp-Entitäten — automationstauglich
- 🚦 **Rate-Limit-Warnung** als Binary Sensor je Workspace × Fenster
- 🤖 **Modell-Katalog workspace-unabhängig**: Live-Modellanzahl, günstigstes bezahltes
  Modell, Free-Modelle und Kosten-Nutzen-Ratio ($/1M gemischt: 80% Input + 20% Output)
  als eigener Sensor je Modell
- 🔘 Refresh-Button · ⚙️ UI-Setup + Optionen (Warnschwelle, Poll-Intervall)

## Installation (HACS)

1. HACS → Integrations → ⋮ → *Custom repositories*
2. `https://github.com/Popoboxxo/ha-go-gauge` · Kategorie: `Integration`
3. Installieren → Home Assistant neu starten

## Einrichtung

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen* → **Go Gauge HA**
2. Deine OpenCode-Go-API-Tokens einfügen — **einer pro Zeile**, einer pro Workspace
   (Keys bekommst du in der OpenCode-UI unter deinem Workspace → Go → API Keys)
3. Fertig

Die Integration validiert den ersten Token live gegen die API und legt dann pro Token
automatisch alle Sensoren an. Neue Tokens = Integration neu einrichten oder Tokens im
Config-Entry ergänzen.

> **Technischer Hinweis:** opencode.ai sitzt hinter Cloudflare. Die Integration sendet
> daher vollständige Browser-Header mit jeder Anfrage — sonst antwortet die API mit
> HTTP 403 (Error 1010). Das ist eingebaut, du musst nichts tun.

## Entitäten (Beispiel: 4 Tokens)

| Entity | Inhalt |
|--------|--------|
| `sensor.go_gauge_ws_3_month_nutzung` | 36 (%) — Attribute: status, resets_at_iso |
| `sensor.go_gauge_ws_3_month_reset` | `2026-09-17T08:38:28+00:00` (Timestamp) |
| `binary_sensor.go_gauge_ws_2_month_rate_limited` | ON = rate-limited |
| `binary_sensor.go_gauge_api_erreichbar` | API-Verbindungsstatus |
| `sensor.go_gauge_live_modelle` | 29 |
| `sensor.go_gauge_gunstigstes_modell` | z.B. `muse-spark-1.2-contributor` (+ Ratio-Attribute) |
| `sensor.go_gauge_free_modelle` | `ox-alpha-free` |
| `sensor.go_gauge_modell_deepseek_v4_flash` | $/1M gemischt je Modell |

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

Tokens liegen ausschließlich verschlüsselt im HA-Speicher (.storage), werden nie geloggt
und nie in Diagnose-Dumps ausgegeben.

## Lizenz

MIT © Popoboxxo
