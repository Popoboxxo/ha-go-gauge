# Go Gauge HA

**The gauge for your OpenCode Go budget — usage, limits & cost-ratio right on your dashboard.**

![Go Gauge HA](https://img.shields.io/badge/HACS-Custom-41BDF5)
![Version](https://img.shields.io/badge/version-0.1.0-blue)

Go Gauge HA ist eine [HACS](https://hacs.xyz)-Custom-Integration für Home Assistant,
die den selbst gehosteten **OpenCode-Go-Monitor** (`collector.py`, Teil dieses Setups)
ausliest und alle Daten als native HA-Entitäten bereitstellt:

## Features

- 🔢 **Dynamische Workspaces** — `ws1..wsN`: neue Workspace-Slots erscheinen automatisch
  beim nächsten Poll, ohne Konfigurationsänderung
- 📈 **Prozentuale Nutzung** je Workspace × Fenster (5h rolling / Woche / Monat)
  inkl. USD-Betrag und Limit
- ⏰ **Konkrete Reset-Zeiten** als echte Timestamp-Entitäten (`device_class: timestamp`)
  — direkt in Automatisierungen nutzbar
- 🚦 **Rate-Limit-Warnungen** als Binary Sensoren pro Workspace × Fenster
- 🤖 **Modell-Katalog workspace-unabhängig**: Live-Modellanzahl, günstigstes Modell,
  Free-Modelle und ein Kosten-Nutzen-Ratio-Sensor ($/1M gemischt: 80% Input + 20% Output)
  je Modell
- 🔘 Refresh-Button für sofortiges Nachladen
- ⚙️ UI-Setup (Config Flow) + Optionen (Warnschwelle, Poll-Intervall) ohne Neustart

## Voraussetzungen

Ein laufender OpenCode-Go-Monitor im LAN:

```bash
python3 collector.py --port 8765   # liefert /state (JSON) + / (HTML-Dashboard)
```

Der Monitor wiederum braucht die nummerierten Env-Slots:

```
OPENCODE_WS_<N>_NAME=Default        # Anzeigename
OPENCODE_WS_<N>_TOKEN=sk-...        # Go-API-Key genau DIESES Workspaces
```

## Installation (HACS)

1. HACS → Integrations → ⋮ → *Custom repositories*
2. Repository: `https://github.com/Popoboxxo/ha-go-gauge` · Kategorie: `Integration`
3. Installieren, dann HA neu starten

Alternativ manuell: `custom_components/go_gauge/` nach `<config>/custom_components/` kopieren.

## Einrichtung

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen* → **Go Gauge HA**
2. Host + Port des Monitors angeben (Default: `172.20.5.120:8765`)
3. Fertig — die Entitäten werden automatisch erzeugt

## Entitäten (Beispiel bei 4 Workspaces)

| Entity | Inhalt |
|--------|--------|
| `sensor.go_gauge_ws3_honcho_month_nutzung` | 36 (%) mit Attributen usd, limit_usd, resets_at_iso |
| `sensor.go_gauge_ws3_honcho_month_reset` | `2026-09-17T08:38:28+00:00` (Timestamp) |
| `sensor.go_gauge_ws2_app_monat_usd` | 60.0 (USD verbraucht) |
| `binary_sensor.go_gauge_ws2_app_month_rate_limited` | ON = rate-limited |
| `binary_sensor.go_gauge_monitor_erreichbar` | Monitor-Verbindungsstatus |
| `sensor.go_gauge_live_modelle` | 29 |
| `sensor.go_gauge_gunstigstes_modell` | `muse-spark-1.2-contributor` (+ Ratio-Attribute) |
| `sensor.go_gauge_free_modelle` | `ox-alpha-free` |
| `sensor.go_gauge_modell_deepseek_v4_pro` | $/1M gemischt je Modell |

## Beispiel-Automation

```yaml
automation:
  - alias: "Go Budget Warnung"
    trigger:
      - platform: numeric_state
        entity_id: sensor.go_gauge_ws3_honcho_month_nutzung
        above: 80
    action:
      - service: notify.persistent_notification
        data:
          message: "Workspace Honcho >80% Monatsbudget!"
```

## Architektur

```
OpenCode Go API ──► collector.py (Monitor, LAN) ──► Go Gauge HA (Coordinator)
  /zen/go/v1/models       :8765/state                    DataUpdateCoordinator
  /zen/go/v1/usage                                       ├─ Sensor (%, USD, Ratio)
                                                         ├─ Timestamp (Reset)
                                                         ├─ BinarySensor (limited)
                                                         └─ Button (refresh)
```

## Lizenz

MIT © Popoboxxo
