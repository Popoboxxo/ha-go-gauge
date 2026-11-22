# Plan — API-Endpoint-Entscheidung, config_flow-Dedup, account.py-Split

**Status:** ✅ Archiviert — vollständig umgesetzt und auf `main` gemergt (2026-11-22).
Alle drei Stufen abgeschlossen:
- (a) PR [#49](https://github.com/Popoboxxo/ha-go-gauge/pull/49) — `api.py` migriert auf `/inference/go/v1/...` (Option B)
- (b) PR [#51](https://github.com/Popoboxxo/ha-go-gauge/pull/51) — `config_flow.py`-Dedup (gestapelt auf #49, dort mitgemergt)
- (c) PR [#50](https://github.com/Popoboxxo/ha-go-gauge/pull/50) — `account.py`-Split (2349 → 636 Zeilen + 3 neue Module)

Merge-Reihenfolge auf main: #51 → #49 → #50. Alle CI-Checks grün (lint/tests/mypy/
canon-check/json-consistency/validate-hacs/validate-hassfest), Canon-Checker
(`check_canon.py`/`check_repo_hygiene.py`) durchgehend 0/0. Dieses Dokument bleibt
als Entscheidungs- und Umsetzungsprotokoll erhalten (siehe `docs/PLAN-account-entry-2.0.0.md`/
`docs/PLAN-burn-lookback-2.1.0.md` für die gleiche Konvention).
**Scope:** `custom_components/go_gauge/{api.py,config_flow.py,account.py}` — drei
unabhängig freigebbare Stufen, (b) hängt von der Entscheidung in (a) ab, (c) ist
unabhängig von (a)/(b).
**Betroffene Versionen:** `go_gauge` 2.1.1 (aktuell) → Patch/Chore, kein erzwungener
Minor-Bump (keine Entity-/Verhaltensänderung außer dem in (b) benannten
Header-Bugfix)
**Branches:** je Stufe eigener Branch (siehe 2.4 / 3.4 / 4.6) — **kein** gemeinsamer
Sammel-Branch, damit jede Stufe einzeln gemerged/verworfen werden kann.

---

## 1. Abhängigkeitsgraph

```
(a) Endpoint-Entscheidung  ──▶  (b) config_flow.py Dedup
         (unabhängig)       ╲
                              ╲
(c) account.py Modul-Split  (unabhängig von a UND b, eigener Branch)
```

(b) kann erst final beschrieben/umgesetzt werden, wenn (a) entschieden ist — die
Diff-Beschreibung in Abschnitt 3 liegt deshalb in zwei Varianten vor. (c) hat keine
technische Abhängigkeit zu (a)/(b) und kann parallel oder in beliebiger Reihenfolge
laufen.

---

## 2. Entscheidungsgate (a): `/zen/` behalten oder auf `/inference/` migrieren?

### 2.1 Live-Befund (verifiziert 2026-11-22 07:58 UTC, Edge FRA, 7 curl-Varianten)

| Request | Ergebnis |
|---|---|
| `GET /zen/go/v1/models` mit Browser-UA+Headers | HTTP 200, Body 42361 Bytes |
| `GET /zen/go/v1/models` ohne jeden Custom-Header | HTTP 200, identischer Body |
| `GET /inference/go/v1/models` mit Browser-UA+Headers | HTTP 200, identischer Body |
| `GET /inference/go/v1/models` ohne Header | HTTP 200, identischer Body |
| `GET /inference/go/v1/models` mit eigenem UA (`ha-go-gauge/2.1.1 (+https://github.com/Popoboxxo/ha-go-gauge)`, laut Doku empfohlen) | HTTP 200, identischer Body |
| `GET /zen/go/v1/usage` ohne Token, Browser-UA | HTTP 401 `AuthError` |
| `GET /inference/go/v1/usage` ohne Token, Browser-UA | HTTP 401, identischer Error-Body |

Kein 403/503, kein CF-Challenge-Body bei irgendeiner Variante. `server: cloudflare`
+ `cf-ray` sind normaler Proxy-Betrieb, kein Blocking-Indikator.

**Caveat (wichtig, nicht relativierbar):** das ist eine Momentaufnahme EINES
Zeitpunkts von EINEM Edge-Standort. Der bestehende Kommentar in `api.py:6-7`
("Cloudflare note: plain urllib/requests get blocked (HTTP 403 Error 1010); aiohttp
WITH full browser headers succeeds (verified live 2026-08-22)") beruhte vermutlich
auf einem damaligen CF-Block bei Non-Browser-UA, der jetzt nicht reproduzierbar war.
Das heißt NICHT, dass der Block komplett verschwunden ist — andere IP-Reputation,
Region oder Zeitpunkt könnten abweichen.

### 2.1b Zusatz-Verifikation — Burst-Test (User-Auflage vor Entscheidung B)

Auf User-Wunsch vor der finalen Entscheidung durchgeführt: 25× `GET
/inference/go/v1/models`, eigener distinktiver UA (`ha-go-gauge/2.1.1
(+https://github.com/Popoboxxo/ha-go-gauge)`), **kein** Browser-Header-Spoof,
~1.2s Abstand zwischen den Requests.

**Ergebnis: 25/25 HTTP 200, identische Body-Größe (42361 Bytes), keine
Cloudflare-Challenge erkennbar.** Deckt nur den unauthentifizierten `models`-Pfad
ab (User hat den authentifizierten `usage`-Live-Test mit echtem Token bewusst
NICHT beauftragt — bleibt offenes Restrisiko, siehe 2.4b).

### 2.2 Aktueller Code-Stand (`api.py:17-25`)

```python
MODELS_URL = "https://opencode.ai/zen/go/v1/models"
USAGE_URL = "https://opencode.ai/zen/go/v1/usage"

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": USER_AGENT,          # Chrome-124-UA, const.py:399
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://opencode.ai",
}
```

### 2.3 Optionen (User entscheidet — keine der drei ist hier vorentschieden)

| Option | Beschreibung | Vorteil | Risiko |
|---|---|---|---|
| **A — Status quo behalten** | `/zen/` + Browser-UA-Spoof unverändert lassen | Battle-tested seit 2026-08-22, aktuell nachweislich funktionsfähig, null Diff-Risiko | `/zen/` ist undokumentiert — OpenCode kann den Pfad jederzeit ohne Deprecation-Hinweis abschalten; Spoofing bleibt dauerhaft ein adversariales Pattern gegen CF-Bot-Erkennung |
| **B — Migration auf `/inference/`** | URLs auf `/inference/go/v1/{models,usage}` ändern, `BROWSER_HEADERS`-UA durch den eigenen distinktiven UA (`ha-go-gauge/<version> (+repo-url)`, wie von der Doku empfohlen) ersetzen | Offiziell dokumentiert, zukunftssicherer, kein Spoofing mehr nötig | Aktuell NICHT belegt, ob der eigene UA langfristig/regional stabil gegen CF ist (nur eine Momentaufnahme); ein Fehlschlag wäre ein Breaking Change für alle Nutzer bis zum nächsten Patch-Release |
| **C — Konservative Zwischenoption** | `/zen/` bleibt primär, `/inference/` als Fallback bei Fehlschlag einbauen | Abdeckung beider Pfade | Höhere Komplexität (zwei URL-Sätze, Fallback-Logik, doppelte Testmatrix) für ein Problem, das aktuell nicht beobachtet wird — potenzielles Overengineering |

### 2.4 Empfehlung des Planners (ursprünglich A — inzwischen durch User-Entscheidung überholt)

Ursprüngliche Empfehlung war **Option A**, mit dem Vorbehalt: "Falls der User
Zukunftssicherheit höher gewichtet als 'keep it working', ist Option B vertretbar."
Genau das ist eingetreten.

### 2.4a ENTSCHEIDUNG (User, nach Zusatz-Burst-Test): Option B

**Status: entschieden.** User wählt **Option B — Migration auf `/inference/go/v1/...`
+ eigener distinktiver User-Agent**, nach zusätzlichem Burst-Test (2.1b, 25/25 HTTP 200,
kein CF-Block). Umsetzung von (a)/(b) folgt dem B-Pfad in Abschnitt 2.5/3.3.

### 2.4b Verbleibendes Restrisiko (akzeptiert, nicht weiter getestet)

Der authentifizierte `/usage`-Endpoint mit echtem Token wurde NICHT live getestet
(User hat das bei der Test-Scope-Festlegung explizit ausgeklammert, nur der
unauthentifizierte `models`-Burst wurde beauftragt). Annahme: dieselbe
Cloudflare-Schicht reagiert vor jedem Auth-Check identisch — nicht belegt, nur
plausibel. Falls nach der Migration doch ein 403/Block am `usage`-Endpoint auftritt,
siehe Rollback-Hinweis in 2.5.

### 2.5 Branch / Commit — gewählter Ausgang B

**Branch:** `refactor/api-inference-endpoint-migration`
**Commit:** `refactor(api): migrate to documented /inference/ endpoint`
**Diff:** `MODELS_URL`/`USAGE_URL` in `api.py` von `/zen/go/v1/...` auf
`/inference/go/v1/...` ändern; `BROWSER_HEADERS["User-Agent"]` vom
Chrome-Spoof (`const.py::USER_AGENT`) auf einen eigenen distinktiven UA-String
(`ha-go-gauge/<version> (+https://github.com/Popoboxxo/ha-go-gauge)`, wie im
Burst-Test 2.1b verifiziert) umstellen; `const.py` um die neue UA-Konstante
ergänzen (alter `USER_AGENT`-Chrome-String kann entfernt werden, wenn er sonst
nirgends mehr referenziert wird — vor dem Entfernen grep prüfen); Code-Kommentar
in `api.py:1-8` aktualisieren (altes "verified live 2026-08-22"/Cloudflare-Spoof-
Kommentar durch Verweis auf diesen Plan + Burst-Test-Datum ersetzen).
**Rollback-Hinweis (wegen Restrisiko 2.4b):** sollte der echte `usage`-Call nach
dem Merge doch 403/blockiert werden, ist der Revert ein reiner Konstanten-Revert
(ein Commit zurück) — kein struktureller Rollback nötig, da (a) isoliert auf
`api.py`/`const.py` beschränkt bleibt.

**Verworfene Alternativen (nicht mehr relevant, nur zur Nachvollziehbarkeit
archiviert):**
- A (Status quo `/zen/` behalten) — war Planner-Empfehlung vor dem Burst-Test.
- C (Fallback-Logik `/zen/` + `/inference/`) — vom Planner nicht empfohlen, vom
  User nicht gewählt.

### 2.6 Verifikation (a)

1. `pytest -q` grün (insb. `tests/test_api_status_and_subscription.py`, falls dort
   URLs/Header literal gepinnt sind — vor dem Diff prüfen).
2. `python3 external/ha-gauge-standard/scripts/check_canon.py /home/hermes/repos/ha-go-gauge`
   → erwartet weiterhin **0 Errors / 0 Warnings** (Canon regelt keine HTTP-Client-Interna,
   siehe Abschnitt 5).
3. `python3 external/ha-gauge-standard/scripts/check_repo_hygiene.py /home/hermes/repos/ha-go-gauge`
   → erwartet weiterhin **0/0**.
4. Zusätzlich (Pflicht bei Option B): manueller Live-Smoke-Test gegen `/inference/`
   vor dem Merge (curl, wie in 2.1/2.1b) — kein reiner Unit-Test-Vertrauensbeweis für
   einen externen Cloudflare-Pfad. Burst-Test (2.1b) deckt das für `models`
   bereits ab; vor dem Merge zusätzlich den authentifizierten `usage`-Call einmal
   gegen `/inference/` prüfen, falls bis dahin ein echter Token verfügbar ist
   (siehe Restrisiko 2.4b).

---

## 3. Stufe (b): `config_flow.py` Dedup (abhängig von (a))

### 3.1 Befund

- `config_flow.py:44` redeklariert `USAGE_URL = "https://opencode.ai/zen/go/v1/usage"`
  — identischer String wie `api.py:18`, aber eigenständig dupliziert statt
  importiert.
- `config_flow.py:51-58`, Funktion `_probe_token`: Header-Dict wird händisch
  nachgebaut — **fehlt `Accept-Language`** gegenüber `api.py`s `BROWSER_HEADERS`.
  Das ist keine reine Dopplung, sondern eine stillschweigende Inkonsistenz.
- Umfang: ~10 Zeilen, Zielzustand ist ein Import statt einer Neudeklaration.

### 3.2 Diff-Beschreibung (gültig für den entschiedenen Ausgang (a) = Option B)

Da (a)/Option B die Konstantennamen `MODELS_URL`/`USAGE_URL`/`BROWSER_HEADERS`
unverändert lässt (siehe 2.5 — nur Werte/UA ändern sich, keine Umbenennung),
ist dieser Diff direkt gültig, keine Anpassung nötig gegenüber dem ursprünglich
als "Variante A" bezeichneten Mechanismus. Der Implementierende prüft trotzdem
vor dem Schreiben den tatsächlichen Stand von `api.py` NACH Merge von (a), statt
diesen Plan blind zu übernehmen (Vorbedingung bleibt: (a) muss gemerged sein,
bevor (b) startet).

```python
# config_flow.py — vorher (Zeilen 24-44)
from .const import (
    ...
    USER_AGENT,
    conf_entry_kind,
    token_unique_id,
)

USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


async def _probe_token(hass: HomeAssistant, token: str) -> tuple[bool, str | None]:
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            USAGE_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Origin": "https://opencode.ai",
                "Authorization": f"Bearer {token}",
            },
            ...
```

```python
# config_flow.py — nachher
from .api import BROWSER_HEADERS, USAGE_URL
from .const import (
    ...
    conf_entry_kind,
    token_unique_id,
)
# USER_AGENT-Import aus .const entfällt hier (kommt jetzt über BROWSER_HEADERS)
# lokale USAGE_URL-Deklaration entfällt komplett


async def _probe_token(hass: HomeAssistant, token: str) -> tuple[bool, str | None]:
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            USAGE_URL,
            headers={**BROWSER_HEADERS, "Authorization": f"Bearer {token}"},
            ...
```

**Bugfix-Nebeneffekt (explizit benannt, kein stillschweigender Verhaltenswechsel):**
`_probe_token` sendet danach zusätzlich `Accept-Language: en-US,en;q=0.9` — das
Fehlen war eine Inkonsistenz zu `api.py`, kein beabsichtigtes Verhalten; im
Commit-Body und CHANGELOG als Fix benennen, nicht nur als Refactor.

### 3.3 (entfällt — in 3.2 konsolidiert, da (a) entschieden ist und die Namen stabil bleiben)

### 3.4 Branch / Commit / Verifikation (b)

- **Branch:** `refactor/config-flow-header-dedup`
- **Commit:** `refactor(config_flow): dedupe usage url/headers from api.py`
  (zweite Zeile im Body: `Also fixes missing Accept-Language header in token probe.`)
- **Verifikation:**
  1. `pytest -q tests/test_config_flow.py tests/test_reauth_flow.py` grün (beide
     Dateien nutzen `_probe_token`/`USAGE_URL` manuell geladen — nach dem Import-Wechsel
     prüfen, ob ihre `_load`-Helfer `go_gauge.api` vor `go_gauge.config_flow`
     vorladen, sonst schlägt der relative Import fehl, siehe Mechanik in Abschnitt 4.5).
  2. `pytest -q` (volle Suite) grün.
  3. `python3 external/ha-gauge-standard/scripts/check_canon.py /home/hermes/repos/ha-go-gauge`
     → erwartet weiterhin **0/0**.
  4. `python3 external/ha-gauge-standard/scripts/check_repo_hygiene.py /home/hermes/repos/ha-go-gauge`
     → erwartet weiterhin **0/0**.

---

## 4. Stufe (c): `account.py` Modul-Split (unabhängig von a/b)

### 4.1 Korrektur der Ausgangsannahme — Reachability ist NICHT vollständig extrahiert

Die Aufgabenstellung nahm an, die Reachability-Snapshot-Logik liege bereits
komplett in `account_logic.py` und der "Split"-Wunsch dafür sei obsolet. Das ist
**nur zur Hälfte richtig**: `account_logic.py` liefert nur die **Typen**
(`Evidence`, `Reachability`, per `TYPE_CHECKING`-Import in `account.py:106-107`
lazy geladen). Die tatsächliche Snapshot-**Orchestrierung** liegt weiterhin direkt
in `account.py` (frisch verifiziert, siehe 4.2, Zeilen 533-774):

- `workspace_coordinators` (533)
- `collect_evidence` (556)
- `class ReachabilitySnapshot` (602)
- `account_reachability_snapshot` (648)
- `account_reachability` (714)
- drei kleine Helfer: `_account_entries` (735), `_entry_is_loaded` (743),
  `_has_enabled_workspace` (759)

Auffällig: diese fünf öffentlichen Symbole (`ReachabilitySnapshot`,
`account_reachability_snapshot`, `account_reachability` eingeschlossen) stehen
**nicht einmal im Modul-Docstring-Bullet-Katalog** (`account.py:1-32`) — eine
bestehende Doku-Lücke, unabhängig vom Split.

### 4.2 Korrektur der Ausgangsannahme — die gegebenen Zeilennummern für "Block 2" sind veraltet

Die Aufgabenstellung nannte für den Adoption-Sweep-Block die Zeilen 556-774 mit
`AdoptionReport`@606, `_adoption_issue_ids`@643, `_adoption_repair_ids`@674,
`async_remove_legacy_model_settings`@706. Frisches `grep ^(class |def |async def )`
gegen den aktuellen Stand (2349 Zeilen, unverändert zur Aufgabenstellung) zeigt:

- `_adoption_repair_ids` **existiert nicht** im aktuellen `account.py`.
- `AdoptionReport` liegt tatsächlich bei **956**, nicht 606.
- `_adoption_issue_ids` liegt tatsächlich bei **919**, nicht 643.
- `async_remove_legacy_model_settings` liegt tatsächlich bei **1740**, nicht 706.
- Der tatsächliche Adoption-Sweep-Block ist **nicht** 556-774 (≈220 Zeilen), sondern
  reicht von **899 bis mindestens 1967** (≈1070 Zeilen) — mehr als die Hälfte der
  Datei, nicht ein Fünftel.

Die Linennummern in der Aufgabenstellung sind also **stale** — dieser Plan baut
auf der frisch verifizierten Struktur (Tabelle 4.3) auf, nicht auf den gegebenen
Zahlen.

### 4.3 Verifizierte Zielstruktur (3-Wege-Split statt 2-Wege, passend zum Docstring: "coordinator, lifecycle, adoption, reachability")

| Neues Modul | Symbole (Quelle: `account.py`, aktueller Stand) | Verantwortung |
|---|---|---|
| `account_coordinator.py` | `_clamp_minutes` (133), `_isoformat` (142), `_workspace_options` (147), `_seed_account_options` (166), `class AccountCoordinator` (222–532), **`_domain_data` (899, hierher verschoben — s. Klärung unten)** | Workspace-unabhängiger Catalog/Publish-Runtime + gemeinsamer `hass.data[DOMAIN]`-Zugriff für alle drei Blätter |
| `account_reachability.py` **(neu, nicht im Original-Taskbeschrieb)** | `workspace_coordinators` (533), `collect_evidence` (556), `class ReachabilitySnapshot` (602), `account_reachability_snapshot` (648), `account_reachability` (714), `_account_entries` (735), `_entry_is_loaded` (743), `_has_enabled_workspace` (759) | Evidence-Sammlung + Reachability-Matrix-Snapshot (siehe 4.1-Korrektur); importiert `_domain_data` aus `account_coordinator.py` |
| `account_adoption.py` | `_adoption_issue_ids` (919), `_delete_adoption_issues` (936), `class AdoptionReport` (956), `_user_disabler` (991), `_warning_severity` (1004), `_create_adoption_issue` (1009), `_delete_adoption_issue` (1039), `_fresh_entry` (1049), `_note_moved` (1064), `_canonical_blocker` (1070), `_adopt_winner` (1112), `_adopt_loser` (1146), `_sweep_entities` (1180), `_device_entries` (1331), `_canonical_device` (1362), `_workspace_device_identifiers` (1411), `_device_identifiers` (1438), `_is_workspace_device` (1452), `_go_gauge_entry_ids` (1457), `_is_go_gauge_owned` (1489), `_device_has_dependencies` (1504), `_merge_duplicate_devices` (1524), `_device_exists` (1601), `_move_account_device` (1616), `async_remove_legacy_model_settings` (1740), `_adopt_registries` (1805), `async_adopt_account_entities` (1886) | Registry-Sweep, Collision/Loser-Pfad, Device-Merge, Legacy-Settings-Cleanup; importiert `_domain_data` aus `account_coordinator.py` |
| `account.py` (bleibt) | `async_ensure_account_entry` (775), `_async_shutdown_coordinator` (1968), `_async_teardown_account_runtime` (1997), `async_setup_account_entry` (2041), `async_unload_account_entry` (2213), `async_remove_account_entry` (2295) | Entry-Lifecycle-Orchestrierung (Setup/Unload/Remove), importiert die drei neuen Module und re-exportiert ihre öffentlichen Symbole, importiert `_domain_data` aus `account_coordinator.py` |

**Beide Platzierungsfragen geklärt (Caller-Grep ausgeführt, nicht nur vermutet):**

1. `_domain_data` (899) — **nicht** `account_adoption.py` (Plan-Tabellen-Platzierung
   oben war falsch). Tatsächliche Aufrufer: `collect_evidence`@575 und
   `account_reachability_snapshot`@676 (Reachability, 2×), `async_adopt_account_entities`@1924
   (Adoption, 1×), sowie `_async_teardown_account_runtime`@2025,
   `async_setup_account_entry`@2093, `async_unload_account_entry`@2260,
   `async_remove_account_entry`@2316 (Lifecycle, 4×) — wird von allen drei
   Ziel-Modulen gebraucht. Da `account.py` nach dem Split `account_reachability.py`
   UND `account_adoption.py` importieren muss (Re-Export-Pflicht unten), würde eine
   Platzierung in einem der beiden Blätter einen Zirkel-Import erzeugen, sobald das
   jeweils andere Blatt es auch braucht. **Korrekte Platzierung: `account_coordinator.py`**
   — das einzige der drei neuen Module ohne Abhängigkeit auf die anderen beiden, also
   der einzige zirkelfreie gemeinsame Nenner. `account_reachability.py` und
   `account_adoption.py` importieren es von dort.
2. `_async_shutdown_coordinator` / `_async_teardown_account_runtime` (1968-2040) —
   Caller-Grep bestätigt die ursprüngliche Empfehlung zu 100%: ausschließlich aus
   `async_setup_account_entry`/`async_unload_account_entry`/`async_remove_account_entry`
   aufgerufen (Lifecycle), keine Aufrufe aus Reachability oder Adoption. **Bleiben
   in `account.py`.**

**Re-Export-Pflicht:** `account.py` muss nach dem Split
`AccountCoordinator`, `ReachabilitySnapshot`, `account_reachability_snapshot`,
`account_reachability`, `workspace_coordinators`, `collect_evidence`,
`AdoptionReport`, `async_adopt_account_entities`, `async_remove_legacy_model_settings`
weiterhin unter `from .account import ...` bereitstellen (einfache
Re-Export-Imports am Modulkopf), weil folgende Dateien genau das heute tun:
`__init__.py:24`, `binary_sensor.py:35,178,187`, `button.py:32`, `diagnostics.py:75`,
`entity.py:30`, `number.py:40`, `sensor.py:43`, `switch.py:37`. Kein einziger dieser
Caller wird in diesem Schritt umgeschrieben — das hält den Diff minimal und den
öffentlichen Import-Pfad stabil.

### 4.4 Reiner Move — kein Verhaltenswechsel

Alle vier Module behalten Funktions-/Klassensignaturen, Docstrings und
Fehlerbehandlung unverändert (siehe Ponytail-Regel: Code, der verschoben wird,
behält seine Error-Handling/Validierung). Der Modul-Docstring von `account.py`
wird um einen Verweis auf die drei neuen Module ergänzt und der
"KNOWN FOLLOW-UP"-Absatz (Zeilen 51-57) entfernt, da der Follow-up damit erledigt
ist.

### 4.5 Offene Recherche-Lücke (Vorbedingung, VOR dem ersten Code-Diff zu klären)

14 Testdateien referenzieren `account.py` über einen **pro Datei eigenen**
`_load(name, path)`-Helfer (`importlib.util.spec_from_file_location` +
manuelle `sys.modules`-Registrierung unter einem Dotted-Name wie
`go_gauge.account`) statt über einen echten Package-Import:
`test_ha_compat.py`, `test_diagnostics.py`, `conftest.py`,
`test_account_platforms.py`, `test_entity_name_migration.py`, `test_init.py`,
`test_reauth_flow.py`, `test_account_adoption.py`, `test_account_coordinator.py`,
`test_account_flow.py`, `test_account_lifecycle.py`,
`test_binary_sensor_availability.py`, `test_entity_id_migration.py`,
`manual_offline_smoke.py`.

**Mechanik (verifiziert an `test_account_coordinator.py:40-60`):** `account.py`s
heutige relative Imports (`from .api import ...`, `from .catalog import ...`,
`from .coordinator import ...`) funktionieren beim manuellen Laden nur, weil
jede Testdatei VORHER `const.py`, `api.py`, `catalog.py`, `coordinator.py` unter
den exakt passenden Dotted-Names (`go_gauge.const`, `go_gauge.api`, ...) in
`sys.modules` registriert, bevor sie `account.py` lädt — Pythons relativer Import
findet den Treffer dann direkt in `sys.modules`, ohne dass `go_gauge` selbst als
echtes Package existieren muss. Nach dem Split braucht **jede** der 14 Dateien
zusätzlich `_load("go_gauge.account_coordinator", ...)`,
`_load("go_gauge.account_reachability", ...)`, `_load("go_gauge.account_adoption", ...)`
**vor** `_load("go_gauge.account", ...)` in ihrer jeweiligen Ladereihenfolge — sonst
schlägt der relative Import in `account.py` mit `ModuleNotFoundError` fehl.

**Vorbedingung für den Implementierenden:** vor dem ersten Diff die Ladereihenfolge
in allen 14 Dateien einzeln auflisten (welche Module jede Datei bereits lädt, in
welcher Reihenfolge) und die drei neuen `_load`-Aufrufe an der richtigen Stelle
ergänzen — pauschal "wird schon passen" ist hier nicht verifiziert und laut
Modul-Docstring (Zeilen 51-57) genau der Grund, warum der Split bisher
verschoben wurde.

### 4.6 Branch / Commit / Verifikation (c)

- **Branch:** `refactor/account-module-split`
- **Commit(s):** `refactor(account): extract coordinator into account_coordinator.py`,
  `refactor(account): extract reachability snapshot into account_reachability.py`,
  `refactor(account): extract adoption sweep into account_adoption.py`
  (drei separate Commits auf demselben Branch, damit ein Bisect im Fehlerfall
  trennscharf bleibt — kein einzelner Mega-Commit für 2349 Zeilen).
- **Verifikation:**
  1. Nach JEDEM der drei Teil-Commits: `pytest -q` vollständig grün (nicht erst am
     Ende der Stufe) — die 14 betroffenen Testdateien zuerst, dann die volle Suite.
  2. `python3 external/ha-gauge-standard/scripts/check_canon.py /home/hermes/repos/ha-go-gauge`
     → erwartet weiterhin **0/0** (Canon regelt kein Python-Datei-Layout, siehe
     Abschnitt 5).
  3. `python3 external/ha-gauge-standard/scripts/check_repo_hygiene.py /home/hermes/repos/ha-go-gauge`
     → erwartet weiterhin **0/0**.
  4. Manueller Grep-Check nach dem letzten Commit: `grep -rn "from .account import" custom_components/` liefert exakt dieselben Symbol-Namen wie vor dem Split (Re-Export-Vertrag aus 4.3 eingehalten).

---

## 5. Canon-Checkpoint (für alle drei Stufen — aktuell kein Trigger, Verfahren für künftigen Fall)

**Befund (read-only geprüft):** `external/ha-gauge-standard/canon/repository.md`,
`canon/entities.md` und die zugehörigen `.schema.json`-Dateien regeln Entity-Schema/
-Semantik und Repo-Hygiene (README, Metadaten, CI-Gates) — **keine** Regel betrifft
HTTP-Client-URLs/Header/UA-Strings (a) oder Python-Datei-Layout/Modul-Split (c) oder
Dedup/DRY-Konventionen (b). Baseline: `check_canon.py` / `check_repo_hygiene.py`
liefern aktuell **0 Errors / 0 Warnings** gegen dieses Repo. **Verdict für alle drei
Stufen: keine Canon-Änderung notwendig.**

**Falls sich das bei der Implementierung doch ändert** (z. B. weil Option B/C in
(a) eine neue, Canon-relevante Deviation erzeugt, was aktuell nicht erwartet wird):

1. **Keine** direkte Änderung in `external/ha-gauge-standard/` aus diesem Repo
   heraus (Submodul-Schutz) — Änderungen gehören in ein separates PR direkt gegen
   `Popoboxxo/ha-gauge-standard`.
2. Erst dort mergen, **danach** in diesem Repo ein eigenständiges
   `chore: bump canon submodule to vX.Y.Z`-PR (Submodule-Pointer-Update), nie als
   Teil eines inhaltlichen Stufen-Commits.
3. **Cross-Repo-Flag für `ha-command-gauge`:** Canon hat eine dokumentierte
   "Paar-Change-Pflicht" — jede Canon-Änderung, die `command_gauge` betrifft, muss
   dort nachgezogen werden. `ha-command-gauge` ist in dieser Umgebung nicht lokal
   vorhanden; dieser Punkt wird hier nur als Flag für den User/Orchestrator notiert,
   nicht umgesetzt.

---

## 6. Offene Annahmen/Korrekturen gegenüber der ursprünglichen Aufgabenstellung

1. **Reachability (Block c):** Nur die Typen (`Evidence`, `Reachability`) sind
   nach `account_logic.py` extrahiert; die eigentliche Snapshot-Orchestrierung
   (`ReachabilitySnapshot`, `account_reachability_snapshot`, `account_reachability`,
   `collect_evidence`, `workspace_coordinators`, ≈240 Zeilen) liegt weiterhin in
   `account.py` und ist nicht obsolet — der Split-Wunsch bleibt sinnvoll, nur als
   drittes Modul statt als "schon erledigt" (siehe 4.1).
2. **Zeilennummern "Block 2" (Block c):** Die in der Aufgabenstellung genannten
   Zeilen für den Adoption-Sweep (556-774, `AdoptionReport`@606 etc.) sind stale;
   `_adoption_repair_ids` existiert im aktuellen `account.py` gar nicht. Der
   tatsächliche Adoption-Block ist ≈899-1967 und damit über 4× größer als
   angenommen (siehe 4.2). Dieser Plan verwendet ausschließlich die frisch per
   `grep` verifizierten Zeilennummern.
3. **Modul-Zuschnitt (Block c):** Aus (1)+(2) folgt ein 3-Wege- statt 2-Wege-Split
   (`account_coordinator.py` / `account_reachability.py` / `account_adoption.py`),
   passend zu den vier im Modul-Docstring selbst genannten Konzernen (coordinator,
   lifecycle, adoption, reachability) statt der ursprünglich vorgeschlagenen zwei
   neuen Dateien.
4. **Beide Platzierungsfragen geklärt** (per Caller-Grep, nicht Vermutung —
   Details in 4.3): `_domain_data` wandert nach `account_coordinator.py` (von
   allen drei Blättern gebraucht, einzige zirkelfreie Option — die
   Original-Planung "account_adoption.py" war falsch). `_async_shutdown_coordinator`/
   `_async_teardown_account_runtime` bleiben wie empfohlen in `account.py`
   (ausschließlich Lifecycle-Aufrufer, 0 Treffer aus Reachability/Adoption).
5. **Canon-Verdict (a/b/c):** vom vorab geprüften Befund übernommen (keine
   Canon-Rule betrifft HTTP-Client-Interna oder Python-Layout) — hier nicht erneut
   in Frage gestellt, nur der Re-Run in jeder Stufe als Verifikationsschritt
   vorgeschrieben (Abschnitt 5).

---

## 7. Zusammenfassung — Stufen-Übersicht

| Stufe | Branch | Commit-Typ/Scope | Abhängigkeit | Freigabe-Gate vor Umsetzung |
|---|---|---|---|---|
| (a) Endpoint-Entscheidung | `refactor/api-inference-endpoint-migration` | `refactor(api): migrate to documented /inference/ endpoint` | — | **Entschieden (Option B, siehe 2.4a) — bereit zur Umsetzung** |
| (b) config_flow Dedup | `refactor/config-flow-header-dedup` | `refactor(config_flow): dedupe usage url/headers from api.py` | (a) muss gemerged sein | Ja — Diff steht fest (3.2), wartet auf Merge von (a) |
| (c) account.py Split | `refactor/account-module-split` | 3× `refactor(account): extract ... into ...` | — (nur Recherche-Lücke 4.5 vorab klären) | Ja — Split-Zuschnitt (4.3) ist ein Vorschlag, kein Fakt |

**Effort-Schätzung:** siehe `effort-estimator`-Gesamtbewertung (Text-Referenz, vom
Planner nicht selbst berechnet) — grobe Planer-Einordnung: (a) ist primär eine
Entscheidung + Ein-Zeiler bzw. URL/Header-Tausch (klein), (b) ist ein ~10-Zeilen-Diff
(klein), (c) ist der eigentliche Aufwandstreiber (groß: 3 neue Dateien, 2 Platzierungs-
entscheidungen, 14 Testdateien mit je eigenem Loader-Fix, 3 Teil-Commits mit
Zwischen-pytest-Runs).
