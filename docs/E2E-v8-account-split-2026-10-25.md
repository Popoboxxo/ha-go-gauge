# E2E-Verifikation v8 Konto-Entry-Split (2.0.0) — Protokoll 2026-10-25

**Status: PASS für HA 2026.9.1 nach den Review-Fixes.** Vier dokumentierte Läufe auf
HA 2026.9.1: Lauf 1 **FAIL** (§3), Lauf 2 Restore + funktionale Fixes (§4), Nachtrag-Sync
auf den Deprecation-Fix (§5) und der finale Phase-F-Lauf nach den Review-Fixes (§5a).
Der finale Lauf ist warnungsfrei, idempotent und belegt den `entity_id`-Erhalt über
statische Registry-Zeilen-`id`s. **Weiter offen (§6):** Min-Version HA 2026.8.0.
Der PASS bezieht sich ausschließlich auf HA 2026.9.1, nicht auf 2026.8.x.

**Zielversion:** ha-go-gauge **2.0.0** (`ConfigEntry.VERSION = 8`, Konto-Entry-Architektur)
**Plan:** [`PLAN-account-entry-2.0.0.md`](PLAN-account-entry-2.0.0.md) §8.6 (Matrix), §11.1 R3/R4/R5/R9
**Muster:** [`E2E-v7-entity-id-migration-2026-09-12.md`](E2E-v7-entity-id-migration-2026-09-12.md)

## 1. Umgebung

| Merkmal | Wert |
|---|---|
| Datum | 2026-10-25 |
| HA Core | 2026.9.1 |
| Instanz | `/home/hermes/ha-test`, Container `ha-test` (Docker) |
| Systemsprache | `de`/DE (keine Umschaltung während der Läufe) |
| Repo / Branch | `/home/hermes/repos/ha-go-gauge`, `feat/account-entry` |
| Commit-Kette | `6507f1c` (Lauf 1) → `f5bd678` (Fix Adoptions-Prädikate, Lauf 2) → `9830e67` (Fix Device-Registry-Mapping, Nachtrag-Sync) |
| Deploy-Weg | `bin/sync /home/hermes/repos/ha-go-gauge` (nur eigene Domain), je ~6 s bis API bereit |
| Läufe | 1 (FAIL) · 2 (Restore + Fixes) · Nachtrag (Deprecation-Fix) · finaler Phase-F-Lauf (**PASS**) |
| Rohdaten | `/var/tmp/opencode/gg-e2e/` (finaler Lauf), davor `/var/tmp/opencode/`, `/tmp/` — außerhalb des Repos, keine `.ha-state/`-Kopien |

Der finale Phase-F-Lauf (§5a) lief auf dem Branch-Stand `feat/account-entry` **nach** den
Review-Fixes; für diesen Lauf wurde kein eigener Commit-Hash protokolliert.

Etikette eingehalten: kein `bin/reset`, kein `bin/provision`, kein `bin/set-language`,
kein `docker compose down -v`; andere Domains blieben unverändert.

## 2. Ausgangslage

| Objekt | Zustand |
|---|---|
| Workspace-Entry | `01M2ACPP29X0RV3QJVPRCQJEA3` „Go Gauge E2E“, `VERSION = 7`, `entry_kind = workspace` |
| Token | Dummy-Token → HTTP 401 (Reauth erwartet, kein Testfehler) |
| go_gauge-Entities | 33 Workspace-Entities (u. a. 5 Alt-Katalog-Entities, 2 Alt-Modell-Regler) |
| Backup | `/var/tmp/opencode/go-gauge-e2e-backup-20261025-151504/` (config_entries, entity_registry, device_registry + md5) |

## 3. Lauf 1 (`6507f1c`) — FAIL

Sync 15:15:29, zweiter Sync 15:25:59. Registry danach: 41 aktive go_gauge-Entities
(8 Konto + 33 Workspace), 5 `deleted_entities`, 2 Entries, 2 Devices.

| # | Symptom | Ursache |
|---|---|---|
| **B1** | Adoption No-op: die 5 Alt-Entities landeten in `deleted_entities`, ihre `entity_id`/History war verloren | `_sweep_entities` (account.py:1212-1217) filterte `candidate.platform == "sensor"` **und** `candidate.domain == "go_gauge"`. HA-Semantik ist umgekehrt: `RegistryEntry.platform` = Integrationsdomain, `.domain` = Entity-Domain → 0 Kandidaten |
| **B2** | Alt-Modell-Regler blieben aktiv (`unavailable`), nicht entfernt | `async_remove_legacy_model_settings` (account.py:1674-1689) prüfte identisch vertauscht: `platform not in {"switch","number"}`, `domain != "go_gauge"` → nie ein Treffer |

Folgekette zu B1: Nach der No-op-Adoption verschob `_move_account_device` das Device vom
Workspace- auf den Konto-Entry; HA's `EntityRegistry.async_device_modified` löschte daraufhin
alle Entities des Devices mit altem `config_entry_id`.

| gelöschte Entity (`deleted_entities`) | alte `unique_id` | deleted_at |
|---|---|---|
| `sensor.go_gauge_ha_go_gauge_models` | `01M2ACPP…_model_catalog` | 14:15:41.362658Z |
| `sensor.go_gauge_ha_go_gauge_live_models` | `01M2ACPP…_models_live_count` | 14:15:41.362717Z |
| `sensor.go_gauge_ha_go_gauge_cheapest_model` | `01M2ACPP…_cheapest_model` | 14:15:41.362739Z |
| `sensor.go_gauge_ha_go_gauge_free_models` | `01M2ACPP…_free_models` | 14:15:41.362753Z |
| `binary_sensor.go_gauge_ha_go_gauge_api_reachable` | `01M2ACPP…_api_reachable` | 14:15:41.362766Z |

`modified_at` des Konto-Devices `c2b6ae6c…` = 14:15:41.362455Z — die Löschungen liegen
**0,2 ms** danach. Verbliebene Alt-Regler: `switch.go_gauge_ha_go_gauge_e2e_auto_update_models`,
`number.go_gauge_ha_go_gauge_models_refresh_min_e2e` (beide `unavailable`).

**Warum die Unit-Tests grün waren:** `tests/test_account_adoption.py` (u. a. Zeile 303) mockt
`platform="sensor"` — also exakt die falsche Semantik. Die Tests bestätigten den Bug.
Zusätzlich: Deprecation `device_registry.devices` als Mapping (account.py:1322/1343,
Entfall HA 2027.9.0).

**Fixes:** `f5bd678` (Prädikate invertiert: `platform` = Integrationsdomain, `domain` = Entity-Domain)
und `9830e67` (`device_registry.devices`-Mapping statt Deprecation).

## 4. Lauf 2 — Restore + `f5bd678` — funktionale Fixes bestätigt

Ablauf: `docker compose stop` (Exit 0) → Restore der 3 Registry-Dateien als `root:root 0644`
(containerisierter Kopierweg wegen `config/.storage`-Besitz) → `bin/sync`. Sync #1 14:40:37Z,
Sync #2 (Idempotenz) 14:45:39Z, jeweils „HA bereit (~6 s)“, Setup-Fehler-Check „(keine)“.

| Datei | md5 Backup | md5 restauriert |
|---|---|---|
| `core.config_entries` | `af850ad17a5dd561131673e56426192b` | identisch |
| `core.device_registry` | `ae3efd405ec3cfa0a42febe25889419c` | identisch |
| `core.entity_registry` | `48cf07b194d7195d01f9ad866c500878` | identisch |

**Adoption — Beobachtung aus Lauf 2 (Registry-Zeile unverändert):**

| alt `entity_id` | `entity_id` nachher | `unique_id` vorher | `unique_id` nachher | `config_entry_id` vorher→nachher | `device_id` vorher→nachher | deleted? | Registry-Zeilen-`id` (statisch bestätigt, §5a.2) |
|---|---|---|---|---|---|---|---|
| `sensor.go_gauge_ha_go_gauge_models` | unverändert | `01M2ACPP…_model_catalog` | `account_model_catalog` | WS → Konto | `c2b6ae…` → `c2b6ae…` | nein | `7a2171811c3d785a7c57b9aae71f5a47` |
| `sensor.go_gauge_ha_go_gauge_live_models` | unverändert | `01M2ACPP…_models_live_count` | `account_models_live_count` | WS → Konto | `c2b6ae…` → `c2b6ae…` | nein | `ebfd72a6…` |
| `sensor.go_gauge_ha_go_gauge_cheapest_model` | unverändert | `01M2ACPP…_cheapest_model` | `account_cheapest_model` | WS → Konto | `c2b6ae…` → `c2b6ae…` | nein | `e314a7f4…` |
| `sensor.go_gauge_ha_go_gauge_free_models` | unverändert | `01M2ACPP…_free_models` | `account_free_models` | WS → Konto | `c2b6ae…` → `c2b6ae…` | nein | `a53f0d41…` |
| `binary_sensor.go_gauge_ha_go_gauge_api_reachable` | unverändert | `01M2ACPP…_api_reachable` | `account_api_reachable` | WS → Konto | `c2b6ae…` → `c2b6ae…` | nein | `f8be8799…` |

Die Registry-Zeilen-`id` ist vorher == nachher (z. B. `model_catalog`
`7a2171811c3d785a7c57b9aae71f5a47`) → dieselbe Zeile, damit History/Area/Labels gebunden bleiben.
Genau 5 Konto-Katalog-Entities, keine Duplikate, keine neuen `sensor./binary_sensor.go_gauge_konto_*`,
keine `account_*`-`unique_id` in `deleted_entities`.

> **Einordnung:** Die Tabelle dokumentiert die Beobachtung aus Lauf 2; die Werte der
> Registry-Zeilen-`id`s wurden im finalen Phase-F-Lauf (§5a.2) als statisch bestätigt
> (vor == nach über alle Syncs). Der förmliche Erhaltungs-Nachweis ist damit erbracht,
> siehe §6c.

**Alt-Settings entfernt (B2-Fix):**

| alt `entity_id` | `unique_id` | aktiv? | `deleted_entities` |
|---|---|---|---|
| `switch.go_gauge_ha_go_gauge_e2e_auto_update_models` | `01M2ACPP…_auto_update_models` | nein | ja |
| `number.go_gauge_ha_go_gauge_models_refresh_min_e2e` | `01M2ACPP…_models_refresh_minutes` | nein | ja |

**Entries / Devices / States:**

| Objekt | Wert |
|---|---|
| Workspace-Entry | `01M2ACPP29X0RV3QJVPRCQJEA3`, v8, `entry_kind = workspace`, Token gesetzt, `usage_refresh_minutes: 10`, `loaded` |
| Konto-Entry | `01M5SR3G2C71JR1AGGMTAHMZH7`, v8, `unique_id = go_gauge_account`, `entry_kind = account`, **kein Token**, options `auto_update_models: true`, `models_refresh_minutes: 60`, `loaded` |
| Konto-Device | `c2b6ae6c87eefc2300881989b95f2475`, Identifier `(go_gauge, account)`, Owner = Konto-Entry |
| Workspace-Device | `1f48c0ed3e894fe113c4c22adeeddccb`, Identifier `(go_gauge, 01M2ACPP…)` |
| Katalog | `sensor.go_gauge_ha_go_gauge_models` = **45**, `live_models` = **38**, `cheapest` = `muse-spark-1.2-contributor` |
| `free_models` / `api_reachable` | `unknown` (leere Liste, zulässig) / `on` (`fresh_ok`) |
| Workspace-Status | `sensor.go_gauge_e2e_api_status` = `auth_error` (HTTP 401 – Dummy-Token), übrige WS-Entities `unavailable` |
| Reauth | Flow `01M5SRCT0S57XG64MCFPV440FV`, `source = reauth`, Step `reauth_confirm`; Entry bleibt `loaded` |

**Settings-/Button-Test:** Switch off → `off` + Option `false`; on → `on` + `true`;
Number 90 → `90`; 60 → `60`; Options final `{auto_update_models: true, models_refresh_minutes: 60}`.
Button-Press: `models_updated_at` 14:40:46.221 → 14:45:21.626, Button-State 14:45:21.223,
danach keine Log-Fehler.

**Idempotenz (2. Sync):** Snapshot vor/nach **byte-identisch** (diff leer) — 39 aktive
go_gauge-Entities, 2 deleted, 2 Entries, 2 Devices, Settings `true`/60 erhalten; die 5
adoptierten `entity_id`s weiterhin aktiv. Erwarteter einmaliger Katalog-Fetch beim Konto-Setup.

## 5. Nachtrag — Sync auf `9830e67` (Deprecation-Fix)

Dieser Sync prüfte nur den Deprecation-Fix (Log-/Registry-Gegenprüfung), er ist **kein**
vollständiger Bestätigungslauf der Matrix. Der vollständige Bestätigungslauf folgt in §5a.

| Prüfpunkt | Ergebnis |
|---|---|
| Sync | Exit 0, HA bereit ~6 s, Setup-Fehler-Grep „(keine)“, Sync-Start 16:07:44 |
| `device_registry.devices`-Deprecation | **0 Treffer** (letzter Treffer 15:45:50 = vor dem Sync) |
| ERROR / CRITICAL / Traceback / `MIGRATION_ERROR` / `ValueError` / „does not generate unique IDs“ | **0** |
| Config-Entries | 2 (beide v8) vor == nach |
| Aktive go_gauge-Entities / States | 39 / 39 vor == nach |
| go_gauge-Devices | 2 vor == nach |
| `entry_id`s, `unique_id`s, `entity_id`s, `device_id`s, Entry-Daten/-Optionen | vor == nach (innerhalb dieses Syncs, kein Migrations-Nachweis) |
| `unique_id`-Duplikate | keine |
| Reauth | Issue `config_entry_reauth_go_gauge_01M2ACPP…` aktiv (erwartet) |

Erwartete Warnungen: 401-Reauth-Meldung `API-Token abgelehnt (HTTP 401) - Reauth wird gestartet`
sowie HA-Standard „not tested by HA“. Deploytes `account.py` (sha256 `107945d9…`) enthält keine
`dev_reg.devices`-Nutzung mehr.

## 5a. Finaler Phase-F-Lauf nach den Review-Fixes — PASS (HA 2026.9.1)

Drei aufeinanderfolgende Syncs auf dem Branch-Stand nach den Review-Fixes, alle mit
Exit 0, jeweils „HA bereit" nach ~6–8 s, keine Setup-Fehler. Rohdaten:
`/var/tmp/opencode/gg-e2e/`.

> **Zuordnung M1/M2/M3:** Die Messreihe wurde als M1/M2/M3 geführt. Die ursprüngliche
> Definition der drei Messpunkte ist aus den vorliegenden Rohdaten nicht rekonstruierbar;
> die Gruppen unten sind deshalb **neutral als Messpunkt-Gruppen** geführt und enthalten
> ausschließlich die erhobenen Fakten — keine inhaltliche Interpretation.

### 5a.1 Messpunkt-Gruppe M1 — Deploy, Setup, Log

| Prüfpunkt | Ergebnis |
|---|---|
| Syncs | 3 × Exit 0, „HA bereit" nach ~6–8 s, keine Setup-Fehler |
| Grep auf ERROR / CRITICAL / Traceback / `MIGRATION_ERROR` / `ValueError` / „does not generate unique IDs“ aus `go_gauge` | **0 Treffer** |
| `device_registry.devices`-Deprecation | **0 Treffer** |
| Erwartet und zulässig | 401-Reauth-Warnung (`API-Token abgelehnt (HTTP 401)`) und HA-Standard „not tested by HA“ |
| Config-Entries | 2 (Workspace + Konto), beide **v8.1** (Versionsstand des finalen Laufs; in Lauf 2/§4 noch `v8`), `state = loaded` |

### 5a.2 Messpunkt-Gruppe M2 — statische Registry-Identität

Alle fünf Konto-Katalog-Entities sind über die Syncs hinweg unverändert in `entity_id`,
`unique_id`, Registry-Zeilen-`id`, `device_id` und `config_entry_id`:

| `entity_id` | `unique_id` | Registry-Zeilen-`id` |
|---|---|---|
| `sensor.go_gauge_ha_go_gauge_models` | `account_model_catalog` | `7a217181…` |
| `sensor.go_gauge_ha_go_gauge_live_models` | `account_models_live_count` | `ebfd72a6…` |
| `sensor.go_gauge_ha_go_gauge_cheapest_model` | `account_cheapest_model` | `e314a7f4…` |
| `sensor.go_gauge_ha_go_gauge_free_models` | `account_free_models` | `a53f0d41…` |
| `binary_sensor.go_gauge_ha_go_gauge_api_reachable` | `account_api_reachable` | `f8be8799…` |

Damit ist der `entity_id`-Erhalt für Bestandsinstallationen belegt: Es entsteht keine neue
Registry-Zeile, History/Area/Labels bleiben an dieselbe Zeile gebunden.

**Bestand:** 39 aktive go_gauge-Entities, 2 `deleted` (Alt-Settings inaktiv), 2 Devices,
keine Duplikate.

**Konto-Button — Key-Wechsel bei stabiler `entity_id`:**

| Merkmal | Wert |
|---|---|
| `entity_id` | `button.go_gauge_konto_konto_aktualisieren` — **unverändert** |
| `unique_id` | `account_refresh` |
| Registry-Zeilen-`id` | `24ba5656…` |
| `translation_key` | jetzt `refresh` |
| `original_name` / `object_id_base` | `Konto aktualisieren` → **`Aktualisieren`** |
| `friendly_name` | `Go Gauge Konto Aktualisieren` |

Begründung: Ein Wechsel von `translation_key`/Anzeigename erzeugt bei HA **keine** neue
`entity_id`, solange die Registry-Zeile über die stabile `unique_id` (`account_refresh`)
gefunden wird. Bestandsinstallationen behalten daher
`button.go_gauge_konto_konto_aktualisieren`; nur der angezeigte Name ändert sich.
Hinweis: `translation_key = refresh` wird mit dem Workspace-Button geteilt — unterschieden
werden beide über das Gerät („Go Gauge Konto“ vs. „Go Gauge <Workspace>“), weshalb die
abgeleitete `entity_id` device-präfixiert bleibt.

**Button-Press:** `models_updated_at` 15:44:46.614 → 15:45:55.345, `outcome = ok`,
kein Fehler.

**Switch/Number:** Switch `off` → Option `false`, `on` → Option `true`; Number auf `90`
→ Option `90`, zurück auf `60` → Option `60`. Number-Bounds: **1–1440**.

**States:**

| Objekt | Wert |
|---|---|
| `models` | 45 |
| `live_models` | 38 |
| `cheapest` | `muse-spark-1.2-contributor` |
| `free_models` | `unknown` (leere Liste) |
| `api_reachable` | `on` (`fresh_ok`) |
| Workspace-`api_status` | `auth_error` |
| Reauth | aktiv: Step `reauth_confirm` + Issue (Dummy-Token, erwartet) |

### 5a.3 Messpunkt-Gruppe M3 — Idempotenz

| Prüfpunkt | Ergebnis |
|---|---|
| Sync 3 (Idempotenz-Lauf) | **byte-identisch** (md5) für `core.entity_registry`, `core.device_registry`, `core.config_entries` — vor == nach |
| Aktive go_gauge-Entities / `deleted` / Devices / Entries / States | 39 / 2 / 2 / 2 / 243 — **vor == nach** |
| Settings nach dem Lauf | `{auto_update_models: true, models_refresh_minutes: 60}` |

Damit ist die Idempotenz des v8-Setups und der Adoption über drei Syncs belegt: kein
Wiederanlegen, kein Entity-Verlust, keine Registry-Drift. Die erwartete 401-Reauth-Warnung
und HA's „not tested by HA“ bleiben als zulässige Meldungen bestehen (Dummy-Token).

## 6. Offene Punkte

| # | Punkt |
|---|---|
| **a** | **Warnungsfreiheit — BELEGT/erledigt.** Der finale Phase-F-Lauf (§5a.1) zeigt **0 Treffer** für ERROR/CRITICAL/Traceback/`MIGRATION_ERROR`/`ValueError`/„does not generate unique IDs“ und **0 Treffer** der `device_registry.devices`-Deprecation. Zulässig bleiben die 401-Reauth-Warnung und HA's „not tested by HA“. |
| **b** | **Min-Version 2026.8.0 — OFFEN/PENDING, nicht belegt.** Es liegt kein Lauf in einem 2026.8-Container vor (Lauf A der Matrix aus Plan §8.6 bleibt offen). Der PASS aus §5a gilt ausschließlich für HA 2026.9.1. Bei Abweichung Version anheben und Plan/README/`hacs.json` nachziehen. |
| **c** | **`entity_id`-Erhalt — BELEGT/erledigt.** Der finale Lauf belegt statische Identität über alle Syncs: die 5 Konto-Entities und der Konto-Button behalten `entity_id`, `unique_id`, Registry-Zeilen-`id`, `device_id` und `config_entry_id` (vor == nach, §5a.2). Keine neuen/duplizierten Registry-Zeilen. |
| **d** | **Canon v1.4.0 gepinnt und nutzbar**, inhaltlicher Nachzug auf Canon v1.5 bleibt Submodul-Follow-up (Details im Statusblock unten). |
| **e** | Dev-Instanz hat einen aktiven Reauth-Flow wegen des Dummy-Tokens — erwartet, kein Produktfehler. |
| **f** | Stale `bin/ha`-Lock (`.ha-state/refresh.lock`) ist ein Harness-Nebenbefund (Lock blieb vor `rmdir` liegen, Folgeläufe blockierten im Spin-Lock) — kein Produktfehler. |
| **g** | Akzeptierte Risiken (Plan §11.1): **R3** `entity_id`-Wechsel nach Konto-Löschung bei deduplizierten Slugs, **R4** kurzes `unavailable` beim ersten v8-Start, **R5** Duplikat-Legacy-Entities werden deaktiviert (nicht gelöscht), **R9** kein Polling bei `auto_update_usage = false`. |

**Canon-Status:**

- `external/ha-gauge-standard` ist auf **Canon v1.4.0** gepinnt und nutzbar —
  `check_canon.py` gegen dieses Repo: **0 Fehler / 0 Warnungen**.
- Inhaltlich hinkt v1.4.0 hinterher (kein `account_entry`-Flag, `api_reachable` noch
  `last_update_success`-basiert, „Refresh beider Zyklen“) → **Canon-v1.5-Kandidat im
  Submodul-Repo**, kein Parent-Edit.
- **Ausstehend:** Release-Dreiklang für den Parent **2.0.0** (Commit → Tag → Push →
  GitHub-Release inkl. Changelog) sowie der HACS-Update-Test (Update von der
  Vorgängerversion auf der Dev-Instanz, erst nach dem Release). Der Submodul-Pin ist nach
  dem Parent-Release nachzuziehen.

## 7. Fazit

**PASS für HA 2026.9.1 (nach den Review-Fixes).** Belegt sind: Registry-Adoption der fünf
Konto-Entities mit stabilen statischen IDs (`entity_id`, `unique_id`, Zeilen-`id`,
`device_id`, `config_entry_id` vor == nach, §5a.2), Entfernung der Alt-Settings
(2 × `deleted`, inaktiv), 401-Setup mit `auth_error` + Reauth statt `unavailable`,
Konto-Settings/Button inklusive funktionierendem Key-Wechsel auf `refresh` bei
unveränderter `entity_id`, Warnungsfreiheit (0 Treffer für Fehler- und
Deprecation-Muster) und Idempotenz über drei Syncs (Registry byte-identisch). Die in
Lauf 1 gefundenen Bugs B1/B2 sind durch `f5bd678` behoben, der Deprecation-Fix durch
`9830e67`, und in §5/§5a gegengeprüft.

**Verbleibend offen:** HA 2026.8.0 als Min-Version (§6b — der PASS deckt nur 2026.9.1
ab), Canon-v1.5-Nachzug im Submodul, Release-Dreiklang (Tag/Push/GitHub-Release für
2.0.0) und der HACS-Update-Test.

