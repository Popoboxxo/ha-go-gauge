# E2E-Verifikation v7 entity_id-Slug-Migration (2026-09-12)

**Instanz:** geteilte HA-Dev-Instanz `/home/hermes/ha-test` (HA Core **2026.9.1**, Systemsprache `de`/`DE`)
**Repo:** `/home/hermes/repos/ha-go-gauge`, Branch `fix/entity-name-migration`, Arbeitsbaum **v7** (`manifest.version` unversioniert, Config-Flow `VERSION = 7`)
**Alter Stand auf der Instanz vor dem Test:** `custom_components/go_gauge` v1.2.0 (`config_flow.VERSION = 5`)

## Ergebnis vorab

**STATUS: failed** — die v7-Migration läuft auf der echten Instanz **nicht durch**. Sie bricht im
v6-Schritt mit `AttributeError` ab; der v7-Schritt (entity_id-Rename) wird nie erreicht. Zusätzlich
ist der `number.*`-Zweig der Mapping-Tabelle gegen die **real erzeugten** entity_ids wirkungslos.
Die 324 Unit-Tests sind grün — sie modellieren beide Defekte nicht.

## Ablauf (exakte Kommandos + Beobachtung)

### 0. Auth der Testinstanz reparieren (nicht destruktiv)

Der `refresh_token` war abgelaufen und ein **stale lock** blockierte `bin/ha`
(Endlos-Warteschleife auf `.ha-state/refresh.lock`). Behoben mit dem dafür vorgesehenen Helper
(kein `bin/reset`, keine Daten gelöscht):

```bash
cd /home/hermes/ha-test
rmdir .ha-state/refresh.lock        # stale lock (kein Halter-Prozess)
bin/reauth                          # Passwort-Login -> neue Tokens, "Tokens erneuert."
bin/ha GET /api/                    # -> {"message":"API running."}
```

### 1. Config-Entry unter dem ALTEN Code (v1.2.0, entry VERSION 5) anlegen

`skip_validation` existiert in v1.2.0 → Entry mit Dummy-Token, ohne echten OpenCode-Go-Token:

```bash
cd /home/hermes/ha-test
FLOW=$(bin/ha POST /api/config/config_entries/flow '{"handler": "go_gauge"}')
FLOW_ID=$(echo "$FLOW" | python3 -c "import json,sys;print(json.load(sys.stdin)['flow_id'])")
bin/ha POST /api/config/config_entries/flow/$FLOW_ID \
  '{"workspace_name":"E2E","token":"dummy-token-e2e-not-a-real-secret","skip_validation":true}'
# -> type=create_entry, entry_id=01M2ACPP29X0RV3QJVPRCQJEA3, version=5, state=loaded
```

Es wurden **21 Legacy-Entities** registriert (deutsche `entity_id`-Slugs, `original_name` deutsch).

### 2. v7-Arbeitsbaum deployen

```bash
cd /home/hermes/ha-test
bin/sync /home/hermes/repos/ha-go-gauge
# == HA neu starten == / HA bereit (nach ~8s) / sync_exit=0
```

### 3. Migration fehlgeschlagen

```bash
bin/ha GET /api/config/config_entries/entry
# go_gauge 01M2ACPP29X0RV3QJVPRCQJEA3  state=migration_error
```

Log-Auszug (`docker compose logs --since 5m`):

```
ERROR (MainThread) [homeassistant.config_entries] Error migrating entry Go Gauge E2E for go_gauge
  File "/config/custom_components/go_gauge/__init__.py", line 205, in async_migrate_entry
    entry.version = 6
  File "/usr/src/homeassistant/homeassistant/config_entries.py", line 581, in __setattr__
    raise AttributeError(
      f"{key} cannot be changed directly, use async_update_entry instead")
AttributeError: version cannot be changed directly, use async_update_entry instead
```

## Before/After-Tabelle (Registry)

v6-Name-Migration lief **vor** dem Abbruch (15× `original_name` deutsch→englisch). v7-Slug-Migration
lief **nicht**: alle `entity_id` unverändert deutsch. `unique_id` durchgehend identisch.

| # | entity_id (nach v7-Sync, UNVERÄNDERT) | unique_id (before = after) | original_name nach v6 | `migrate_entity_id()` Ziel (nicht angewandt) |
|---|---|---|---|---|
| 1 | `binary_sensor.go_gauge_ha_go_gauge_api_erreichbar` | `..._api_reachable` | `Go Gauge API Reachable` | `binary_sensor.go_gauge_ha_go_gauge_api_reachable` |
| 2 | `binary_sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_rate_limited` | `..._ws1_5h_limited` | `Go Gauge E2E 5h rolling rate-limited` | `None` (bereits englisch) |
| 3 | `binary_sensor.go_gauge_ha_go_gauge_e2e_abo_aktiv` | `..._ws1_subscription_active` | `Go Gauge E2E Subscription Active` | `..._e2e_subscription_active` |
| 4 | `binary_sensor.go_gauge_ha_go_gauge_e2e_monthly_rate_limited` | `..._ws1_month_limited` | `Go Gauge E2E Monthly rate-limited` | `None` |
| 5 | `binary_sensor.go_gauge_ha_go_gauge_e2e_weekly_rate_limited` | `..._ws1_week_limited` | `Go Gauge E2E Weekly rate-limited` | `None` |
| 6 | `button.go_gauge_ha_go_gauge_aktualisieren` | `..._refresh` | `Go Gauge Refresh` | `button.go_gauge_ha_go_gauge_refresh` |
| 7 | `number.go_gauge_ha_go_gauge_modelle_refresh_minuten_e2e` | `..._models_refresh_minutes` | `Go Gauge Models Refresh (min) · E2E` | **`None` (DEFEKT)** |
| 8 | `number.go_gauge_ha_go_gauge_nutzung_refresh_minuten_e2e` | `..._usage_refresh_minutes` | `Go Gauge Usage Refresh (min) · E2E` | **`None` (DEFEKT)** |
| 9 | `number.go_gauge_ha_go_gauge_warnschwelle_e2e` | `..._warn_percent` | `Go Gauge Warning Threshold · E2E` | **`None` (DEFEKT)** |
| 10 | `sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_nutzung` | `..._ws1_5h_percent` | `Go Gauge E2E 5h rolling Usage` | `..._e2e_5h_rolling_usage` |
| 11 | `sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_reset` | `..._ws1_5h_reset` | `Go Gauge E2E 5h rolling Reset` | `None` |
| 12 | `sensor.go_gauge_ha_go_gauge_e2e_monthly_nutzung` | `..._ws1_month_percent` | `Go Gauge E2E Monthly Usage` | `..._e2e_monthly_usage` |
| 13 | `sensor.go_gauge_ha_go_gauge_e2e_monthly_reset` | `..._ws1_month_reset` | `Go Gauge E2E Monthly Reset` | `None` |
| 14 | `sensor.go_gauge_ha_go_gauge_e2e_weekly_nutzung` | `..._ws1_week_percent` | `Go Gauge E2E Weekly Usage` | `..._e2e_weekly_usage` |
| 15 | `sensor.go_gauge_ha_go_gauge_e2e_weekly_reset` | `..._ws1_week_reset` | `Go Gauge E2E Weekly Reset` | `None` |
| 16 | `sensor.go_gauge_ha_go_gauge_free_modelle` | `..._free_models` | `Go Gauge Free Models` | `sensor.go_gauge_ha_go_gauge_free_models` |
| 17 | `sensor.go_gauge_ha_go_gauge_gunstigstes_modell` | `..._cheapest_model` | `Go Gauge Cheapest Model` | `sensor.go_gauge_ha_go_gauge_cheapest_model` |
| 18 | `sensor.go_gauge_ha_go_gauge_live_modelle` | `..._models_live_count` | `Go Gauge Live Models` | `sensor.go_gauge_ha_go_gauge_live_models` |
| 19 | `sensor.go_gauge_ha_go_gauge_modelle` | `..._model_catalog` | `Go Gauge Models` | `sensor.go_gauge_ha_go_gauge_models` |
| 20 | `switch.go_gauge_ha_go_gauge_e2e_modelle_auto_update` | `..._auto_update_models` | `Go Gauge E2E Auto Update Models` | `..._e2e_auto_update_models` |
| 21 | `switch.go_gauge_ha_go_gauge_e2e_nutzung_auto_update` | `..._auto_update_usage` | `Go Gauge E2E Auto Update Usage` | `..._e2e_auto_update_usage` |

**unique_id identisch (before == after): 21/21. entity_id unverändert (Rename nicht ausgeführt): 0/21.**

Hinweis: Alle realen entity_ids tragen das **Device-Name-Präfix `go_gauge_ha_`** (Device "Go Gauge HA"),
weil HA 2026.9 bei `has_entity_name=False` den Device-Namen in die entity_id/Slug einrechnet. Genau
darauf ist der `number`-Zweig der Migration nicht vorbereitet.

## Verdict je Kriterium

| Kriterium | Verdict | Begründung |
|---|---|---|
| Anzeigename englisch? | **TEILWEISE / unklar** | `original_name` ist nach der v6-Migration englisch (15/21 geändert), und die (wegen `migration_error` unavailable) States zeigen englische `friendly_name`. Bei **geladener** v7-Entity mit `translation_key` wird der `friendly_name` dagegen aus `translations/de.json` aufgelöst → dann **deutsch** (Systemsprache `de`). Die Anzeige ist per Design sprachabhängig, nicht fix englisch. |
| entity_id-Slug englisch? | **FAIL** | v7-Rename lief nie (Crash). Alle 21 Slugs bleiben deutsch. Zusätzlich würde selbst ohne Crash kein `number.*` migrieren (Mapping-Zweig defekt). |
| unique_id unverändert? | **PASS** | 21/21 unique_ids byte-identisch before/after. |
| History/Statistics gebunden? | **NICHT VERIFIZIERT** | Da kein Rename stattfand, blieb die Bindung trivial. HA-Recorder rebindet bei einem echten Rename über `components/recorder/entity_registry.py` (`async_update_statistics_metadata` + `async_update_states_metadata`), d. h. die Mechanik existiert — konnte hier aber nicht end-to-end beobachtet werden. |

## Root Causes (belegt)

### RC-1 — Migration bricht ab: direkte `entry.version`-Zuweisung

`custom_components/go_gauge/__init__.py` setzt `entry.version = 3/4/5/6/7` direkt (Zeilen 177,
188, 199, 205, **215**). HA 2026.9 verbietet das (`ConfigEntry.__setattr__` → `AttributeError`).
Der v5-Entry läuft `if entry.version < 6:` → `_async_migrate_entity_names()` (erfolgreich) →
`entry.version = 6` → Absturz. HA markiert den Entry als `migration_error`, Setup entfällt
(alle Entities `unavailable`). Retry beim nächsten Start schlägt identisch fehl.
Korrektur: Version über `hass.config_entries.async_update_entry(entry, version=...)` bzw. einen
lokalen Zähler + einmaliges `async_update_entry(..., version=7)` setzen.

### RC-2 — `number.*`-Rename greift nie (Device-Präfix-Annahme falsch)

`const.py` `_apply_anchor()` behandelt `number` positionell:
`go_gauge_<fragment>_<workspace>`. Real lautet die entity_id aber
`number.go_gauge_ha_go_gauge_warnschwelle_e2e` (Device-Präfix `go_gauge_ha_` davor) → kein Zweig
matcht → `migrate_entity_id()` liefert `None` (empirisch für alle 3 number-Slugs bestätigt).
Der generische Trailing-Token-Zweig für alle anderen Plattformen funktioniert trotz Präfix.

### RC-3 — Test-Fidelity-Lücke

Alle 324 Unit-Tests grün. Ursache: Die Migrationstests nutzen `MagicMock()` als Entry, der
`entry.version = 6` erlaubt (real verboten), und idealisierte Slugs ohne `go_gauge_ha_`-Präfix
(`tests/test_entity_id_migration.py` `FULL_ID_CASES`, `TestMappingIsNotFreshInstallCanonical`).
RC-1 und RC-2 können von der Suite daher nicht erkannt werden.

## Nebenbeobachtung (nicht migrationsrelevant)

Beim API-Ausfall (Dummy-Token) wirft der v7-`UsagePercentSensor` `ValueError`
(nicht-numerischer String `'Fehler'` bei `state_class=measurement`/`unit='%'`); die
Workspace-Nutzungssensoren werden nicht hinzugefügt. Das Verhalten existierte bereits in v1.2.0
(im Before-Lauf fehlten die `*_nutzung`-States ebenfalls), ist also kein v7-Regress.

## Zustand der Testinstanz nach dem Test (bewusst NICHT geleert)

- `go_gauge` Entry `01M2ACPP29X0RV3QJVPRCQJEA3` bleibt als **`migration_error`** liegen (mit 21
  Legacy-Registry-Einträgen) — genau der Ausgangszustand, den ein gefixter Migrator braucht.
- Instanz läuft jetzt v7-`custom_components/go_gauge` (durch `bin/sync`).
- `health_o_mat` und andere Domains unangetastet; Systemsprache unverändert (`de`).
- Tokens (`/home/hermes/ha-test/.ha-state/`) wurden nie gelesen/ausgegeben/kopiert.

## Open Points (empfohlene Folge-Tasks)

1. **RC-1 fixen** (`entry.version` → `async_update_entry`), dann v7-Sync wiederholen; erwartetes
   Ergebnis: Entry wird v7, `entity_id`-Rename für 18/21 Entities.
2. **RC-2 fixen**: `number`-Zweig in `_apply_anchor()` auf das reale `go_gauge_<device>_...`-Präfix
   verallgemeinern (z. B. Trailing-Match auch für `_<fragment>_<workspace>` bzw. Device-Präfix
   tolerieren), sonst bleiben 3 number-Slugs deutsch.
3. **RC-3 schließen**: Entry-Double verwenden, das direkte `version`-Zuweisung wie HA verbietet;
   Testfälle um die real beobachteten `go_gauge_ha_...`-Slugs ergänzen (Rohdaten unten).
4. Kriterium **Anzeigename englisch** fachlich klären: `translation_key` + `de.json` liefert bei
   Systemsprache `de` bewusst deutsche `friendly_name` — ist das gewünscht oder soll der Anzeigename
   fix englisch sein?
5. Kriterium **History/Statistics-Rebinding** nach dem Fix erneut prüfen (HA rebindet über den
   Recorder-Listener; mit `statistics_meta`-Vorher/Nachher belegen).

## Rohdaten / Artefakte (nicht im Repo)

- `/tmp/gg-e2e/before_registry.json`, `/tmp/gg-e2e/after_registry.json` — Registry-Snapshots
- `/tmp/gg-e2e/comparison.txt` — Diff-Auswertung
- `/tmp/gg-e2e/before_db.txt`, `/tmp/gg-e2e/after_db.txt` — `states_meta`/`statistics_meta`
- HA-Log: `docker compose logs --since 5m` (Traceback oben)
