# E2E-Verifikation v7 entity_id-Slug-Migration (2026-09-12)

> **Aktuellster Stand:** Der Erstlauf weiter unten war `failed` (RC-1/RC-2). Nach den Fixes
> (RC-1 `async_update_entry(..., version=...)`, RC-2 `number`-Anchoring mit Device-Präfix,
> RC-3 gehärtete Unit-Tests) wurde der E2E **erfolgreich wiederholt** — siehe
> **[Re-Run nach RC-1/RC-2/RC-3](#re-run-nach-rc-1rc-2rc-3-2026-09-12)** am Dateiende.
> **Neuer Befund dort: ND-1** (English-Slug-Ziel kippt für nach der Migration neu registrierte
> Entities auf `de`-Systemen).

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

---

# Re-Run nach RC-1/RC-2/RC-3 (2026-09-12)

**Ziel:** E2E-Verifikation der v7 entity_id-Slug-Migration nach den Fixes. Fixture unverändert:
Config-Entry `01M2ACPP29X0RV3QJVPRCQJEA3` in `migration_error`, Version **5**, 21 go_gauge-Entities
unter deutschen Legacy-Slugs, `unique_id`s unangetastet. Instanz HA 2026.9.1, Systemsprache `de`.

**Fixes gegenüber dem Erstlauf:**

| Fix | Ort |
|---|---|
| RC-1: Version via `hass.config_entries.async_update_entry(entry, …, version=new_version)` statt direktem `entry.version=` | `custom_components/go_gauge/__init__.py:233-239` |
| RC-2: `number`-Anchoring toleriert Device-Präfix (`go_gauge_<legacy>` via `str.find`, Workspace/Präfix byte-identisch) | `custom_components/go_gauge/const.py:223-250` (`_apply_anchor`) |
| RC-3: Unit-Tests gehärtet (`tests/test_entity_id_migration.py`, Entry-Double verbietet `entry.version=`) | `tests/` |

## Kommandos

```bash
# 1) Vorher-Snapshot (Registry + Entry + Recorder-Metadaten, WAL-bewusst beim Nachher-Lauf)
python3 /tmp/gg-e2e/snapshot_v2.py rerun_before

# 2) Deploy + Neustart (nur go_gauge; KEIN bin/reset, Sprache unverändert de)
cd /home/hermes/ha-test && bin/sync /home/hermes/repos/ha-go-gauge
# -> "HA bereit (nach ~8s)", "keine Setup-Fehler", sync_exit=0

# 3) Nachher-Snapshot
python3 /tmp/gg-e2e/snapshot_v2.py rerun_after_final
cd /home/hermes/ha-test && bin/ha GET /api/config/config_entries/entry   # state=loaded
cd /home/hermes/ha-test && bin/ha GET /api/states                        # friendly_names (de)
```

> **Snapshot-Hinweis:** Der erste Nachher-Snapshot (kurz nach `sync`) las `.storage/core.entity_registry`
> bevor HA den Rename-Schreibvorgang geflusht hatte und zeigte fälschlich unveränderte Slugs. Der
> verbindliche Nachher-Snapshot (`rerun_after_final`) sowie alle Recorder-Werte wurden nach dem
> Registry-Flush (mtime `10:57:46`) bzw. mit WAL-Kopie erhoben.

## Ergebnis Entry

| | before | after |
|---|---|---|
| entry_id | `01M2ACPP29X0RV3QJVPRCQJEA3` | `01M2ACPP29X0RV3QJVPRCQJEA3` |
| version | **5** | **7** |
| Runtime-State | `migration_error` | **`loaded`** |
| unique_id | `go_gauge_1e7dcc36f43759fa` | `go_gauge_1e7dcc36f43759fa` (unverändert) |

## Before/After-Tabelle (die 21 Legacy-Entities)

| # | entity_id (before) | entity_id (after) | unique_id (before == after) |
|---|---|---|---|
| 1 | `binary_sensor.go_gauge_ha_go_gauge_api_erreichbar` | `binary_sensor.go_gauge_ha_go_gauge_api_reachable` | `01M2ACPP29X0RV3QJVPRCQJEA3_api_reachable` |
| 2 | `switch.go_gauge_ha_go_gauge_e2e_modelle_auto_update` | `switch.go_gauge_ha_go_gauge_e2e_auto_update_models` | `01M2ACPP29X0RV3QJVPRCQJEA3_auto_update_models` |
| 3 | `switch.go_gauge_ha_go_gauge_e2e_nutzung_auto_update` | `switch.go_gauge_ha_go_gauge_e2e_auto_update_usage` | `01M2ACPP29X0RV3QJVPRCQJEA3_auto_update_usage` |
| 4 | `sensor.go_gauge_ha_go_gauge_gunstigstes_modell` | `sensor.go_gauge_ha_go_gauge_cheapest_model` | `01M2ACPP29X0RV3QJVPRCQJEA3_cheapest_model` |
| 5 | `sensor.go_gauge_ha_go_gauge_free_modelle` | `sensor.go_gauge_ha_go_gauge_free_models` | `01M2ACPP29X0RV3QJVPRCQJEA3_free_models` |
| 6 | `sensor.go_gauge_ha_go_gauge_modelle` | `sensor.go_gauge_ha_go_gauge_models` | `01M2ACPP29X0RV3QJVPRCQJEA3_model_catalog` |
| 7 | `sensor.go_gauge_ha_go_gauge_live_modelle` | `sensor.go_gauge_ha_go_gauge_live_models` | `01M2ACPP29X0RV3QJVPRCQJEA3_models_live_count` |
| 8 | `number.go_gauge_ha_go_gauge_modelle_refresh_minuten_e2e` | `number.go_gauge_ha_go_gauge_models_refresh_min_e2e` | `01M2ACPP29X0RV3QJVPRCQJEA3_models_refresh_minutes` |
| 9 | `button.go_gauge_ha_go_gauge_aktualisieren` | `button.go_gauge_ha_go_gauge_refresh` | `01M2ACPP29X0RV3QJVPRCQJEA3_refresh` |
| 10 | `number.go_gauge_ha_go_gauge_nutzung_refresh_minuten_e2e` | `number.go_gauge_ha_go_gauge_usage_refresh_min_e2e` | `01M2ACPP29X0RV3QJVPRCQJEA3_usage_refresh_minutes` |
| 11 | `number.go_gauge_ha_go_gauge_warnschwelle_e2e` | `number.go_gauge_ha_go_gauge_warning_threshold_e2e` | `01M2ACPP29X0RV3QJVPRCQJEA3_warn_percent` |
| 12 | `binary_sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_rate_limited` | *identisch* | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_5h_limited` |
| 13 | `sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_nutzung` | `sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_usage` | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_5h_percent` |
| 14 | `sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_reset` | *identisch* | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_5h_reset` |
| 15 | `binary_sensor.go_gauge_ha_go_gauge_e2e_monthly_rate_limited` | *identisch* | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_month_limited` |
| 16 | `sensor.go_gauge_ha_go_gauge_e2e_monthly_nutzung` | `sensor.go_gauge_ha_go_gauge_e2e_monthly_usage` | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_month_percent` |
| 17 | `sensor.go_gauge_ha_go_gauge_e2e_monthly_reset` | *identisch* | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_month_reset` |
| 18 | `binary_sensor.go_gauge_ha_go_gauge_e2e_abo_aktiv` | `binary_sensor.go_gauge_ha_go_gauge_e2e_subscription_active` | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_subscription_active` |
| 19 | `binary_sensor.go_gauge_ha_go_gauge_e2e_weekly_rate_limited` | *identisch* | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_week_limited` |
| 20 | `sensor.go_gauge_ha_go_gauge_e2e_weekly_nutzung` | `sensor.go_gauge_ha_go_gauge_e2e_weekly_usage` | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_week_percent` |
| 21 | `sensor.go_gauge_ha_go_gauge_e2e_weekly_reset` | *identisch* | `01M2ACPP29X0RV3QJVPRCQJEA3_ws1_week_reset` |

**Bilanz:** 15 umbenannt, 6 waren bereits englisch (`*_reset`, `*_rate_limited`). `unique_id`
identisch 21/21, keine entfallenen/neuen `unique_id` unter den Legacy-Entities, 0 Duplikate,
0 verwaiste `device_id`s. Anzahl go_gauge-Registry-Einträge: **21 → 37** (21 migriert + 16 neu
registriert, siehe ND-1).

## Verdict je Kriterium

| Kriterium | Verdict | Beleg |
|---|---|---|
| Entry migriert v5→7, verlässt `migration_error` | **PASS** | API `state=loaded`, `.storage/core.config_entries` `version=7`; „keine Setup-Fehler" im Sync-Log |
| entity_id-Slug englisch für alle 21 Legacy-Entities | **PASS** | 15/15 deutsche Slugs umbenannt, 6/6 waren bereits englisch; kein deutscher Mapping-Token mehr in den 21 IDs |
| `unique_id` unverändert (21) | **PASS** | 21/21 byte-identisch; keine Duplikate/Orphans |
| History/Statistics an dieselbe Entity gebunden | **PASS** | Recorder-Rebinding in-place (gleiche metadata_id / statistic-id, nur `entity_id`-String neu) |
| Anzeigename-Sprache | **DEUTSCH (by design)** | Systemsprache `de` + `has_entity_name` + `translation_key` → `translations/de.json` |

## History/Statistics — tatsächlich verifiziert

Recorder-Kopie `/tmp/gg-e2e/live/ha.db` (+ `-wal`), Query über Python-`sqlite3`:

- **statistics_meta:** id 8 `sensor.go_gauge_ha_go_gauge_modelle` → `…_models`; id 9
  `…_live_modelle` → `…_live_models`. Die zugehörigen `statistics`-Zeilen (je 2) bleiben erhalten —
  derselbe Statistik-Datensatz, nur umbenannt.
- **states_meta:** ids 51/52/53/54/59/60 und 167–181 behalten ihre `metadata_id`, der
  `entity_id`-String wurde auf den englischen Wert aktualisiert; bereits geschriebene
  `states`-Zeilen (12 bzw. 3 bzw. 2) bleiben vorhanden. Die `*_nutzung`-Rows (179–181) heißen
  jetzt `*_usage` — echtes In-place-Rebinding.
- **Mechanik:** HA-Recorder-Listener `homeassistant/components/recorder/entity_registry.py:31-34`
  (`async_update_statistics_metadata` + `async_update_states_metadata`), getriggert über
  `entity_registry_updated`.

## Anzeigename

Systemsprache `de`/`DE` (HA 2026.9.1). `GoGaugeEntityBase` setzt `_attr_has_entity_name=True`; die
Entities führen `translation_key` statt `_attr_name`, daher löst HA den `friendly_name` aus
`translations/de.json` auf → **deutsch**, z. B. `Go Gauge E2E 5h rolling Nutzung`,
`Go Gauge Konto Günstigstes Modell`, `Go Gauge E2E Warnschwelle`. Das ist die beabsichtigte
Lokalisierung, **kein Defekt** — aber eine Design-Konsequenz: die v6-Namensmigration
(`original_name` englisch) wird beim Entity-Setup durch die `translation_key`-Auflösung wieder
auf den deutschen Wert überschrieben (Registry zeigt `original_name=Modelle`, `Warnschwelle`, …).

## NEUER DEFEKT ND-1 — English-Slug-Ziel kippt für nach der Migration neu registrierte Entities (`de`-System)

Nach dem erfolgreichen Setup registrierte die Integration **16 zuvor nicht existierende** Entities
(Derived-Usage-Sensoren + `pace_red_limit`, die es in v1.2.0 noch nicht gab). **10 davon erhalten
deutsche entity_id-Slugs:**

| entity_id (neu) | unique_id | Slug-Sprache |
|---|---|---|
| `number.go_gauge_e2e_ampel_rot_grenze` | `…_pace_red_percent` | DEUTSCH (`ampel`, `rot_grenze`) |
| `sensor.go_gauge_e2e_5h_rolling_prognose` | `…_ws1_5h_forecast` | DEUTSCH (`prognose`) |
| `sensor.go_gauge_e2e_weekly_prognose` | `…_ws1_week_forecast` | DEUTSCH (`prognose`) |
| `sensor.go_gauge_e2e_monthly_prognose` | `…_ws1_month_forecast` | DEUTSCH (`prognose`) |
| `sensor.go_gauge_e2e_5h_rolling_restbudget` | `…_ws1_5h_remaining` | DEUTSCH (`restbudget`) |
| `sensor.go_gauge_e2e_weekly_restbudget` | `…_ws1_week_remaining` | DEUTSCH (`restbudget`) |
| `sensor.go_gauge_e2e_monthly_restbudget` | `…_ws1_month_remaining` | DEUTSCH (`restbudget`) |
| `sensor.go_gauge_e2e_5h_rolling_restzeit` | `…_ws1_5h_time_to_reset` | DEUTSCH (`restzeit`) |
| `sensor.go_gauge_e2e_weekly_restzeit` | `…_ws1_week_time_to_reset` | DEUTSCH (`restzeit`) |
| `sensor.go_gauge_e2e_monthly_restzeit` | `…_ws1_month_time_to_reset` | DEUTSCH (`restzeit`) |

Die übrigen 6 neuen (`*_pace`, `*_burn_rate`) sind englisch, weil der `de.json`-Text dort bereits
englisch ist.

**Exakte Belege:**

- `homeassistant/generated/languages.py` — `NATIVE_ENTITY_IDS` enthält `de` (im Container verifiziert:
  `'de' in NATIVE_ENTITY_IDS → True`).
- `homeassistant/helpers/entity_platform.py:224-227` —
  `object_id_language = hass.config.language if hass.config.language in languages.NATIVE_ENTITY_IDS else languages.DEFAULT_LANGUAGE`.
  HA 2026.9 bildet Objekt-IDs auf `de`-Systemen also bewusst aus der **deutschen** Übersetzung.
- `custom_components/go_gauge/translations/de.json` — `sensor.forecast="Prognose"`,
  `sensor.remaining="Restbudget"`, `sensor.time_to_reset="Restzeit"`,
  `number.pace_red_limit="Ampel Rot-Grenze"` → genau die beobachteten deutschen Slugs.
- Folge: Da der Entry jetzt **Version 7** hat, läuft `_async_migrate_entity_ids`
  (`custom_components/go_gauge/__init__.py:107-138`) nie wieder — die 10 deutschen Slugs bleiben
  dauerhaft, während die 21 Legacy-Entities englisch sind (gemischtsprachige Registry).
- Zusätzlich unschön: die migrierten Legacy-IDs tragen den **alten** Device-Slug
  `go_gauge_ha_go_gauge_…` (früherer Device-Name „Go Gauge HA"), die neu registrierten den
  aktuellen `go_gauge_e2e_…` (Device „Go Gauge E2E") → inkonsistente Präfixe.

**Optionen (fachlich zu entscheiden):**
(a) Für alle Entities einen expliziten englischen `suggested_object_id` setzen (HA prefixiert diesen
    bei `has_entity_name` **nicht** mit dem Device-Namen, siehe `entity_registry.py:1360`);
(b) Anforderung „entity_id englisch" fallen lassen und HA-native (deutsche) Entity-IDs akzeptieren —
    dann ist die v7-Slug-Migration selbst weitgehend obsolet;
(c) Slug-Migration als wiederkehrende Prüfung statt version-gated bauen.

## Unit-Tests (RC-3)

`python3 -m pytest -q` → **383 passed** (Erstlauf: 324). Die gehärteten Migrations-Tests decken
RC-1 (Entry-Double verbietet `entry.version=`) und RC-2 (reale `go_gauge_ha_…`-Slugs) ab.

## Zustand der Testinstanz nach dem Re-Run

- Entry `01M2ACPP29X0RV3QJVPRCQJEA3`: `version=7`, Runtime `loaded`.
- 37 go_gauge-Registry-Einträge (21 migriert + 16 neu), Devices „Go Gauge E2E" und „Go Gauge Konto",
  keine Duplikate/Orphans.
- Systemsprache unverändert `de`; keine andere Domain angefasst; **kein** `bin/reset`.
- Tokens (`/home/hermes/ha-test/.ha-state/`) wurden nie gelesen/ausgegeben/kopiert.

## Rohdaten (nicht im Repo)

- `/tmp/gg-e2e/rerun_before_snap.json`, `/tmp/gg-e2e/rerun_after_final_snap.json` — Registry/Entry/Meta
- `/tmp/gg-e2e/table_legacy.md`, `/tmp/gg-e2e/table_new.md` — generierte Vergleichstabellen
- `/tmp/gg-e2e/live/ha.db` (+ `-wal`/`-shm`) — Recorder-Kopie für `states_meta`/`statistics_meta`
- `/tmp/gg-e2e/snapshot_v2.py` — Snapshot-Skript (WAL-bewusst nachrüstbar)

## Open Points (aktualisiert)

1. **ND-1 entscheiden** (`suggested_object_id` englisch erzwingen vs. native Entity-IDs akzeptieren) —
   sonst driftet die Slug-Sprache bei jeder Neu-Registrierung auf `de`-Systemen erneut.
2. Kriterium **„Anzeigename englisch"** fachlich klären: `translation_key` + `de.json` liefert bei
   Systemsprache `de` bewusst deutsche `friendly_name` (Design-Konsequenz, kein Defekt).
3. **Präfix-Inkonsistenz** `go_gauge_ha_go_gauge_*` (migriert) vs. `go_gauge_e2e_*` (neu) bewerten.
4. Vorbestehende verwaiste `states_meta`-Rows aus früheren `deeptest`-Workspaces (kein
   Registry-Eintrag) sind **nicht** durch diese Migration verursacht.
