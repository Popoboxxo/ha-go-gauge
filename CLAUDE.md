# ha-go-gauge

> Projektbeschreibung für Claude-Agenten. Diese Datei ist die **einzige Quelle**
> für projektspezifischen Kontext — Agenten lesen sie, statt eigenen Kontext zu haben.
>
> Generiert von agent-meta v0.101.0-beta.3 — `2026-08-30`
>
> **Längenempfehlung:** 200–500 Zeilen optimal. Über 500 Zeilen → Detailwissen in
> `docs/ARCHITECTURE.md`, `docs/API.md` o.ä. auslagern und manuell verlinken.
> Agent-spezifisches Wissen → `.claude/3-project/<rolle>-ext.md` (Extension).
>
> **CLAUDE.md Hierarchie (Claude Code lädt in dieser Reihenfolge):**
> 1. `~/.claude/CLAUDE.md` — global, alle Projekte (~50 Zeilen max, persönliche Präferenzen)
> 2. `<projekt>/CLAUDE.md` — diese Datei, projektspezifisch (von agent-meta verwaltet)
> 3. `<ordner>/CLAUDE.md` — optional in Unterordnern (z.B. `src/backend/CLAUDE.md`)

---

## Eigene Notizen

Hier kannst du eigene, projektspezifische Notizen eintragen. Dieser Bereich wird von `agent-meta` nicht überschrieben!

---

## Projekt

**Name:** ha-go-gauge
**Präfix:** gg
**Plattform:** Home Assistant Custom Integration (HACS)
**Beschreibung:** HACS Custom Integration für Home Assistant, die direkt gegen die OpenCode-Go-API (opencode.ai) spricht und Nutzung, Limits und Kosten-Ratio des Go-Budgets als Entities bereitstellt.

## Tech-Stack

- **Runtime:** Home Assistant Core (Python)
- **Sprache:** Python 3
- **Key-Dependencies:** - homeassistant: custom_components integration (domain `go_gauge`)
- pytest: Unit-Tests (eigene HA-Stubs, siehe pytest.ini)


## Architektur

```
custom_components/go_gauge/
  __init__.py       # Setup / Entry-Point
  config_flow.py    # UI-Konfiguration
  coordinator.py    # DataUpdateCoordinator (Polling)
  entity.py         # Basis-Entity
  sensor.py         # Nutzungs-/Modell-Sensoren
  binary_sensor.py  # Rate-Limit-/Abo-Status
  number.py         # Update-Intervalle
  switch.py         # Auto-Update-Zyklen
  button.py         # Manueller Refresh
  diagnostics.py    # Diagnose-Export
  const.py          # Konstanten
  manifest.json
  strings.json
tests/

```

**Entry-Point:**
```
custom_components/go_gauge/__init__.py — Integration-Setup
```

**Besondere Patterns:**
- DataUpdateCoordinator pro Workspace-Instanz, getrennte Auto-Update-Zyklen für Usage/Modelle
- Ein Sensor als "Modell-Katalog" (dynamische Attribute statt Einzel-Entities pro Modell)
- Nur die erste Instanz ("Catalog Owner") legt Katalog-Entities an — keine Duplikate


## Code-Konventionen

- Python 3, HA-Integrationskonventionen (async, DataUpdateCoordinator)
- snake_case für Module/Funktionen, PascalCase für Klassen
- Positionale Argumente vermeiden wo Verwechslungsgefahr besteht (siehe v0.6.1-Fix)


## Build & Development

```bash
# Build
(kein Build — reine Python-Integration)

# Tests
pytest

# Dev-Stack starten
(kein Dev-Stack — Test gegen echte/gemockte HA-Instanz)

# Nach Änderungen neu laden

```

## Anforderungs-Kategorien

Kategorien für `docs/REQUIREMENTS.md`:

- Kernfunktionalität (Sensoren, Coordinator)
- Config Flow / Setup
- Nichtfunktionale Anforderungen (Rate-Limits, Diagnostics)



## Agenten-Konfiguration

<!-- agent-meta:managed-begin -->
<!-- Dieser Block wird von sync.py bei jedem sync automatisch aktualisiert. -->
<!-- Manuelle Änderungen hier werden überschrieben. -->

> **AI ROUTING:** Claude -> CLAUDE.md | Gemini, Opencode -> AGENTS.md

Generiert von agent-meta v0.101.0-beta.3 — `2026-08-30`
DoD-Preset: **standard** | REQ-Traceability: false | Tests: true | Codebase-Overview: false | Security-Audit: false
> **Einstiegspunkt:** Starte mit dem `orchestrator`-Agenten für alle Entwicklungsaufgaben — Ausnahmen siehe Abschnitt »Orchestrator — Universal Router«.
<!-- agent-meta:managed-end -->
