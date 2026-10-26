# Plan — Burn-Lookback & Kalendermonat-Forecast (go_gauge 2.1.0)

**Status:** freigegeben (User-Bestätigung liegt vor)
**Zielversion:** `go_gauge` 2.1.0 · Canon 1.7.0
**ConfigEntry-Version:** bleibt **8** (kein Migrator, kein Schema-Bruch)
**Branch:** `feat/burn-lookback-settings`

---

## 1. Ziel

Die Burn-Rate- und Forecast-Berechnung von `go_gauge` wird konfigurierbar und
belastbarer:

1. Der Burn-Rate-Lookback ist nicht mehr hart auf 2 h verdrahtet, sondern pro
   Workspace und pro Fenster (5h / week / month) über drei Slider einstellbar.
2. Die Burn-Rate nutzt eine OLS-Regression (mit Achsenabschnitt) über alle Samples
   im konfigurierten Lookback statt einer Zwei-Punkt-Steigung.
3. Der Forecast unterscheidet zwei Methoden: Ø-Pace (`pct * T / t`) nach einer
   Mindest-Einschaltzeit und einen Recent-Rate-Fallback davor.
4. Das Monatsfenster nutzt die **tatsächliche Kalendermonatlänge** aus `resetsAt`
   statt der nominalen 30 Tage.
5. Ein Diagnostics-Block macht Lookback, Sample-Counts, effektive Lookback-Sekunden
   und die gewählte Forecast-Methode debugbar.
6. Die Canon-Governance wird gehärtet (`mode`, `step`, `default_value`,
   `entity_category` werden geprüft) und um die neuen Entities erweitert.

**Nicht-Ziele:** keine Änderung bestehender `unique_id`s, kein neuer
ConfigEntry-Migrationsschritt, keine Persistenz der Sample-Historie über einen
HA-Restart hinweg (die In-Memory-Semantik bleibt), keine neue Pace-Entity.

---

## 2. Entscheidungsprotokoll (D1–D24)

| ID | Entscheidung | Begründung |
|----|--------------|------------|
| **D1** | Ebene: **Workspace** (pro Config-Entry) | Burn-Rate ist eine Eigenschaft des Workspace-Budgets, nicht des Kontos. Konto-Entry bleibt unberührt. |
| **D2** | Granularität: **3 Number-Entities**, je Fenster eine | Fenster haben unterschiedliche Dynamik (5h-Rolling vs. Woche vs. Monat) — ein gemeinsamer Wert wäre ein falscher Kompromiss. |
| **D3** | Reset: **1 Button pro Workspace**, setzt **nur** die drei Burn-Lookbacks auf Defaults | Keine Kopplung an Sample-Historie, Warnschwelle, Pace-Rot-Schwelle oder Refresh-Intervall — der Button muss eng und vorhersagbar sein. |
| **D4** | Reset-Kategorie: `EntityCategory.DIAGNOSTIC`, `enabled` | Ein Reset ist ein Diagnose-/Reparaturwerkzeug, keine Konfiguration. Sichtbar (nicht disabled), damit er im UI auffindbar bleibt. |
| **D5** | Einheit: **Minuten** | Deckungsgleich mit `usage_refresh_minutes`; Slider-Range 5–10080 ist als Minutenangabe verständlich. |
| **D6** | Ranges/Defaults: 5h min 15 / max 300 / default 120 / step 5 · week min 60 / max 10080 / default 720 / step 60 · month min 60 / max 10080 / default 2880 / step 60 | 5h-Fenster ist 300 min lang → Lookback darf das Fenster nicht übersteigen. Week/Month decken bis 7 Tage ab (Max = 10080 min). |
| **D7** | Modus: `NumberMode.SLIDER` **nur** für die drei neuen Numbers | Große, grob gerasterte Bereiche sind als Slider bedienbar; die bestehenden Numbers bleiben `BOX` (präzise Zahleneingabe). |
| **D8** | Burn-Mathe: **OLS-Steigung mit Achsenabschnitt** über alle Samples im Lookback; 2 Samples **und** Mindestspanne bleiben Pflicht; negative Steigung → `None` | OLS mittelt Ausreißer über alle Punkte, die Zwei-Punkt-Steigung kippt bei einem einzelnen verrauschten Poll. `None` bei Falltendenz ist bestehende Semantik und bleibt. |
| **D9** | Retention: Samples bis zum **Max-Slider-Wert** aufbewahren (5h 300 min, week/month 10080 min), **Cap 10080 Samples pro Fenster**; Erhöhen des Lookbacks wirkt sofort | Sonst wäre ein erhöhter Lookback leer, bis genug neue Samples anfallen. Der Cap verhindert unbegrenztes Wachstum im Speicher. |
| **D10** | Reset-Erkennung: Historie löschen bei **Prozent-Drop ODER Änderung von `resets_at`** | Ein Fenster-Rollover ist nicht immer mit einem Prozent-Drop verbunden (z. B. 100 % → 100 % bei neuem Fenster). `resets_at` ist das verlässlichere Signal. |
| **D11** | Forecast: Ø-Pace (`pct * T / t`) ab `t_min`; davor Recent-Rate-Fallback (`pct + burn_rate * remaining_h`); `pct == 0` → `0.0`; `t_min = max(15 min, 1 % der tatsächlichen Fensterlänge)` | Nach wenigen Minuten ist `pct/t` statistisch wertlos (Divisor nahe 0). Der Fallback nutzt die gemessene Steigung, solange der Ø-Pace noch nicht belastbar ist. |
| **D12** | Ampel nutzt **Ø-Pace**; `forecast_recent` ist **Attribut** an Forecast und Pace; **keine zweite Pace-Entity** | Eine Ampel pro Fenster bleibt die UI-Wahrheit; die Zusatzinformation gehört als Attribut daneben, nicht als Entity-Duplikat. |
| **D13** | Options-Flow: die drei Lookbacks als Zahlenfelder mit Range-Validierung; fehlenden `pace_red_percent`-String ergänzen | Der Options-Flow ist der zweite Weg zu denselben Werten (neben den Slidern) und muss validieren. `pace_red_percent` fehlt heute in allen Sprachdateien → Roh-Key im UI. |
| **D14** | Diagnostics: voller Debug-Block pro Workspace: `burn_lookback_minutes` je Fenster, Sample-Counts, effektive Lookback-Sekunden, `forecast_method` / `elapsed_fraction` | Ohne diese Werte ist „Burn-Rate ist None" nicht diagnostizierbar. Keine Tokens, keine Secrets. |
| **D15** | Versionierung: `go_gauge` **2.1.0**, Canon **1.7.0**, ConfigEntry `VERSION` bleibt **8** (kein Migrator) | MINOR: neue Entities, aber kein Bruch. Kein Entry-Schema-Change → kein Migrator nötig (Canon-Regel „VERSION nur mit Migrator"). |
| **D16** | Canon-Checker **voll härten**: `mode`, `step`, `default_value`, `entity_category` in Schema + `check_canon.py`; bestehende Numbers als `BOX`/step 1 nachziehen | Sonst könnten neue Entities mit beliebigem Modus/Step entstehen. Die Härtung ist nur mit vollständigem Schema möglich → gehört in Phase 2. |
| **D17** | `command_gauge`: Core Canon + registrierte Deviations **⚠️ CG-N5** (drei Lookback-Numbers) und **CG-B6** (Reset-Button), je mit Schließungsbedingung; `command_gauge` zieht später nach | `command_gauge` hat dieselbe Architektur; die Abweichung wird sichtbar registriert statt still geduldet. |
| **D18** | PR-Reihenfolge: **2-phasiger Canon** (`reserve` → `activate`), alle CI-Läufe grün | Phase 1 reserviert die Keys, ohne Pflicht-Entities zu erzeugen → kein rotes CI zwischen den Repos. Phase 2 aktiviert. |
| **D19** | Naming: **„Burn Lookback"** / **„Reset Burn Lookbacks"** | Sprechend und konsistent mit dem bestehenden `burn_rate`-Vokabular. |
| **D20** | Slider-Kategorie: `EntityCategory.CONFIG` | Lookbacks sind Nutzerkonfiguration, keine Diagnose. |
| **D21** | Guard: Attribut `lookback_too_short` am **Burn-Rate-Sensor** | Macht im UI sichtbar, warum `unknown` anliegt — ohne Log-Spam. |
| **D22** | Alle neuen Attribute im **Canon-Schema** verankern (burn_rate: `lookback_minutes`, `samples`, `lookback_too_short`; forecast/pace: `forecast_recent`, `forecast_method`, `elapsed_fraction`, `window_seconds`) | `check_canon.py` validiert `extra_state_attributes` gegen das Schema — neue Attribute ohne Schema-Eintrag sind ein Fehler. |
| **D23** | `resetsAt` fehlt/ungültig: Forecast `None`, `forecast_method="unavailable"` (kein Fake-Wert) | Konsistent mit der bestehenden „None statt Fake-Zahl"-Regel. |
| **D24** | `lookback_too_short = true`, wenn `lookback < max(2 * usage_refresh_minutes, 5 min)` | Zwei Polls sind das Minimum für eine Steigung; bei 10 min Refresh also < 20 min. Die 5-min-Untergrenze schützt bei sehr kurzen Refresh-Intervallen. |

---

## 3. Architektur

### 3.1 Modulschnitt

| Modul | Verantwortung |
|-------|---------------|
| `burn_lookback.py` (**neu**) | HA-freie Logik: Lookback-Normalisierung/Defaults, Sample-Retention-Prune, OLS-Steigung, Forecast-Methodenwahl, Kalendermonat-Berechnung, `lookback_too_short`-Prädikat. Kein `homeassistant`-Import → direkt per `pytest` testbar. |
| `const.py` | Option-Keys, Defaults, Ranges, Schrittweiten, Retention-Cap, `WINDOW_SECONDS` bleibt (nominal) als Fallback. |
| `coordinator.py` | Optionen parsen (`burn_lookback_minutes: dict[str, int]`), `set_burn_lookback()`, Sample-Aufzeichnung mit `resets_at`, `burn_rate()`, `forecast_details()`. Die bestehenden HA-freien Kernel-Funktionen (`window_elapsed_fraction`, `forecast_percent`, `pace_status`, `burn_rate_per_hour`) bleiben für Rückwärtskompatibilität erhalten. |
| `sensor.py` | Burn-Rate-Sensor nutzt `coordinator.burn_rate()` + Attribute; Forecast/Pace nutzen `coordinator.forecast_details()` + Attribute. Ampel weiterhin `pace_status()` auf Basis des Ø-Pace. |
| `number.py` | Drei neue Slider (`EntityCategory.CONFIG`, `NumberMode.SLIDER`), Setter → Coordinator + `persist_options` + `async_update_listeners()`. |
| `button.py` | Reset-Button (`EntityCategory.DIAGNOSTIC`, `mdi:restore`). |
| `config_flow.py` | Options-Flow um drei validierte Zahlenfelder + `pace_red_percent` erweitert. |
| `diagnostics.py` | Debug-Block pro Workspace. |

### 3.2 Datenfluss

```
API-Poll (usage)
  └─ _async_update_data()
       ├─ je Workspace/Fenster: percent, resets_at
       └─ _record_usage_sample(key, win, percent, now, resets_at)
            ├─ Reset-Erkennung: percent < last.percent  ODER  resets_at != last.resets_at
            │     → samples.clear()
            ├─ append((now, percent, resets_at))
            └─ prune: cutoff = now - retention_max(window)     [5h 18000 s, week/month 604800 s]
                 cap  = 10080 Samples je Fenster (neueste bleiben)

Burn-Rate-Sensor
  └─ coordinator.burn_rate(key, win)
       ├─ lookback = burn_lookback_minutes[win]  (Default 120/720/2880)
       ├─ Samples im Lookback-Fenster
       ├─ OLS (mit Achsenabschnitt) → %/h ; negative Steigung → None
       ├─ lookback_too_short = lookback < max(2 * usage_refresh_minutes, 5 min)
       └─ Attribute: lookback_minutes, samples, lookback_too_short

Forecast / Pace
  └─ coordinator.forecast_details(key, win)
       ├─ resets_at fehlt/ungültig → (None, method="unavailable")
       ├─ pct == 0                → 0.0
       ├─ T  = actual_window_seconds(win, resets_at)   [month = Kalendermonat]
       ├─ t  = T - (resets_at - now)                   [geklemmt auf 0..T]
       ├─ t_min = max(900 s, 1 % von T)
       ├─ t < t_min → forecast = pct + burn_rate * remaining_h , method="recent"
       └─ sonst     → forecast = pct * T / t                   , method="pace"
       └─ Attribute: forecast_recent, forecast_method, elapsed_fraction, window_seconds
```

### 3.3 Kalendermonat

`actual_window_seconds("month", resets_at)`:

1. `period_end = resets_at` (Zeitzone wie geliefert, sonst UTC).
2. `period_start` = `period_end` minus **einen Kalendermonat**, Tag auf die Länge des
   Zielmonats geklemmt (`calendar.monthrange`), z. B. 31.03. → 28./29.02.
3. `T = (period_end - period_start).total_seconds()` → 28/29/30/31 Tage.
4. Fallback auf `WINDOW_SECONDS["month"]` (30 d), wenn `resets_at` unbrauchbar ist —
   dieser Fall wird aber von D23 vorher abgefangen.

Damit ergibt ein 31-Tage-Monat einen Forecast von ~256,8 % statt `unknown`
(Beispielrechnung: `pct` und `t` aus dem 31-Tage-Fenster, `pct * T / t`).

`week` bleibt 604800 s, `5h` bleibt 18000 s. `WINDOW_SECONDS` in `const.py` bleibt als
nominaler Fallback erhalten, wird aber für `month` nicht mehr für den Forecast benutzt.

### 3.4 Sample-Repräsentation

`_usage_samples: dict[str, list[tuple[datetime, float, datetime | None]]]`
Schlüssel unverändert `f"{workspace_key}:{win}"`. Das dritte Tupel-Element ist das
`resets_at` des Samples (für D10). Bestehende Tests, die 2-Tupel übergeben, müssen
weiter funktionieren — die neuen Leser tolerieren 2- und 3-Tupel.

---

## 4. Datei- und Testplan

### 4.1 Parent-Repo (`ha-go-gauge`)

| Datei | Änderung |
|-------|----------|
| `docs/PLAN-burn-lookback-2.1.0.md` | **neu** (dieses Dokument) |
| `custom_components/go_gauge/const.py` | Option-Keys, Defaults, Ranges, Steps, Retention-Cap |
| `custom_components/go_gauge/burn_lookback.py` | **neu** (HA-freie Logik) |
| `custom_components/go_gauge/coordinator.py` | Option-Parsing, Retention, Reset-Erkennung, OLS-Anbindung, Forecast-Details |
| `custom_components/go_gauge/sensor.py` | Attribute + neue Berechnungsquelle |
| `custom_components/go_gauge/number.py` | 3 Slider |
| `custom_components/go_gauge/button.py` | Reset-Button |
| `custom_components/go_gauge/config_flow.py` | 3 Felder + Validierung |
| `custom_components/go_gauge/diagnostics.py` | Debug-Block |
| `custom_components/go_gauge/strings.json` | Master-Keys |
| `custom_components/go_gauge/translations/{en,de}.json` | abgeleitete Keys |
| `custom_components/go_gauge/manifest.json` | `2.1.0` |
| `CHANGELOG.md` | Abschnitt 2.1.0 |
| `README.md`, `README.de.md` | Feature-/Options-Doku |
| `tests/` | siehe 4.2 |
| `external/ha-gauge-standard` (Pointer) | Bump auf Phase-1-, später Phase-2-Commit |

### 4.2 Tests

| Testdatei | Inhalt |
|-----------|--------|
| `tests/test_burn_lookback.py` | **neu**: Defaults/Ranges/Clamp, Retention (Cutoff je Fenster, Cap 10080), OLS (perfekte Gerade, Rauschen, <2 Samples, zu kurze Spanne, negative Steigung → `None`), `lookback_too_short`, Kalendermonat (28/29/30/31 Tage), Forecast-Methodenwahl (`pace`/`recent`/`unavailable`/`pct==0`), `t_min`-Grenze |
| `tests/test_coordinator_burn_lookback.py` | **neu**: Option-Parsing, `set_burn_lookback`, Retention greift bei erhöhtem Lookback sofort, Reset bei Drop **und** bei `resets_at`-Wechsel, 2-/3-Tupel-Kompatibilität |
| `tests/test_forecast_calendar.py` | **neu**: 31-Tage-Monat ≈ 256,8 %, `t_min`-Fallback, `pct == 0`, `resetsAt` fehlt → `None` + `unavailable`, `forecast_recent` |
| `tests/test_burn_lookback_entities.py` | **neu**: 3 Slider (Kategorie CONFIG, Modus SLIDER, Steps, Ranges, Unique-IDs), Reset-Button (DIAGNOSTIC, Unique-ID, setzt nur die drei Werte, ruft `async_update_listeners`) |
| `tests/test_config_flow.py` | Options-Flow: 3 neue Felder, Range-Validierung (min/max verletzt → Fehler), `pace_red_percent` vorhanden |
| `tests/test_diagnostics.py` | Debug-Block: `burn_lookback_minutes`, Sample-Counts, effektive Lookback-Sekunden, `forecast_method`, `elapsed_fraction`; keine Tokens |
| `tests/test_translations.py` | **neu**: Key-Parität `strings.json` ↔ `translations/en.json` ↔ `translations/de.json` inkl. neuer Keys |
| `tests/test_derived_usage.py`, `tests/test_forecast_pace.py` | unverändert grün (Kernel-Rückwärtskompatibilität); ggf. um 3-Tupel-Fälle ergänzt |
| `tests/test_ha_compat.py`, `tests/test_repo_hygiene.py` | Version 2.1.0, Manifest/CHANGELOG-Konsistenz |

**Definition of Done:** `pytest -q` grün, `check_canon.py` 0 Fehler / 0 Warnungen
(nach Phase 2), keine Regression in den 28 Bestandstestdateien.

---

## 5. Canon-2-Phasen-Strategie

Grund für zwei Phasen: `check_canon.py` ist ein CI-Gate im Parent-Repo. Würde man
neue Pflicht-Entities im Canon aktivieren, bevor der Code sie liefert, wäre CI rot —
und umgekehrt. Also erst **reservieren**, dann **aktivieren**.

### Phase 1 — `reserve` (Branch `feat/canon-v1.7-burn-lookback`)

- `canon_version` bleibt **1.6.0** (kein Breaking Change der Canon-Version).
- Neue Keys als `domain_extensions.go_gauge` (reserviert, nicht Pflicht):
  `burn_lookback_5h`, `burn_lookback_week`, `burn_lookback_month` (number),
  `reset_burn_lookbacks` (button).
- `settings_ranges` um die drei Lookbacks erweitern (unit `min`, min/max/default/step,
  `mode: slider`, `entity_category: config`).
- Canon-Doku: Burn-Lookback als **geplant** kennzeichnen; Monatsfenster =
  Kalendermonat aus `resetsAt` dokumentieren.
- `matrix/parity.md`: Status **„geplant"**.
- Checks: `pre-commit`, `check_docs.py`, `check_canon.py` grün.
- Commit + Push + PR (falls Remote-Auth verfügbar).

### Phase 2 — `activate` (Branch `feat/canon-v1.7-burn-lookback-activate`)

- Die vier Keys von `domain_extensions` nach **core `entities`** verschieben.
- `canon_version` → **1.7.0**, Master `go_gauge` → **2.1.0**.
- Canon-Doku §§ 2/3/8/9 aktualisieren (Fenster, Sensor-Satz, Settings-Numbers/Buttons,
  Namensschema).
- `settings_ranges` um `mode`, `step`, `default_value` (bzw. `default`) ergänzen.
- `scripts/check_canon.py` härten: `mode`, `step`, `default_value`,
  `entity_category` in Schema **und** Checker; bestehende Numbers als `BOX`/step 1
  nachziehen.
- `matrix/parity.md` + README aktualisieren.
- Deviations für `command_gauge`: **⚠️ CG-N5** (drei Lookback-Numbers) und **CG-B6**
  (Reset-Button), je mit Schließungsbedingung („entfällt, sobald command_gauge die
  Entities in 2.x liefert").
- Checks grün; Commit + Push + PR.

---

## 6. Release-Plan

1. Parent-Branch `feat/burn-lookback-settings` mit Plan, Implementierung, Tests,
   Submodul-Pointer auf Phase-1-, danach Phase-2-Commit.
2. `manifest.json` → `2.1.0`, `CHANGELOG.md`-Abschnitt `[2.1.0]`, `README.md` +
   `README.de.md` aktualisiert; Release-Notes-Entwurf (Summary, ✨ New features,
   💥 Breaking changes = keine, Full-Changelog-Link).
3. **Kein** Merge, **kein** Tag, **kein** GitHub-Release ohne erneute explizite
   User-Bestätigung.
4. Nach Freigabe (separater Schritt, nicht Teil dieses Plans): Merge → Tag `v2.1.0`
   (Tag ↔ `manifest.version` synchron) → echtes GitHub Release.

---

## 7. Risiken und Gegenmaßnahmen

| Risiko | Wirkung | Gegenmaßnahme |
|--------|---------|---------------|
| `check_canon.py` ist ein Regex-Parser, kein Import | Neue Konstrukte (Helper-Funktionen in `_attr_unique_id`, dynamische Ranges) werden nicht erkannt → falsches Grün oder falsches Rot | Unique-IDs und Ranges als Literale/Templates schreiben; `_attr_unique_id = f"{entry.entry_id}_burn_lookback_5h_minutes"` direkt in der Klasse |
| Retention-Bedarf (7 Tage) × 3 Fenster × n Workspaces | Speicherwachstum im Coordinator | Cap 10080 Samples je Fenster; bei 10-min-Polling sind 7 Tage ≈ 1008 Samples, der Cap greift nur bei sehr kurzen Intervallen |
| Erhöhen des Lookbacks „wirkt sofort" | Samples, die vorher weggepruned wurden, fehlen dauerhaft | Retention wird **immer** am Maximum (nicht am aktuellen Lookback) berechnet — einmalig umgestellt, danach wirkungslos |
| `resets_at`-Wechsel durch API-Jitter (gleiche Periode, anderer Sekundenwert) | Falsche Resets, Burn-Rate dauerhaft `None` | Vergleich auf **Änderung des Werts**; API liefert stabile ISO-Zeitstempel. Bei Bedarf Toleranz im Test dokumentieren, nicht stillschweigend einbauen |
| Kalendermonat bei `resets_at` weit in der Zukunft/nahe Monatsende | Falsche Fensterlänge | Nur die Spanne `period_end - 1 Monat` verwenden, Tag klemmen; Fallback auf 30 d, wenn die Rechnung < 20 d oder > 40 d ergibt |
| Options-Flow und Slider schreiben dieselben Options | Divergenz (Slider zeigt alten Wert) | Beide Wege rufen `persist_options` und aktualisieren den Coordinator-Zustand; Slider zusätzlich `async_update_listeners()` |
| Submodul-Pointer versehentlich im Parent gestaged | Verletzung der Submodul-Regel | Nur der `git`-Agent setzt den Pointer bewusst (`git add external/ha-gauge-standard`), niemals automatisch |
| Zwei Phasen im Submodul, Parent zeigt zwischenzeitlich auf Phase 1 | CI im Parent prüft gegen Phase-1-Schema | Genau dafür ist Phase 1 da: reservierte Keys machen den Zwischenstand grün |

---

## 8. Invarianten (nicht verhandelbar)

- Keine bestehende `unique_id` wird geändert; keine bestehende Entity umbenannt.
- `GoGaugeConfigFlow.VERSION` bleibt **8**; `async_migrate_entry` bleibt unverändert.
- Diagnostics enthalten niemals Tokens, Token-Fingerprints bleiben erlaubt.
- Neue HA-Imports nur, wenn `tests/conftest.py` die Fake-Modulliste mitpflegt.
- Keine Code-Änderung auf `main`/`master`; alles auf Feature-Branches.
- Interne Doku Deutsch, Code/Commits Englisch, erste Commit-Zeile ≤ 72 Zeichen.
