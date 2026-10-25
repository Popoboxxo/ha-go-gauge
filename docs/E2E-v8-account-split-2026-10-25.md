# E2E-Verifikation v8 Konto-Entry-Split (2.0.0) — Protokollvorlage (2026-10-25)

> **STATUS: NICHT AUSGEFÜHRT — pending.**
> Dieses Dokument ist eine **Vorlage**. Der E2E-Lauf auf der geteilten Instanz
> `/home/hermes/ha-test` ist eine **Nutzeraktion**; in der Dokumentations-Session
> wurde **kein** `bin/sync`, `bin/reset` oder Dev-Deploy ausgeführt.

**Zielversion:** ha-go-gauge **2.0.0** (`ConfigEntry.VERSION = 8`, Konto-Entry-Architektur)
**Plan:** [`PLAN-account-entry-2.0.0.md`](PLAN-account-entry-2.0.0.md) §8.6 (Matrix),
§11.1 R3/R4/R5/R9 (akzeptierte Risiken)
**Repo:** `/home/hermes/repos/ha-go-gauge`, Branch `feat/account-entry`
**Instanz:** geteilte HA-Testinstanz `/home/hermes/ha-test` (Etikette: dortige `README.md`)
**Muster:** [`E2E-v7-entity-id-migration-2026-09-12.md`](E2E-v7-entity-id-migration-2026-09-12.md)

## Ergebnis vorab

**STATUS: pending** — keine Zeile dieses Protokolls ist bisher belegt. Nach dem
Lauf je Zeile `PASS` / `FAIL` eintragen und die Beobachtungen (Registry-Snapshot,
Entry-Liste, Log-Auszug) unter *Rohdaten* verlinken.

## Ausgangslage / Erwartungen

- Genau **EINE** Konto-Entry (`unique_id = go_gauge_account`, Titel `Go Gauge Konto`,
  `entry_kind = "account"`, kein Token) neben N Workspace-Entries
  (`entry_kind = "workspace"`).
- Genau **EIN** Konto-Gerät `(DOMAIN, "account")`, Name `Go Gauge Konto`;
  Workspace-Geräte `(DOMAIN, <entry_id>)`, Name `Go Gauge {Workspace}`.
- Konto-Entities mit kanonischer `unique_id`:
  `account_model_catalog`, `account_models_live_count`, `account_cheapest_model`,
  `account_free_models`, `account_api_reachable`,
  `account_refresh` (Button), `account_auto_update_models` (Switch, Default ON),
  `account_models_refresh_minutes` (Number, 1–1440, Default 60).
- Workspace-Entities: Usage-Fenster-Sensoren, `api_status`, Rate-Limited/
  Subscription-Binary-Sensoren, `_refresh` (nur Usage), `_auto_update_usage`,
  `_warn_percent`, `_pace_red_percent`, `_usage_refresh_minutes`.
- **Entfernt:** Workspace-`auto_update_models` und Workspace-`models_refresh_minutes`.

## Vorbedingungen (Etikette der geteilten Instanz)

- Nur die eigene Domain syncen (`bin/sync /home/hermes/repos/ha-go-gauge`);
  Registry/Config-Entries anderer Domains müssen den Neustart überleben.
- `bin/reset` löscht **alle** Daten aller Plugins → **nur nach Abstimmung**, nie
  als Vorbereitung für einen einzelnen Lauf.
- `bin/set-language de|en` betrifft die **ganze** Instanz → nur koordiniert.
  Der Sprach-/Slug-Check läuft auf der bestehenden Systemsprache; keine
  Umschaltung während des Laufs.
- Tokens (`.ha-state/`) nie lesen, ausgeben, kopieren oder committen.
- `unique_id`s nie ändern — die geteilte Registry akkumuliert.
- E2E ist **kein** Ersatz für die Unit-Tests (578 grün), sondern der Nachweis der
  HA-Registry-/Device-Mechanik auf der echten Instanz.

## Matrix (aus Plan §8.6)

| Lauf | Umgebung | Pflichtschritte | Abbruchkriterium |
|---|---|---|---|
| **A — Min-Version** | ephemerer Docker-Container mit **HA 2026.8.0** | install → 1 Workspace → Konto-Entry entsteht → 5 Konto-Entities + Button + Switch + Number am Konto-Device → keine `ERROR`-Zeile | Konto-Device entsteht unter dem Workspace-Device, eine Entity fehlt, oder die Adoption loggt `TypeError` |
| **B — v7→v8-Upgrade** | Kopie der geteilten Instanz mit v1.7.0 + 2 Workspaces (danach `bin/reset` **nur nach Abstimmung**) | `bin/sync` → Neustart → 5 Entities behalten `entity_id`/History/Area/Labels, hängen am Konto-Device, `unique_id = account_*`; genau 1 Konto-Device; alte Workspace-Regler weg; 0 offene Repair-Issues | jeder `MIGRATION_ERROR`, jede `ValueError`-Zeile, Duplikat-Device, verbliebene Alt-Regler |
| **C — Aktuell-Instanz** | `/home/hermes/ha-test` | Adoptionswiederholung (2. Neustart) → 0 Registry-Änderungen im Log; Konto löschen → Re-Create beim nächsten Reload; deaktivieren → Entities `unavailable`, kein Re-Enable; Switch aus → kein Katalog-Fetch mehr (Log), Switch an → Fetch im Intervall | Re-Create trotz `disabled_by`, Doppel-Entry, Fetch trotz Auto-Refresh OFF |
| **D — Sprach-/Slug-Check** | `de`-Systemsprache | `entity_id`-Slugs der übernommenen Entities unverändert (kein Re-Slug durch Device-Umzug) | `_2`-Suffix neu entstanden |

## Checkliste je Lauf

### A — Min-Version (HA 2026.8.0, ephemerer Container)

- [ ] Container startet mit HA **2026.8.0**; Version im UI/`/api/config` belegt.
- [ ] Integration installiert, 1 Workspace angelegt (Token oder `skip_validation`).
- [ ] Genau 1 Konto-Entry erscheint **ohne** manuellen „Integration hinzufügen"-Schritt.
- [ ] Konto-Device `Go Gauge Konto` existiert genau einmal und **nicht** unter dem Workspace-Device.
- [ ] 5 Konto-Entities + `account_refresh` + `account_auto_update_models` +
      `account_models_refresh_minutes` vorhanden; `unique_id`s kanonisch.
- [ ] Konto-Settings live wirksam (Switch/Number ohne Reload), Defaults ON/60.
- [ ] Keine `ERROR`-Zeile; insbesondere kein `TypeError` aus
      `async_update_device(new_config_entry_id=…)`.
- **Abbruch:** fehlende Entity, Konto-Device unter Workspace-Device, `TypeError`,
  `adoption_unsupported`-Issue.

### B — v7→v8-Upgrade (v1.7.0 + 2 Workspaces)

- [ ] Ausgangslage dokumentiert: v1.7.0-Entries (`VERSION = 7`), 2 Workspaces,
      alte Workspace-Regler vorhanden.
- [ ] `bin/sync /home/hermes/repos/ha-go-gauge` → HA neu gestartet, Entry `loaded`.
- [ ] 5 Konto-Entities behalten `entity_id`, History, Area und Labels.
- [ ] `unique_id` der 5 Entities = `account_*`; keine Duplikate/Orphans.
- [ ] Genau 1 Konto-Device; keine verwaisten Alt-Devices.
- [ ] Workspace-Regler `auto_update_models` / `models_refresh_minutes` sind
      aus der Registry entfernt (nicht nur `unavailable`).
- [ ] 0 offene Repair-Issues; bei Duplikaten: `account_entity_collision_*`
      nennt `winner`/`losers`, Verlierer sind deaktiviert (nicht gelöscht).
- [ ] Übergangsfenster beobachtet: 5 Entities kurz `unavailable`, danach `ok`.
- **Abbruch:** `MIGRATION_ERROR`, `ValueError`-Zeile, Duplikat-Device,
  verbliebene Alt-Regler.

### C — Aktuell-Instanz / Wiederholung

- [ ] Zweiter HA-Neustart: **0** Registry-/Device-Schreibzugriffe (Idempotenz).
- [ ] Konto-Entry löschen → beim nächsten Workspace-Reload/HA-Start neu erzeugt;
      dabei dokumentieren, ob eine zuvor deduplizierte `entity_id` (`_2`) wechselt
      (akzeptiertes Risiko R3).
- [ ] Konto-Entry deaktivieren → Entities `unavailable`, **kein** Re-Create,
      **kein** Re-Enable; Workspaces laufen weiter.
- [ ] Switch `account_auto_update_models` aus → kein Katalog-Fetch im Log
      (Publish-Tick bleibt HTTP-frei); an → Fetch im Basisintervall.
- [ ] Workspace `auto_update_usage` aus → gar kein Polling mehr
      (`update_interval = None`, kein 24-h-Fallback; R9).
- [ ] `api_reachable`-Matrix stichprobenartig: frischer Erfolg → ON;
      nur 401/kein Abo → unknown; nur frische Fehlschläge → OFF;
      keine frische Evidenz → unknown.
- **Abbruch:** Re-Create trotz `disabled_by`, Doppel-Entry, Fetch trotz
  Auto-Refresh OFF.

### D — Sprach-/Slug-Check (`de`)

- [ ] `entity_id`-Slugs der übernommenen Konto-Entities unverändert
      (kein Re-Slug durch den Device-Umzug).
- [ ] Kein neues `_2`-Suffix bei den 5 Konto-Entities.
- [ ] Konto-Settings-Entities und `account_refresh` einmalig vorhanden.
- **Abbruch:** neu entstandenes `_2`-Suffix.

## Zu sammeln (Rohdaten, nicht ins Repo)

- Registry-Snapshot vor/nach (`unique_id`, `entity_id`, `config_entry_id`, `device_id`).
- `/api/config/config_entries/entry` (Entry-Liste, `version`, `state`).
- Geräte-Registry (Konto-Device, Workspace-Devices, Duplikate).
- Repair-Issues (`/api/repairs` bzw. Issue-Registry).
- HA-Log-Auszug des jeweiligen Neustarts (`docker compose logs --since 5m`).
- Recorder-Metadaten (`states_meta`/`statistics_meta`) für die History-Bindung.

Ablage außerhalb des Repos (z. B. `/tmp/gg-e2e-v8/`), keine Tokens, keine
`.ha-state/`-Kopien.

## Offene Punkte / Hinweise

1. **Canon im Submodul:** `external/ha-gauge-standard` muss auf **Canon v1.4**
   (Konto-Entry, `account_entry`, zwei Buttons, Konto-Settings,
   Evidenz-/TTL-Regel) aktualisiert und als **Submodul-Release `v1.4.0` vor dem
   Parent-Release** veröffentlicht werden. Im Parent-Repo wird nur der gepinnte
   Commit nachgezogen; das Submodul-Repo ist hier **nicht** editiert.
2. **E2E pending:** Kein Lauf in dieser Dokumentations-Session. Der
   v7→v8-Upgrade (Lauf B) braucht eine Kopie der geteilten Instanz; `bin/reset`
   nur nach Abstimmung.
3. **Akzeptierte Risiken** (Plan §11.1): R3 (`entity_id`-Wechsel nach
   Konto-Löschung bei deduplizierten Slugs), R4 (kurzes `unavailable` beim
   ersten v8-Start), R5 (Duplikat-Legacy-Entities werden deaktiviert, nicht
   gelöscht), R9 (kein Polling bei `auto_update_usage = false`).
4. **Mindestversion:** Der Lauf A ist der Nachweis für HA 2026.8.0
   (`async_update_device(new_config_entry_id=…)`); bei Abweichung Version
   anheben und Plan/README/`hacs.json` nachziehen.
