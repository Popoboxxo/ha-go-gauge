# Implementierungsplan — Konto-Entry-Architektur (ha-go-gauge 2.0.0)

**Quelle:** reviewter Plan v1 (`CHANGES_REQUESTED`, 5 Blocker, 8 Major) → Plan v2 → Finalentscheidungen E1–E9
**Basis:** Read-only-Inspektion des Ist-Stands (v1.7.0, `ConfigEntry.VERSION = 7`)
**Zielversion:** 2.0.0 (Breaking); optional vorher 2.0.0b0 als Pre-Release
**Home-Assistant-Mindestversion:** 2026.8.0
**Sprache:** Deutsch; Code-/Entity-/Commit-Bezeichner englisch
**Status dieses Dokuments:** finale Fassung (ersetzt Plan v2)

---

## 1. Kurzfazit + Architekturentscheidung

Die Konto-Daten (Modell-Katalog + Erreichbarkeit) verlassen den Workspace-Coordinator und bekommen eine
**eigene, automatisch angelegte ConfigEntry** (`unique_id = "go_gauge_account"`, Titel `Go Gauge Konto`).
Sie ist einzige Besitzerin der 5 Konto-Entities, der beiden Konto-Einstellungen (Switch + Number), des
Konto-Devices `(DOMAIN, "account")` und eines eigenen Katalog-Refresh-Buttons.
Workspace-Entries bleiben reine Usage-Instanzen.

Die Konto-Einstellungen `auto_update_models` und `models_refresh_minutes` liegen **global auf der
Konto-Entry** (Options der Konto-Entry). Es gibt keine Ableitung aus Workspace-Werten: ein Workspace
besitzt diese Regler ab 2.0.0 nicht mehr.

Damit entfallen drei Problemklassen **strukturell** (nicht per Sonderfall): Owner-Wahl/Refcount
(B2/B3/M1), entry-lose Entities (B1) und die Katalog-Blockade der Workspace-Migration (B4). Die Adoption
der 5 Legacy-Entities und das Entfernen der alten Workspace-Modell-Settings-Entities laufen einmalig,
idempotent und reihenfolgeunabhängig im `async_setup_entry` der Konto-Entry **vor**
`async_forward_entry_setups`.

### 1.1 Delta gegenüber Plan v2 (was hier anders ist)

| Punkt | Plan v2 | Finale Fassung |
|---|---|---|
| E4 Konto-Intervall | aus Workspace-Werten abgeleitet (inkl. Zusatzattributen) | Globale Konto-Settings (Switch + Number auf der Konto-Entry), keine Ableitung, keine Zusatzattribute |
| Konto-Options-Flow | Abort (`no_options`), kein Options-Flow | Eigener Options-Flow ist **Pflicht**; der v2-Abort entfällt |
| Workspace-Modell-Settings | bleiben physisch im Workspace erhalten | werden aus der Entity-Registry **entfernt** (Breaking) |
| HA-Mindestversion | offene Annahme, `2026.8.0` provisorisch | **festgelegt** auf 2026.8.0 + Guard |
| Adoptionsreihenfolge | Device-Move **vor** Entity-Move | Entity-Move **vor** Device-Move (sonst löscht der Move Entities) |
| Konto-Fetch | `min(intervall, 5)` als Fetch-Regel | Publish-Tick 5 min **ohne HTTP**; Fetch über eigenen Timer im Basisintervall, Backoff bei 403/Netzfehler |
| Letzter Workspace weg | Konto-Fetch aus (`fetch_interval_minutes = None`) | Konto-Fetch läuft mit den eigenen Settings weiter (workspace-unabhängig) |

### 1.2 Findings → Lösung

| Finding | Lösung in 2.0.0 | Betroffene Stelle |
|---|---|---|
| **B1** Entity-Ownership (entry-lose Entities) | Alle Konto-Entities werden ausschließlich von der Konto-Entry über `async_forward_entry_setups(entry, ACCOUNT_PLATFORMS)` erzeugt → `config_entry_id` + Device sind per EntityPlatform garantiert. Kein Owner-Zweig mehr. | `__init__.py:async_setup_entry`, `sensor.py`, `binary_sensor.py`, `button.py`, `switch.py`, `number.py`, `entity.py:GoGaugeAccountEntityBase` |
| **B2** Handoff/Reload-Rekursion | Kein Handoff, kein `async_reload` im Unload, keine Owner-Übergabe. `_async_update_listener` wird **nur** für Workspace-Entries registriert; die Konto-Entry hat keinen Reload-Listener, ihre Setter wirken live. | `__init__.py:async_unload_entry`, `account.py:async_setup_account_entry` |
| **B3** Refcount/Concurrency | Kein Refcount. `ensure` und Adoption sind per `asyncio.Lock` in `hass.data[DOMAIN]["_adoption_lock"]` serialisiert; Erzeugung der Konto-Entry unter derselben Lock mit Re-Check. Jeder Schritt ist ein bedingtes „ist schon korrekt → skip". | `account.py:async_ensure_account_entry`, `async_adopt_account_entities` |
| **B4** Migrations-Dauerblockade | `async_migrate_entry` v8 = **nur** Version-Bump + `entry_kind`-Marker, keine Registry-/Netz-Arbeit → O(1), kann nicht in `MIGRATION_ERROR` laufen. Die gesamte Registry-Arbeit liegt danach in der Konto-Entry, pro Entity try/except. | `__init__.py:async_migrate_entry` |
| **B5** wirkungslose Models-Settings | Die Regler wandern auf die Konto-Entry und wirken dort direkt am `AccountCoordinator` (Fetch-Timer, Backoff) — inkl. Persistenz in `entry.options`. Kein Workspace-Regler ohne Wirkung mehr. | `switch.py:AccountAutoUpdateModelsSwitch`, `number.py:AccountModelsRefreshMinutesNumber`, `account.py` |
| **M1** Election-Fallback | Entfällt vollständig: genau eine Konto-Entry, deterministisch per Singleton-`unique_id`, kein „erster geladener Entry wird Owner". | `__init__.py` (Code-Entfernung), `const.py:ACCOUNT_ENTRY_UNIQUE_ID` |
| **M2** Reachability-Freshness | `is_on` wird **on-read** aus Evidenz + TTL berechnet; ein Publish-Tick (5 min, ohne HTTP) schreibt den Zustand neu, damit TTL-Ablauf sichtbar wird. | `account_logic.py:resolve_reachability`, `account.py:ACCOUNT_PUBLISH_TICK_MINUTES`, `binary_sensor.py` |
| **M3** `models_block`-Konsumenten | Vollständige Liste §2.6/§2.13 + §8.3; der Block lebt nur noch im `AccountCoordinator.data`. | `diagnostics.py`, `button.py`, `coordinator.py:_snapshot`, `sensor.py`, `__init__.py`-Toleranz-Snapshot, Tests |
| **M4** HA-Versionskompatibilität | Mindestversion **2026.8.0** in `hacs.json` (Basis für `async_update_device(new_config_entry_id=...)` und die per-Entry-Identifier-Indizierung) + Guard: `TypeError` beim Transfer → Log + Repair-Issue `adoption_unsupported`, kein Crash. Zusätzlich Test, der die deprecated Device-APIs im Quelltext verbietet. | `hacs.json`, `account.py:_entry_transfer_supported`, `tests/test_ha_compat.py` |
| **M5** Account-Startup/Fehlerisolation | Konto-Setup wirft nie: Erst-Fetch, Adoption, Sweeps und `ensure` sind try/except-gekapselt; kein `async_config_entry_first_refresh`, kein `config_entry=`, kein `ConfigEntryAuthFailed` aus dem Konto. Workspace-Setup ruft `ensure` tolerant auf. | `account.py`, `__init__.py:async_setup_entry` |
| **M6** Modulgrenzen | Reine Logik in HA-freie Module (`account_logic.py`, `catalog.py`, `api.py`) → ohne HA-Install testbar; HA-Glue in `account.py`. `coordinator.py` re-exportiert die bewegten Symbole (keine Test-Brüche). | §2 |
| **M7** Doku/Entity-Map/Canon | README-Entity-Tabelle, `docs/dashboards/entity-map.yaml`, `docs/dashboards/README.md` (HA-Min), CHANGELOG 2.0.0; Canon-Änderung **nur im Submodul-Repo** `external/ha-gauge-standard`. | §9 |
| **M8** Evidenz-Races | Keine Push-Kette: Reachability liest Evidenz bei jedem Property-Zugriff aus den *aktuell geladenen* Coordinator-Objekten (Snapshot-Liste via `list(hass.data[DOMAIN].items())`, Referenz-Swap von `coordinator.data` ist atomar). | `account.py:_collect_evidence`, `workspace_coordinators` |

**Nicht mehr vorhanden (bewusst gelöscht):** `coordinator.is_catalog_owner`, `hass.data[DOMAIN]["_catalog_owner"]`,
Owner-Transfer in `async_unload_entry`, `fetch_models()` im Workspace-Zyklus, `models_block` im
Workspace-Snapshot, `AutoUpdateModelsSwitch`/`ModelsRefreshMinutesNumber` in den Workspace-Plattformen.

---

## 2. Datei-für-Datei-Änderungen

| Datei | Status | Kern |
|---|---|---|
| `custom_components/go_gauge/api.py` | **neu** (HA-frei) | `OpenCodeGoApiClient`, `BROWSER_HEADERS`, `MODELS_URL`, `USAGE_URL` |
| `custom_components/go_gauge/catalog.py` | **neu** (HA-frei) | `_pnum`, `efficiency`, `build_models_block` |
| `custom_components/go_gauge/account_logic.py` | **neu** (HA-frei) | Konto-Kinds, Unique-ID-Normalisierung, Evidenz/TTL |
| `custom_components/go_gauge/account.py` | **neu** | `AccountCoordinator`, Setup/Unload/Remove, `ensure`, Adoption, Sweeps |
| `custom_components/go_gauge/const.py` | geändert | Konto-Konstanten, Entry-Kind, TTL-Faktoren, `ACCOUNT_PLATFORMS` |
| `custom_components/go_gauge/__init__.py` | geändert | Branching Entry-Kind, `async_setup`, v8-Bump, Owner-Code raus |
| `custom_components/go_gauge/coordinator.py` | geändert | Models-Zyklus raus, `last_usage_*`-Evidenz, Intervall nur Usage, Re-Exports |
| `custom_components/go_gauge/entity.py` | geändert | `GoGaugeAccountEntityBase` als eigener Typ (`available=True`), `persist_account_options` |
| `custom_components/go_gauge/sensor.py` | geändert | Branch Account/Workspace, Katalog-Sensoren am `AccountCoordinator` |
| `custom_components/go_gauge/binary_sensor.py` | geändert | Branch; `ApiReachableBinarySensor` aus Evidenz |
| `custom_components/go_gauge/button.py` | geändert | Branch; `AccountRefreshButton` (Katalog) + `RefreshButton` (nur Usage) |
| `custom_components/go_gauge/switch.py` | geändert | Branch; Workspace nur `AutoUpdateUsageSwitch`, Konto `AccountAutoUpdateModelsSwitch` |
| `custom_components/go_gauge/number.py` | geändert | Branch; Workspace ohne Modell-Intervall, Konto `AccountModelsRefreshMinutesNumber` |
| `custom_components/go_gauge/diagnostics.py` | geändert | Konto-Zweig + Workspace-Verweis, `models_block_present` raus |
| `custom_components/go_gauge/config_flow.py` | geändert | `VERSION = 8`, `async_step_import`, Konto-Options-Flow |
| `custom_components/go_gauge/strings.json` + `translations/{de,en}.json` | geändert | `button.refresh_account`, `options.account.*`, `issues.*` |
| `custom_components/go_gauge/manifest.json` | geändert | `"version": "2.0.0"` |
| `hacs.json` | geändert | `"homeassistant": "2026.8.0"` |
| `tests/*` | geändert/neu | §8 |

### 2.1 `api.py` (neu, HA-frei)
```python
MODELS_URL = "https://opencode.ai/zen/go/v1/models"
USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
BROWSER_HEADERS: dict[str, str]           # unverändert aus coordinator.py
class OpenCodeGoApiClient:
    def __init__(self, session: aiohttp.ClientSession) -> None: ...
    async def fetch_models(self) -> list[str]: ...
    async def fetch_usage(self, token: str) -> dict[str, Any]: ...   # GoGaugeAuthError bei 401
```
Reiner Move aus `coordinator.py:208-249`; Verhalten unverändert (403-Entitlement-Trennung bleibt).

### 2.2 `catalog.py` (neu, HA-frei)
Move von `_pnum`, `efficiency`, `build_models_block` (`coordinator.py:58-91, 264-299`). Signatur unverändert:
`build_models_block(live_ids: list[str] | None) -> dict[str, Any]` mit den Schlüsseln
`model_count_live, models, cheapest_model, cheapest_overall, cheapest_ratio, free_models, models_updated_at`.

### 2.3 `account_logic.py` (neu, HA-frei)
```python
ACCOUNT_ENTITY_KINDS: tuple[str, ...] = (
    "model_catalog", "models_live_count", "cheapest_model", "free_models", "api_reachable")
PLATFORM_BY_KIND: dict[str, str] = {...}          # sensor×4 + binary_sensor

def canonical_unique_id(kind: str) -> str: ...            # f"account_{kind}"
def kind_for_unique_id(unique_id: str) -> str | None: ...  # account_<kind> ODER *_<kind>
def matches_kind(platform: str, unique_id: str, kind: str) -> bool: ...

@dataclass(frozen=True)
class Evidence: source: str; workspace: str | None; outcome: str; at: datetime  # ok|neutral|fail
@dataclass(frozen=True)
class Reachability: state: bool | None; reason: str; counts: dict[str, int]
def models_ttl(base_minutes: int | None) -> timedelta: ...   # 2×, Fallback 120 min
def usage_ttl(minutes: int) -> timedelta: ...                # 3×
def resolve_reachability(ev: Sequence[Evidence], now: datetime,
                         models_ttl_: timedelta, usage_ttl_: timedelta) -> Reachability: ...
```
`kind_for_unique_id` sortiert Kandidaten **nach Länge absteigend** (deterministisch, kein Suffix-Konflikt).
Die Konto-Settings-IDs (`account_auto_update_models`, `account_models_refresh_minutes`) sind **keine** Kinds
und werden von `kind_for_unique_id` nicht erfasst.

### 2.4 `account.py` (neu)
```python
class AccountCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None: ...  # name=f"{DOMAIN}_account"
    data: dict[str, Any]
    auto_fetch: bool                    # aus entry.options, Default DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS
    base_interval_minutes: int          # aus entry.options, Default DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES
    last_models_ok: datetime | None
    last_models_attempt: datetime | None
    last_models_outcome: str | None     # "ok" | "fail"
    @property
    def fetch_interval_minutes(self) -> int | None: ...      # None wenn auto_fetch False
    async def _async_update_data(self) -> dict[str, Any]: ... # NUR Snapshot, nie HTTP
    async def async_fetch_now(self) -> None: ...              # HTTP, Backoff/Reset, danach Publish
    def set_fetch_interval(self, minutes: int) -> None: ...   # Basisintervall + Reschedule + Publish
    def set_auto_fetch(self, enabled: bool) -> None: ...      # Timer an/aus + Publish
    def _effective_fetch_minutes(self) -> int | None: ...     # Backoff-Abbildung (§6.3)
    def _reschedule_fetch(self) -> None: ...                  # async_track_time_interval neu setzen
    def _snapshot(self, now: datetime) -> dict[str, Any]: ...

def workspace_coordinators(hass: HomeAssistant) -> dict[str, GoGaugeCoordinator]: ...

async def async_ensure_account_entry(hass: HomeAssistant) -> None: ...
async def async_setup_account_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool: ...
async def async_unload_account_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool: ...
async def async_remove_account_entry(hass: HomeAssistant, entry: ConfigEntry) -> None: ...
async def async_adopt_account_entities(hass: HomeAssistant,
                                       entry: ConfigEntry) -> AdoptionReport: ...
async def async_remove_legacy_model_settings(hass: HomeAssistant) -> int: ...

@dataclass
class AdoptionReport:
    renamed: list[tuple[str, str]]; moved: list[str]; device_moved: str | None
    merged_devices: list[str]; collisions: list[tuple[str, str]]; errors: list[str]
    removed_settings: int
```
Zwei getrennte Zeitgeber (§6.2): `update_interval` des Coordinators ist **immer**
`ACCOUNT_PUBLISH_TICK_MINUTES` (5 min) und macht **keinen** HTTP-Call; der Fetch läuft über einen
separaten `async_track_time_interval`-Timer im effektiven Intervall. `async_fetch_now()` ist der einzige
HTTP-Pfad (Timer, Button, einmaliger Erst-Fetch). Kein `config_entry=`-Argument, kein
`async_config_entry_first_refresh`.

### 2.5 `const.py`
```python
ACCOUNT_ENTRY_UNIQUE_ID = f"{DOMAIN}_account"
CONF_ENTRY_KIND = "entry_kind"
ENTRY_KIND_ACCOUNT = "account"
ENTRY_KIND_WORKSPACE = "workspace"
DATA_ACCOUNT_KEY = "_account"
DATA_ADOPTION_LOCK = "_adoption_lock"
ACCOUNT_DEVICE_IDENTIFIER = "account"
ACCOUNT_PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "number"]
ACCOUNT_PUBLISH_TICK_MINUTES = 5

DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS = True
DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES = 60
ACCOUNT_MODELS_REFRESH_MIN = 1
ACCOUNT_MODELS_REFRESH_MAX = 1440
ACCOUNT_BACKOFF_MAX_MINUTES = 60

MODELS_TTL_FACTOR = 2
USAGE_TTL_FACTOR = 3
MODELS_TTL_FALLBACK_MINUTES = MODELS_TTL_FACTOR * DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES
ENTITY_NAME_ACCOUNT = "Go Gauge Konto"

def conf_entry_kind(data: Mapping[str, Any]) -> str: ...   # Default ENTRY_KIND_WORKSPACE
```
`CONF_AUTO_UPDATE_MODELS` / `CONF_MODELS_REFRESH_MINUTES` bleiben als Options-Schlüssel bestehen, gelten
ab 2.0.0 aber **ausschließlich** für die Konto-Entry.

### 2.6 `__init__.py`
- **neu** `async def async_setup(hass, config) -> bool`: `await account.async_ensure_account_entry(hass)`
  in try/except → `return True`. (E2 „beim HA-Start".)
- `async_migrate_entry`: Ziel `8`; `if entry.version > 8: return False`; Schritte ≤7 unverändert; neuer Schritt
  `if new_version < 8: data.setdefault(CONF_ENTRY_KIND, ENTRY_KIND_WORKSPACE); new_version = 8`.
  **Keine** Registry-/Netz-Aufrufe in v8 (B4) — O(1), ein `async_update_entry` am Ende.
- `async_setup_entry`: `if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT: return await account.async_setup_account_entry(hass, entry)`.
  Workspace-Pfad: Coordinator + toleranter Erst-Refresh (Fallback-Snapshot ohne `models_block`, mit
  `last_usage_ok`/`last_usage_attempt`/`last_usage_outcome`), Registry-Store, `async_forward_entry_setups`,
  `entry.add_update_listener(_async_update_listener)` (nur Workspace!), danach
  `try: await account.async_ensure_account_entry(hass)`.
- `async_unload_entry`: Konto-Branch → `async_unload_account_entry`; Workspace-Branch ohne Owner-Logik.
- **neu** `async def async_remove_entry(hass, entry)`: Konto-Branch → `async_remove_account_entry`;
  Workspace → `None`.

### 2.7 `coordinator.py`
- Imports auf `from .api import ...`, `from .catalog import build_models_block, efficiency` + **Re-Export**
  (`build_models_block`, `efficiency`, `OpenCodeGoApiClient`, `MODELS_URL`, `USAGE_URL`, `BROWSER_HEADERS`),
  damit `tests/test_coordinator_parsing.py` und `tests/manual_offline_smoke.py` unverändert laufen.
- `__init__`: `update_interval = timedelta(minutes=self.usage_minutes) if self.auto_usage else None`.
  `auto_models` / `models_minutes` **entfallen** (die Settings leben auf der Konto-Entry).
  Neu: `last_usage_attempt`, `last_usage_ok`, `last_usage_outcome`.
- `recalculate_interval()`: nur noch Usage.
- `_async_update_data()`: Models-Abschnitt (`coordinator.py:581-617`) entfällt; `last_usage_attempt = now`
  immer, `last_usage_ok`/`last_usage_outcome` am Zyklusende (`ok` wenn `ok_any`; `neutral` wenn alle
  `no_subscription`/`auth_error`; sonst `fail`). Auth-Pfad (`ConfigEntryAuthFailed`) unverändert.
- `_snapshot()`: Keys `fetched_at, last_usage_fetch, last_usage_ok, last_usage_attempt, last_usage_outcome,
  auto_usage, usage_refresh_minutes, workspaces`. `models_block`/`last_models_fetch` entfallen (M3).

### 2.8 `entity.py`
`GoGaugeAccountEntityBase` wird **eigene** Basis (nicht mehr Subklasse von `GoGaugeEntityBase`):
```python
class GoGaugeAccountEntityBase(CoordinatorEntity):
    _attr_has_entity_name = True
    coordinator: AccountCoordinator
    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry) -> None: ...
        # device_info: identifiers {(DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)}, name ENTITY_NAME_ACCOUNT
    @property
    def available(self) -> bool: return True      # available=True + None ist HA-konform

def persist_account_options(hass, entry: ConfigEntry, **changes: Any) -> bool:
    """Options der Konto-Entry schreiben - ohne Reload-Flag (kein Update-Listener)."""
```
`persist_options` (Workspace) bleibt unverändert.

### 2.9 `sensor.py`
- `async_setup_entry`: Konto-Branch → `ModelCatalogSensor`, `LiveModelsCountSensor`, `CheapestModelSensor`,
  `FreeModelsSensor` (Typ `AccountCoordinator`); Workspace-Branch = bisheriger Fenster-Satz, der bisherige Owner-Block entfällt.
- `ModelCatalogSensor.extra_state_attributes`: zusätzlich `account_auto_update_models` (bool),
  `account_fetch_interval_minutes` (int|None, effektives Intervall inkl. Backoff),
  `account_models_refresh_minutes` (int, Basisintervall), `account_last_models_outcome` (str|None).
- Alle vier Katalog-Sensoren lesen `self.coordinator.data["models_block"]` vom `AccountCoordinator`; `None`-Verhalten
  unverändert (`native_value → None`).

### 2.10 `binary_sensor.py`
Branch wie §2.9. `ApiReachableBinarySensor.is_on`:
```python
result = resolve_reachability(_collect_evidence(self.hass), now,
                              models_ttl(account.base_interval_minutes),
                              usage_ttl(ws.usage_minutes))
return result.state    # True | False | None
```
`extra_state_attributes`: `reachability_reason`, `evidence` (Liste `{source, workspace, outcome, age_s, ttl_s, fresh}`),
`models_ttl_s`, `usage_ttl_s`, `checked_at`. `RateLimitedBinarySensor` / `SubscriptionActiveBinarySensor` unverändert.

### 2.11 `button.py`
- Workspace: `RefreshButton` setzt nur `last_usage_fetch = None` (kein Katalog-Fetch).
- Konto: `AccountRefreshButton(_attr_translation_key = "refresh_account")`, `unique_id = "account_refresh"`,
  `async_press → await self.coordinator.async_fetch_now()` (nur Katalog).

### 2.12 `switch.py` / `number.py`
Beide Plattformen verzweigen nach `conf_entry_kind`:
- **Workspace:** `switch.py` erzeugt nur `AutoUpdateUsageSwitch`; `number.py` nur `UsageRefreshMinutesNumber`,
  `WarnPercentNumber`, `PaceRedPercentNumber`. `AutoUpdateModelsSwitch` und `ModelsRefreshMinutesNumber`
  werden **nicht mehr erzeugt** (Registry-Bereinigung §3.3/§4.7).
- **Konto:** `switch.py` → `AccountAutoUpdateModelsSwitch` (`unique_id = "account_auto_update_models"`,
  `translation_key = "auto_update_models"`), `number.py` → `AccountModelsRefreshMinutesNumber`
  (`unique_id = "account_models_refresh_minutes"`, `translation_key = "models_refresh_min"`,
  `min = ACCOUNT_MODELS_REFRESH_MIN`, `max = ACCOUNT_MODELS_REFRESH_MAX`, `step = 1`).
- Konto-Setter:
  ```python
  def set_auto_fetch(enabled): coordinator.set_auto_fetch(enabled)
  persist_account_options(hass, entry, **{CONF_AUTO_UPDATE_MODELS: enabled})
  self.async_write_ha_state()
  ```
  Analog `set_fetch_interval(int(value))` mit `CONF_MODELS_REFRESH_MINUTES`.
  Kein `recalculate_interval()`, kein `_skip_reload`, kein Reload — die Konto-Entry hat keinen Update-Listener.
- Usage-Setter im Workspace unverändert (`recalculate_interval()` + `persist_options`).

### 2.13 `diagnostics.py`
- Konto-Entry: eigener Zweig (`conf_entry_kind`), liefert `auto_update_models`, `models_refresh_minutes`,
  `fetch_interval_minutes` (effektiv), `backoff_active` (bool), `last_models_attempt/ok/outcome`,
  `reachability` (`reason` + Evidenz), `adoption` (letzter Report aus `hass.data`) — ohne Token (Konto hat keinen).
- Workspace-Entry: `models_block_present` und das Owner-Feld entfallen; neu
  `account: {loaded: bool, entry_id, auto_update_models, models_refresh_minutes, reachability_reason}`
  aus `hass.data[DOMAIN].get(DATA_ACCOUNT_KEY)`. `REDACT_KEYS` unverändert.

### 2.14 `config_flow.py`
`VERSION = 8`; neu:
```python
async def async_step_import(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
    await self.async_set_unique_id(ACCOUNT_ENTRY_UNIQUE_ID)
    self._abort_if_unique_id_configured()
    return self.async_create_entry(title=ENTITY_NAME_ACCOUNT,
                                   data={CONF_ENTRY_KIND: ENTRY_KIND_ACCOUNT})
```
`async_get_options_flow`: bei `conf_entry_kind == ENTRY_KIND_ACCOUNT` → `GoGaugeAccountOptionsFlowHandler`,
sonst bisheriger Handler. Der Workspace-Options-Flow verliert die Felder `auto_update_models` und
`models_refresh_minutes`; der Konto-Options-Flow bietet genau diese beiden Felder an.

---

## 3. Lifecycle-/Adoptionsschritte (exakte Reihenfolgen)

**Invariante:** Konto-Setup ist selbsttragend (braucht keinen Workspace); Workspace-Setup braucht kein Konto.
Beide Reihenfolgen sind korrekt, weil Adoption ausschließlich Registry/Device-Zustand + `account_entry.entry_id`
liest. `async_migrate_entry` v8 enthält **keine** Registry-Arbeit (§2.6).

### 3.1 HA-Neustart
1. `async_setup(hass, config)` → `async_ensure_account_entry` (Lock → vorhandene Konto-Entry per
   `unique_id == ACCOUNT_ENTRY_UNIQUE_ID`; geladen? → return; deaktiviert? → return; sonst `async_setup`).
2. HA setzt gespeicherte Entries in Storage-Reihenfolge auf — nicht steuerbar, nicht relevant:
   - **Konto zuerst:** `async_setup_account_entry` = Registry-Arbeit (Entity-Sweep → Duplikat-Devices →
     Device-Move → Alt-Settings-Entfernung, §3.3) → Coordinator → `hass.data` → `async_forward_entry_setups`
     → Fetch-Timer → einmaliger Erst-Fetch (nur wenn `models_block is None`, tolerant).
   - **Workspace zuerst:** Workspace-Coordinator → forward → `ensure_account_entry` (legt an/lädt).
     Der Workspace bekommt keine Katalog-Entities (kein Owner-Flag mehr).
3. Konto-Fetch läuft unabhängig von der Anzahl geladener Workspaces mit den Konto-Settings.

### 3.2 Erstinstallation
User-Flow (`async_step_user`) → Workspace-Entry **v8** → `async_setup_entry` → `ensure_account_entry` →
Import-Flow legt Konto-Entry an (Titel `Go Gauge Konto`, `data={"entry_kind": "account"}`, kein Token) →
Konto-Setup: Registry-Sweep findet 0 Kandidaten (leere Registry) → Konto-Entities + Konto-Settings werden neu
angelegt (Auto-Refresh ON, 60 min).

### 3.3 v7 → v8-Update
1. `async_migrate_entry` je Workspace-Entry: `data[entry_kind]="workspace"`, `version=8` (ein `async_update_entry`,
   atomar, kein I/O) → HA lädt die Entry neu.
2. Workspace-Setup (ohne Katalog-Entities) → `ensure_account_entry` → Konto-Entry wird angelegt.
3. Konto-Setup **vor** `async_forward_entry_setups` (gesamte Registry-Arbeit, idempotent, reihenfolgeunabhängig):
   a. **Entity-Sweep je Kind (§4.1/§4.3):** Kandidaten über `platform` + `unique_id` (canonical ODER
      Legacy-Suffix), inkl. `config_entry_id is None`. Pro Gewinner: `new_unique_id="account_<kind>"` →
      `config_entry_id=account_entry.entry_id` → `device_id=<kanonisches Device>`.
      → **Entities zuerst**, damit der folgende Device-Move keine Entities mehr verliert.
   b. **Duplikat-Devices:** Entities aller Kandidaten-Devices sind jetzt auf dem kanonischen Device; ein
      Duplikat-Device wird **nur entfernt, wenn es danach leer ist** (keine Entities, keine Child-Devices auf
      HA 2026.9+); sonst Repair-Issue `account_device_not_empty` und Device bleibt stehen.
   c. **Device-Move:** `dr.async_update_device(kanonisches_device.id,
      new_config_entry_id=account_entry.entry_id)`. Gewinner-Device = das Device mit
      `(DOMAIN, "account")` in `identifiers`, das bereits der Konto-Entry gehört; sonst
      `min(key=(account_entry.entry_id not in d.config_entries, d.id))`.
   d. **Alt-Settings-Entfernung (§4.7):** Registry-Einträge der Workspace-Modell-Regler
      (`*_auto_update_models`, `*_models_refresh_minutes`, ausgenommen die kanonischen `account_*`) werden
      per `async_remove` entfernt.
   e. Erst danach Platform-Forward: HA findet die Registry-Einträge über die nun kanonische `unique_id` und
      aktualisiert sie → `entity_id`, History, Area, Labels, Device-Zuordnung bleiben.
4. Erwartete Übergangserscheinung: zwischen Schritt 1 und 3e sind die 5 Entities kurz `unavailable`
   (Registry-Eintrag ohne laufende Platform-Instanz). Kein Fehlerzustand, kein Nutzereingriff.

### 3.4 Konto-Entry löschen
1. HA: Plattform-Unload (5 Entities + Button + Switch + Number) → `async_unload_account_entry` (Fetch-Timer
   abbrechen, Coordinator aus `hass.data`, `DATA_ACCOUNT_KEY` entfernen) → `async_remove_account_entry`
   (Adoption-Issues löschen) → HA entfernt Entity-Registry-Einträge und das Device (nur noch dieser Entry
   als Besitzer).
2. Neu angelegt wird sie beim **nächsten** Workspace-Setup (Reload/Options-Änderung/HA-Start) — E2.
3. Restrisiko: Neu-Erzeugung der Entities kann bei vorher deduplizierten Slugs (`_2`) eine andere `entity_id`
   ergeben (Registry-Eintrag war gelöscht) → §11 R3.

### 3.5 Konto-Entry deaktivieren
`ensure` erkennt `disabled_by is not None` → **kein** Re-Create/Re-Enable (Nutzerintent respektiert). Konto-Entities
bleiben als Registry-Einträge, Zustand `unavailable`; der Fetch-Timer läuft nicht. Workspaces laufen unverändert.
Re-Aktivierung: Nutzer lädt die Integration neu (oder HA-Neustart), dann Setup wie 3.1.

### 3.6 Workspace-Reauth / Reload / Löschen
- **Reauth:** Token-Update → `async_reload(workspace)` → Unload → Setup (`ensure`: Konto existiert/geladen →
  return). Der Konto-Coordinator **überlebt** (eigene Entry) → `last_models_ok` bleibt erhalten, kein
  unnötiger Katalog-Fetch.
- **Reload (Options-Änderung):** identisch; `ensure`/Adoption/Sweeps sind idempotent → 0 Schreibzugriffe.
- **Workspace löschen:** Unload → HA entfernt Workspace-Entities und Workspace-Device. Das Konto-Device ist
  nicht an diesen Entry gebunden → nichts zu verschieben. Konto-Fetch läuft weiter.

### 3.7 Letzter Workspace weg
Die Konto-Entry bleibt geladen und **fetcht weiter** mit ihren eigenen Settings (Default ON, 60 min) — die
Konto-Daten sind workspace-unabhängig. `ModelCatalog` behält den letzten Katalog; `ApiReachable` ist `ON`,
solange ein Katalog-Fetch innerhalb der TTL liegt; ohne frische Evidenz `unknown`. Es gibt keine
Workspace-Evidenz mehr, also auch keine `usage`-Quelle in den Attributen.

---

## 4. Migrations-/Kollisionsstrategie + Idempotenz

### 4.1 Kandidatenmenge (Registry-Sweep)
Für jedes Kind aus `ACCOUNT_ENTITY_KINDS` (5) und die zugehörige Platform:
`entries = [e for e in ent_reg.entities.values() if e.platform == platform and e.domain == DOMAIN
 and (e.unique_id == f"account_{kind}" or e.unique_id.endswith(f"_{kind}"))]`
→ deckt `{<beliebige_entry_id>}_{kind}` (v1.x) und `account_{kind}` ab; `config_entry_id is None`
(verwaiste Einträge) ist enthalten. Kein Zugriff auf Workspace-Objekte nötig.

### 4.2 Deterministischer Gewinner
`winner = min(candidates, key=(0 if unique_id == canonical else 1, entity_id or "", id))`
→ kanonische `unique_id` gewinnt immer; sonst lexikografisch kleinste `entity_id`. Die Reihenfolge der
Sweeps/Setups beeinflusst das Ergebnis nicht (E7).

### 4.3 Verlierer (Kollision)
Kein Löschen, kein Abbruch:
1. `config_entry_id = account_entry.entry_id` (verhindert Verwaist-Werden beim Löschen des Alt-Workspace),
2. `device_id = <kanonisches Device>` (sonst bleibt das Alt-Device als Geist),
3. `disabled_by = RegistryEntryDisabler.USER` (kein Unique-ID-Konflikt mit der Platform, kein Polluting der Device-Karte),
4. Legacy-`unique_id` **unverändert** lassen,
5. Repair-Issue `account_entity_collision_<kind>` (`is_fixable=False`, `severity=WARNING`,
   Platzhalter: `kind`, `winner`, `losers`), Text: „Duplikat deaktiviert, bitte löschen".

### 4.4 Repair-Issue-Ansatz (`homeassistant.helpers.issue_registry`)
```python
ir.async_create_issue(hass, DOMAIN, issue_id, is_fixable=False,
                      severity=ir.IssueSeverity.WARNING, translation_key="account_entity_collision",
                      translation_placeholders={...})
```
Issue-IDs (stabil, damit idempotent aktualisiert statt dupliziert wird):
`account_entity_collision_<kind>`, `account_device_merged`, `account_device_not_empty`,
`adoption_unsupported`, `adoption_failed`.
`ir.async_delete_issue(...)` bei erfolgreicher Adoption (pro Kind/Issue) sowie in `async_remove_account_entry`.
Übersetzungen: `strings.json → "issues": {"account_entity_collision": {...}, ...}` + `translations/{de,en}.json`
(hassfest-Konsistenz prüft das).

### 4.5 Idempotenz-Nachweis (Reihenfolge: Entities vor Device)
Pro Entity/Device ist die Adoption eine Konjunktion von Prädikaten; jedes Prädikat ist nach einem Lauf wahr
und seine Verletzung ist der einzige Auslöser des jeweiligen Schreibzugriffs:

| # | Prädikat `P(x)` | Schreibzugriff nur wenn `¬P` | Nach 1 Lauf |
|---|---|---|---|
| 1 | `e.unique_id == account_<kind>` **oder** e ist designierter Verlierer | `async_update_entity(new_unique_id=...)` | wahr |
| 2 | `e.config_entry_id == account_entry_id` | `async_update_entity(config_entry_id=...)` | wahr |
| 3 | `e.device_id == kanonisches_device.id` (wenn Device existiert) | `async_update_entity(device_id=...)` | wahr |
| 4 | Duplikat-Device hat keine Entities (und keine Child-Devices) | `dr.async_remove_device(loser.id)` | wahr |
| 5 | `account_entry_id in device.config_entries` | `dr.async_update_device(device_id, new_config_entry_id=...)` | wahr |
| 6 | kein Registry-Eintrag mit `*_auto_update_models` / `*_models_refresh_minutes` außer `account_*` | `ent_reg.async_remove(entity_id)` | wahr |

Schritte 1–3 laufen **vor** 4–5: erst wenn keine Entity mehr auf ein Alt-Device zeigt, darf das Device
verschwinden bzw. wandern (sonst löscht der Device-Move Entities, die noch zur alten Entry gehören).
Zweiter Lauf: **0** Registry-/Device-Schreibzugriffe (Test-Assertion über Mock-Callcounts, §8).
Reihenfolgeunabhängigkeit: der Sweep liest nur persistierten Zustand; die Lock serialisiert parallele Aufrufe
(HA-Start + Import-Flow + Workspace-Setup), danach greifen die Prädikate.

### 4.6 Fehlerpfad (M5)
Jeder Kandidat läuft in eigenem `try/except (ValueError, TypeError, KeyError)`; Fehler gehen in
`report.errors`, lösen `adoption_failed` (einmalig) aus und brechen **nie** das Konto-Setup ab. `ValueError`
bei `new_unique_id` wird durch die Vorabprüfung `ent_reg.async_get_entity_id(platform, DOMAIN, canonical)`
vermieden; schlägt sie fehl, greift der Verlierer-Pfad.

### 4.7 Alt-Settings-Entfernung (Breaking)
Kandidaten: Registry-Einträge mit `platform in {"switch", "number"}`, `domain == DOMAIN` und
`unique_id.endswith("_auto_update_models") or unique_id.endswith("_models_refresh_minutes")`, **ausgenommen**
die kanonischen IDs `account_auto_update_models` und `account_models_refresh_minutes`.
Ablauf: `ent_reg.async_remove(entity_id)` je Kandidat, Zähler in `report.removed_settings`, Log-Zeile mit Anzahl.
Kein Repair-Issue (gewollte Entfernung), kein Werte-Übernehmen aus den Workspace-Options (die Semantik wechselt
von „pro Workspace" auf „global"; die Konto-Entry startet mit ON/60, sofern keine Konto-Options existieren).
Idempotent: zweiter Lauf findet 0 Kandidaten.

---

## 5. Reachability-Matrix (TTL, Stale, „Evidenz")

**Evidenz** = ein *beobachteter, zeitgestempelter Fetch-Ausgang eines aktuell geladenen Coordinators*:
`Evidence(source, workspace, outcome, at)` mit
`source ∈ {"models","usage"}`, `workspace` = `ws_name` (nur usage), `outcome ∈ {"ok","neutral","fail"}`.

| Ausgang | Quelle | Einstufung | Begründung |
|---|---|---|---|
| Katalog-Fetch erfolgreich | `AccountCoordinator.last_models_ok/attempt` | `ok` | API lieferte Daten |
| Workspace-Zyklus `ok_any` | `GoGaugeCoordinator.last_usage_ok` | `ok` | API lieferte Nutzungsdaten |
| `status == "no_subscription"` | Workspace | `neutral` | API hat geantwortet (403 Entitlement), kein Erfolgsbeleg |
| `status == "auth_error"` (401) | Workspace | `neutral` | API hat geantwortet, Konfig-/Token-Problem |
| `status == "error"` / Exception | Workspace | `fail` | 403 ohne Entitlement (Cloudflare/Rate-Limit), 5xx, Netz |
| Katalog-Fetch-Exception | Konto | `fail` | kein verwertbarer Response |

**TTL:** `models_ttl = MODELS_TTL_FACTOR (2) × base_interval_minutes` der Konto-Entry (das **konfigurierte
Basisintervall**, nicht das backoff-verlängerte Effektivintervall). Ist Auto-Refresh aus, gilt
`MODELS_TTL_FALLBACK_MINUTES = 120`. `usage_ttl = 3 × usage_minutes` des jeweiligen
Workspace-Coordinators (Default 10 min → 30 min). Frisch ⇔ `now - at <= ttl`.

**Auflösung** (`resolve_reachability`, Priorität strikt in dieser Reihenfolge):

| Lage (nur frische Evidenz) | Ergebnis | `reason` |
|---|---|---|
| mind. ein `ok` | `True` (ON) | `fresh_ok` |
| kein `ok`, mind. ein `neutral` | `None` (unknown) | `answered_no_success` |
| nur `fail` | `False` (OFF) | `all_fresh_failed` |
| keine frische Evidenz | `None` (unknown) | `no_evidence` |

Stale Evidenz wird ignoriert (nicht als `fail` gewertet). `no_subscription`/`auth_error` zählen **nie** als
Erfolg. Der 5-Minuten-Publish-Tick schreibt den Zustand neu, damit ein TTL-Ablauf in der UI ankommt; der Tick
macht **keinen** HTTP-Call (§2.4). Attribute am Sensor: `reachability_reason`, `evidence[]`,
`models_ttl_s`, `usage_ttl_s`, `checked_at`.

---

## 6. Konto-Einstellungen (E4)

`auto_update_models` und `models_refresh_minutes` sind **globale Konto-Einstellungen** und liegen in den
Options der Konto-Entry. Es gibt keine Ableitung aus Workspace-Werten; Workspaces besitzen diese Regler nicht.

### 6.1 Entities und Defaults

| Entity | Klasse | `unique_id` | Wertebereich | Default |
|---|---|---|---|---|
| Switch | `AccountAutoUpdateModelsSwitch` | `account_auto_update_models` | on/off | **ON** (`DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS`) |
| Number | `AccountModelsRefreshMinutesNumber` | `account_models_refresh_minutes` | 1–1440 min | **60** (`DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES`) |

Beide hängen am Konto-Device (`(DOMAIN, "account")`) und werden über `ACCOUNT_PLATFORMS` von der Konto-Entry
erzeugt.

### 6.2 Zwei Zeitgeber (Fetch vs. Publish)

| Zeitgeber | Intervall | Aufgabe | HTTP |
|---|---|---|---|
| `DataUpdateCoordinator.update_interval` | fix `ACCOUNT_PUBLISH_TICK_MINUTES` (5 min) | Snapshot neu berechnen + Listener/States schreiben (TTL-Ablauf sichtbar) | **nein** |
| Fetch-Timer (`async_track_time_interval`) | `_effective_fetch_minutes()` (Basisintervall, ggf. Backoff) | `async_fetch_now()` → Katalog-HTTP | **ja** |

Damit ist das konfigurierte Intervall die echte Fetch-Regel (kein 5-Minuten-Deckel). Der Timer wird in
`async_setup_account_entry` gestartet, bei jeder Einstellungsänderung neu gesetzt (`_reschedule_fetch`) und im
Unload abgebrochen. `auto_fetch = False` → kein Timer, `fetch_interval_minutes is None`; der Publish-Tick läuft
weiter.

### 6.3 Backoff bei 403 / Netzfehlern
- Ausgangspunkt ist das **Basisintervall** `b = base_interval_minutes`.
- Fehlschlag (HTTP 403 ohne Entitlement, 5xx, Netz-Exception) → `failures += 1`.
- Effektivintervall: `b` wenn `b > ACCOUNT_BACKOFF_MAX_MINUTES`, sonst `min(b * 2**failures, 60)`.
  Beispiele: `b=10` → 20 → 40 → 60 → 60; `b=60` → 60 (bereits am Deckel); `b=120` → 120 (unverändert).
- Erfolg → `failures = 0`, nächster Fetch wieder im Basisintervall.
- `401` ist auf der Konto-Entry nicht möglich (kein Token); `last_models_outcome` bleibt `ok|fail`.

### 6.4 Live-Wirkung ohne Reload
- Switch/Number-Setter: `coordinator.set_auto_fetch(...)` (setzt `coordinator.auto_fetch`) bzw.
  `coordinator.set_fetch_interval(...)` (setzt `base_interval_minutes`) → Timer sofort neu →
  `async_update_listeners()` → `persist_account_options(...)` schreibt `entry.options`.
- Für die Konto-Entry wird **kein** `entry.add_update_listener(...)` registriert; `hass.config_entries
  .async_update_entry(entry, options=...)` löst daher keinen Reload aus. `_async_update_listener` bleibt
  ausschließlich an Workspace-Entries gebunden.
- Der Konto-Options-Flow (`GoGaugeAccountOptionsFlowHandler`) bietet genau die beiden Felder an, wendet die
  Werte im `async_step_init` **live** über dieselben Setter an und persistiert sie über den normalen
  Options-Mechanismus.

### 6.5 Sichtbarkeit
`ModelCatalogSensor.extra_state_attributes` zeigt `account_auto_update_models`, `account_models_refresh_minutes`
(Basis), `account_fetch_interval_minutes` (effektiv inkl. Backoff) und `account_last_models_outcome`.
Zusätzlich im Diagnostics-Export (Konto- und Workspace-Zweig). Warnschwelle und Pace-Rot-Grenze bleiben
unverändert pro Workspace.

---

## 7. Account-Entry-Flow (Import, Singleton, Title, Data-Marker, kein Token)

### 7.1 Der Flow-Schritt
```python
# config_flow.py
class GoGaugeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 8

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(ACCOUNT_ENTRY_UNIQUE_ID)   # "go_gauge_account"
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=ENTITY_NAME_ACCOUNT,                            # "Go Gauge Konto", konstant
            data={CONF_ENTRY_KIND: ENTRY_KIND_ACCOUNT},
        )
```

| Aspekt | Festlegung | Begründung |
|---|---|---|
| Quelle | `context={"source": SOURCE_IMPORT}` | nie aus der „Integration hinzufügen"-UI erreichbar (kein `async_step_user`-Zweig für Konto) |
| Singleton | `unique_id = "go_gauge_account"` (Konstante `ACCOUNT_ENTRY_UNIQUE_ID`) | HA erzwingt Eindeutigkeit pro Domain; `_abort_if_unique_id_configured()` verhindert Duplikate |
| Titel | `ENTITY_NAME_ACCOUNT` = `"Go Gauge Konto"` | identisch mit dem Device-Namen → konsistente UI, keine Übersetzungsabhängigkeit (Entry-Titel sind Daten, keine `translation_key`s) |
| Data-Marker | `{CONF_ENTRY_KIND: ENTRY_KIND_ACCOUNT}` — **kein** `token`, **kein** `workspace_name` | Konto-Entry hat kein Geheimnis → Diagnostics/Redaction unkritisch; `conf_entry_kind()` klassifiziert ohne Sonderfälle |
| Options | eigener `GoGaugeAccountOptionsFlowHandler` (Pflicht) mit `auto_update_models` + `models_refresh_minutes` | die Regler gehören zur Konto-Entry; Persistenz in `entry.options`, Wirkung live ohne Reload (§6.4) |
| Reauth | nicht anwendbar; `async_step_reauth` bleibt unverändert (kein Token, kein 401 möglich) | Konto kennt keine Auth |
| Version | `VERSION = 8` → alle neu erzeugten Entries (Konto **und** Workspace) starten auf v8 | ein Zielversion-Wert, keine Versionsdrift zwischen Entry-Arten |
| Probe | **kein** `_probe_token` | kein Token vorhanden |
| Erst-Fetch | nicht im Flow, sondern in `async_setup_account_entry` (tolerant, einmalig) | Flow bleibt ohne I/O schnell; Setup entscheidet über Notwendigkeit (`models_block is None`) |

`async_step_user` (Workspace) schreibt zusätzlich `CONF_ENTRY_KIND: ENTRY_KIND_WORKSPACE` in `entry.data` — damit ist
jede Entry ab 2.0.0 explizit markiert (Altbestand wird per v8-Migration markiert, §2.6).

### 7.2 Singleton-Durchsetzung unter Concurrency (B3)
`async_ensure_account_entry` läuft immer unter `hass.data[DOMAIN][DATA_ADOPTION_LOCK]` (ein `asyncio.Lock`,
lazy per `setdefault` erzeugt) — dieselbe Lock serialisiert Adoption und Erzeugung:

```python
async def async_ensure_account_entry(hass):
    data = hass.data.setdefault(DOMAIN, {})
    lock = data.setdefault(DATA_ADOPTION_LOCK, asyncio.Lock())
    async with lock:
        entries = hass.config_entries.async_entries(DOMAIN)
        existing = _account_entries(entries)                    # unique_id == ACCOUNT_ENTRY_UNIQUE_ID
        if len(existing) > 1:                                   # nur bei hand-editiertem Storage
            _report_duplicate_account_entries(hass, existing)   # Repair WARNING, kein Löschen
        acc = min(existing, key=lambda e: e.entry_id) if existing else None
        if acc is not None:
            if acc.disabled_by is not None: return              # Nutzerintent respektieren (E2)
            if acc.state is ConfigEntryState.LOADED: return
            try: await hass.config_entries.async_setup(acc.entry_id)
            except Exception as err: _LOGGER.warning(...)       # nie propagieren
            return
        if not _has_enabled_workspace(entries): return          # kein Workspace → kein Konto anlegen
        try: await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT})
        except Exception as err: _LOGGER.warning(...)           # Workspace-Setup bricht NIE ab
```

- Doppel-Anlage durch zwei parallele Aufrufer ist durch die Lock + `_abort_if_unique_id_configured` ausgeschlossen;
  der Re-Check `async_entries` **innerhalb** der Lock ist der eigentliche Garant (der Abort allein wäre racig).
- **Implementierungs-Verifikation (kein offener Punkt mehr):** `flow.async_init(..., source=import)` läuft den
  Flow bis zum Ergebnis durch, d. h. nach dem `await` existiert die Entry (bzw. der Abort ist eingetreten).
  Verifikation: Test `test_account_lifecycle.py::test_ensure_creates_entry_once` zählt
  `async_entries(DOMAIN)` nach dem Aufruf.
- Konto-Setup läuft nach der Erzeugung durch HA selbst (Import-Entries werden sofort eingerichtet); ein
  zusätzliches `async_setup` im Aufrufer ist nicht nötig und würde doppelt laden.

### 7.3 HA-Setup-Reihenfolgen — Behandlungsmatrix

| Situation | Wer handled | Verhalten bei Fehler/Reihenfolge |
|---|---|---|
| HA-Start, Konto-Entry existiert | HA (Entry-Setup) + `async_setup`-Ensure | Beide idempotent; `ensure` sieht `LOADED` → No-op |
| HA-Start, Konto-Entry fehlt (gelöscht) | `async_setup` → `ensure` | Konto wird angelegt; Workspaces folgen unabhängig |
| HA-Start, Konto deaktiviert | `ensure` | Kein Re-Create/Re-Enable; Konto-Entities `unavailable` |
| Erstinstallation (0 Entries) | HA ruft `async_setup` **nicht** auf (kein Entry, keine YAML-Config) | Konto entsteht erst beim ersten Workspace-Setup |
| Workspace wird vor Konto geladen | Workspace-Setup-Ende → `ensure` | Konto wird geladen/angelegt |
| Konto wird vor Workspace geladen | Konto-Setup | Konto-Fetch läuft mit eigenen Settings (ON/60); Workspace-Evidenz kommt später hinzu |
| Workspace-Reload (Options/Reauth) | Unload → Setup → `ensure` | Konto-Coordinator bleibt am Leben (eigene Entry) → kein Katalog-Refetch |
| Konto-Entry wird gelöscht (Laufzeit) | `async_unload_account_entry` + `async_remove_account_entry` | Bleibt weg bis zum nächsten Workspace-Setup/HA-Start (E2) |
| Konto-Entry wird deaktiviert/aktiviert | `ensure` | Deaktiviert = No-op; Re-Aktivierung erst nach Reload/HA-Start wirksam (dokumentiert) |
| Konto-Settings ändern | Switch/Number/Options-Flow | Timer + Options sofort aktualisiert, **kein** Reload |
| Konto-Setup intern (Sweeps/Fetch/Forward) | `account.py` | Sweeps + Erst-Fetch in try/except; nur `async_forward_entry_setups` darf HA-Fehler durchreichen |
| Domain-`async_setup` schlägt fehl | — | `return True` immer, `ensure` ist gekapselt → Domain-Setup kann nicht scheitern |

Registrierung im `hass.data` (einheitlich, damit Diagnostics/Services greifen):
`hass.data[DOMAIN][entry.entry_id] = AccountCoordinator` **und** `hass.data[DOMAIN][DATA_ACCOUNT_KEY] = <derselbe>`.
Alle Iterationen über Workspace-Coordinatoren laufen über `workspace_coordinators(hass)` (`isinstance`-Filter),
nie über Schlüsselkonventionen.

---

## 8. Testplan

### 8.1 Strategie (drei Ebenen, wie im Repo etabliert)
1. **HA-freie Unit-Tests** gegen `account_logic.py` / `catalog.py` / `api.py` (kein `homeassistant`-Import nötig).
2. **Registry-/Coordinator-Fakes** für alle HA-berührenden Pfade (`account.py`, `sensor.py`, `binary_sensor.py`,
   `switch.py`, `number.py`), inkl. echter Doppel für Entity-/Device-/Issue-Registry (in `tests/conftest.py` zentral).
3. **E2E auf der geteilten Instanz** (`/home/hermes/ha-test`) + **zweiter, ephemerer Container** mit der
   deklarierten Mindestversion 2026.8.0 (M4-Nachweis).

Konvention: `pytest.ini` bleibt `-p no:homeassistant`; HA-Stubs ausschließlich in `conftest.py` (kein Modul-eigenes
`sys.modules`-Setup in neuen Dateien). Test-Tags referenzieren die Findings: `[PLAN2-B1]` … `[PLAN2-M8]`.

### 8.2 `tests/conftest.py` — Erweiterungen (Voraussetzung für alles Weitere)
| Ergänzung | Zweck |
|---|---|
| `_FAKE_HA_MODULES += ["homeassistant.helpers.issue_registry", "homeassistant.helpers.device_registry"]` | neue Importe der Integration |
| `class FakeEntityRegistry` (`entities`-Dict, `async_get`, `async_get_entity_id`, `async_update_entity`, `async_remove`, `async_migrate_entries`) | Adoption + Alt-Settings-Entfernung ohne echte HA-Registry |
| `class FakeDeviceRegistry` (`devices`-Dict, `async_get_device`, `async_update_device(device_id, new_config_entry_id=...)`, `async_remove_device`, `async_entries_for_device`) + `DeviceEntry(config_entries: set, identifiers: set, id)` | Device-Move/Duplikat-Merge + Leer-Prüfung |
| `class FakeIssueRegistry` (Sammel-Liste von `async_create_issue`-Aufrufen, `async_delete_issue`) | Repair-Issue-Assertions ohne HA |
| `DataUpdateCoordinator`-Stub erweitern: `update_interval`-Setter, `_schedule_refresh()`, `async_refresh()`, `async_update_listeners()` (Calls zählbar) | Publish-Tick + Live-Wirkung ohne Reload |
| Timer-Stub (`async_track_time_interval` ersetzbar, Unsub zählbar) | Fetch-Timer: Start, Reschedule, Cancel |
| `RegistryEntryDisabler`-Stub (`USER`) in `homeassistant.helpers.entity_registry` | Verlierer-Pfad-Assertion |
| `StrictConfigEntry` unverändert wiederverwenden (`_MANAGED_CONFIG_ENTRY_ATTRS`) | v8-Bump muss über `async_update_entry` laufen |

### 8.3 Abdeckung der Review-Lücken (Mapping Pflicht → Test)

| Review-Lücke | Test | Assertion |
|---|---|---|
| **kein Besitzerwechsel / keine Übergabe** (B2, B3, M1) | `test_account_lifecycle.py::test_workspace_unload_keeps_account_coordinator`, `::test_no_owner_keys_in_domain_data` | Coordinator-Objekt-Identität vor/nach Unload gleich; kein Owner-Schlüssel in `hass.data[DOMAIN]`; `async_reload` nie aufgerufen |
| **Adoption idempotent** (B4, E7) | `test_account_adoption.py::test_second_pass_writes_nothing`, `::test_sweep_covers_all_five_kinds`, `::test_entity_with_config_entry_none_is_adopted` | Callcount `async_update_entity`/`async_update_device`/`async_remove` == 0 im 2. Lauf; 5 Kandidaten; `config_entry_id=None`-Fall wird gezogen |
| **Adoptionsreihenfolge Entities-vor-Device** (Device-Strategie) | `test_account_adoption.py::test_entities_moved_before_device_move` | Mock-Call-Reihenfolge: alle `async_update_entity`-Calls vor dem `async_update_device(new_config_entry_id=...)`; Device-Move nie bei noch zugeordneten Entities |
| **Adoption reihenfolgeunabhängig** (E7) | `test_account_adoption.py::test_permutation_order_same_result` (`itertools.permutations` über die 5 Kinds, je ein frisches Registry-Setup) | Endzustand (unique_ids, config_entry_ids, device_id) in allen 120 Permutationen identisch |
| **Kollision + Repair** (E7, M5) | `::test_collision_deterministic_winner`, `::test_collision_loser_disabled_not_removed`, `::test_collision_creates_repair_issue`, `::test_device_duplicate_merged_and_reported` | Gewinner = kleinste `entity_id`; Verlierer `disabled_by == USER`, `async_remove` nie; `FakeIssueRegistry` enthält `account_entity_collision_<kind>`; leeres Verlierer-Device `async_remove_device` + `account_device_merged` |
| **Duplikat-Device nur wenn leer** (Device-Strategie) | `::test_device_duplicate_not_empty_keeps_device_and_reports` | Device mit verbleibender Entity/Child-Device bleibt; Issue `account_device_not_empty`; kein `async_remove_device` |
| **Konto-Entry löschen** (E2) | `test_account_lifecycle.py::test_remove_entry_deletes_issues`, `::test_workspace_setup_recreates_account_after_delete`, `::test_entities_keep_entity_id_after_readoption` | Issue-Registry leer; `async_entries` == 1 nach erneutem Workspace-Setup; `entity_id` vor/nach identisch (Registry-Fake mit `new_unique_id`-Semantik) |
| **Konto-Entry deaktivieren** (E2) | `::test_disabled_account_is_not_recreated`, `::test_disabled_account_reflected_in_workspace_diagnostics` | `flow.async_init` nie aufgerufen; `account.loaded is False` im Diagnostics-Dict |
| **Reachability-TTL** (E5, M2) | `test_account_logic.py::test_matrix_*` (4 Zeilen der Matrix), `::test_stale_evidence_ignored`, `::test_ttl_uses_base_interval_not_backoff`, `test_binary_sensor_availability.py::test_api_reachable_unknown_without_evidence`, `::test_api_reachable_off_when_all_fresh_failures`, `::test_api_reachable_on_from_fresh_models_only`, `::test_api_reachable_neutral_no_subscription_is_unknown` | Zustand exakt `True/False/None` + `reason`-String; Grenzfall `age == ttl` ist frisch; TTL folgt dem Basisintervall auch im Backoff |
| **Konto-Settings** (E4) | `test_account_settings.py::test_defaults_on_and_60`, `::test_switch_turn_off_sets_auto_fetch_false`, `::test_switch_turn_on_sets_auto_fetch_true`, `::test_number_sets_base_interval`, `::test_number_bounds_1_1440`, `::test_setter_persists_options`, `::test_setter_does_not_reload`, `::test_options_flow_applies_live`, `::test_account_platforms_include_switch_and_number` | Defaults ON/60; `coordinator.auto_fetch`/`base_interval_minutes` geändert; `entry.options[CONF_*]` gesetzt; `async_reload`/`add_update_listener`-Aufruf == 0; `ACCOUNT_PLATFORMS == ["sensor","binary_sensor","button","switch","number"]` |
| **Alt-Settings-Entities entfernt** (E4, Breaking) | `test_account_settings.py::test_legacy_workspace_model_settings_removed`, `::test_removal_is_idempotent`, `::test_workspace_platforms_do_not_recreate_model_settings` | Registry-Einträge `*_auto_update_models`/`*_models_refresh_minutes` weg, `account_*` bleibt; 2. Lauf 0 `async_remove`; `switch.async_setup_entry`/`number.async_setup_entry` im Workspace-Branch erzeugen die beiden Klassen nicht |
| **Backoff** (E4) | `test_account_coordinator.py::test_backoff_doubles_from_base`, `::test_backoff_caps_at_60`, `::test_backoff_skipped_above_60`, `::test_backoff_resets_on_success` | 10→20→40→60→60; 60→60; 120→120; Erfolg → 10 |
| **Publish-Tick ohne HTTP** (E4, M2) | `test_account_coordinator.py::test_publish_tick_does_not_fetch` | `_async_update_data` ruft den API-Client nie; `update_interval == 5 min`; Snapshot aktualisiert |
| **models_block-Konsumenten** (M3) | `test_coordinator_parsing.py::test_snapshot_has_no_models_keys`, `test_diagnostics.py::test_workspace_diag_has_account_block`, `test_button_split.py::test_workspace_refresh_does_not_touch_catalog`, `test_sensor_logic.py::*_reads_account_coordinator` | Workspace-Snapshot ohne `models_block`/`last_models_fetch`; Diagnostics ohne `models_block_present`; Workspace-Button setzt `last_models_*` nie |
| **HA-Min 2026.8.0** (M4) | `test_ha_compat.py::test_declared_min_version_consistent`, `::test_no_deprecated_device_transfer_kwargs`, `::test_account_coordinator_has_no_config_entry`, `::test_transfer_typeerror_creates_issue_and_setup_stays_true` | `hacs.json.homeassistant == "2026.8.0"` == README-Badge **und** `docs/dashboards/README.md`; `add_config_entry_id`/`remove_config_entry_id` nirgends im Quelltext; kein `config_entry=` in `account.py`; `TypeError`-Simulation → Setup `True` + `adoption_unsupported` |
| **Account-Startup/Fehlerisolation** (M5) | `test_account_lifecycle.py::test_setup_survives_fetch_error`, `::test_setup_survives_adoption_exception`, `::test_no_first_refresh_call`, `test_account_coordinator.py::test_update_never_raises_auth_failed` | Setup `True`, Coordinator `last_update_success is False`, `async_config_entry_first_refresh` nie aufgerufen, kein `ConfigEntryAuthFailed` |

### 8.4 Neue Testdateien im Detail

**`tests/test_account_logic.py`** (HA-frei)
`canonical_unique_id`, `kind_for_unique_id` (canonical + Legacy + Fremd-`unique_id` → `None`, Längen-Priorität),
`matches_kind`, `models_ttl` (Basis 60 → 120 min, `None` → 120 min), `usage_ttl` (10 → 30 min),
`resolve_reachability` (alle 4 Matrix-Zeilen + Stale + leere Sequenz).

**`tests/test_account_adoption.py`**
Fakes aus 8.2; Fälle: Standard-Adoption (5 Entities eines Alt-Owners), teilweise adoptiert, doppelt vorhanden,
`config_entry_id=None`, `entity_id=None` (deaktivierter Registry-Eintrag → skip), Kollision canonical↔Legacy,
Reihenfolge Entities-vor-Device, Device-Move ohne Kandidaten (→ `device_moved is None`, HA legt beim Forward an),
mehrere Konto-Devices (Merge), nicht-leeres Duplikat-Device (Repair statt Löschen), `TypeError` aus
`async_update_device` (Guard), `ValueError` aus `async_update_entity` (→ `report.errors`, kein Raise).

**`tests/test_account_lifecycle.py`**
`async_setup` → `ensure` (anlegen / laden / deaktiviert / kein Workspace), `async_setup_account_entry`
(Reihenfolge: Registry-Sweeps **vor** `async_forward_entry_setups` — Assertion über Aufruf-Reihenfolge im Mock),
Erst-Fetch nur bei `models_block is None`, Fetch-Timer Start/Reschedule/Cancel, `async_unload_account_entry`,
`async_remove_account_entry`, Workspace-Setup/Unload, Konto-Löschung + Re-Create, Deaktivierung.

**`tests/test_account_coordinator.py`**
`update_interval == 5 min` und kein HTTP im Tick; Fetch-Timer im Basisintervall; `set_fetch_interval(10)` →
Timer 10 min, `set_auto_fetch(False)` → kein Timer und `fetch_interval_minutes is None`; `async_fetch_now`
erzwingt; Backoff-Fälle (§8.3); `last_models_outcome` `ok`/`fail`; `_snapshot`-Schlüssel exakt; kein
`config_entry`-Attribut.

**`tests/test_account_flow.py`**
`async_step_import` erzeugt Entry mit `title == "Go Gauge Konto"`, `data == {"entry_kind": "account"}`,
`unique_id == "go_gauge_account"`, kein `token` im Data-Dict; zweiter Aufruf → Abort `already_configured`;
`GoGaugeConfigFlow.VERSION == 8`; Konto-Options-Flow zeigt beide Felder und persistiert; `async_step_user`
schreibt `entry_kind == "workspace"`; Workspace-Options-Schema enthält die Modell-Felder nicht mehr.

**`tests/test_account_settings.py`** (ersetzt den v2-Entwurf `test_account_settings_aggregation.py`)
Switch/Number: Defaults ON/60, Grenzen 1–1440, Setter → `AccountCoordinator` + `entry.options`, kein Reload,
Options-Flow live; `ACCOUNT_PLATFORMS`; Entfernen der Alt-Settings-Entities inkl. Idempotenz; Workspace-Plattformen
erzeugen `AutoUpdateModelsSwitch`/`ModelsRefreshMinutesNumber` nicht mehr.

**`tests/test_button_split.py`**
`button.async_setup_entry` Konto-Zweig → genau `AccountRefreshButton` mit `unique_id == "account_refresh"`;
Workspace-Zweig → genau `RefreshButton`, `async_press` setzt nur `last_usage_fetch=None`;
`AccountRefreshButton.async_press` ruft `async_fetch_now()` (kein Usage-Fetch).

**`tests/test_ha_compat.py`**
Quelltext-/JSON-Guards aus 8.3 + `homeassistant`-Mindestversion vs. Doku; `manifest.version == "2.0.0"`;
`config_flow.VERSION == 8`; `check_version_sync`-Konvention (Tag `vX.Y.Z` ↔ Manifest) als Vorwärtstest.

### 8.5 Geänderte bestehende Tests
| Datei | Änderung |
|---|---|
| `tests/test_init.py` | `TestAsyncSetupEntryLogic`/`TestAsyncUnloadEntryLogic`: alle Owner-Tests **entfernen**, ersetzen durch Kind-Branching; `test_migrate_future_version_rejected` → Version 9; alle „→ 7"-Erwartungen → 8; neuer `test_migrate_v7_to_v8_sets_entry_kind` (keine Registry-Arbeit); `test_async_setup_ensures_account`; `test_account_entry_has_no_update_listener` |
| `tests/test_config_flow.py` | `VERSION == 8`; `test_user_flow_writes_entry_kind`; Import-Flow-Tests (siehe 8.4); Workspace-Options ohne Modell-Felder |
| `tests/test_reauth_flow.py` | Owner-Fixture entfernen (Attribut existiert nicht mehr) |
| `tests/test_runtime_settings.py` | Models-Setter-Tests entfernen; Konto-Setter (Switch/Number) → `AccountCoordinator` + `entry.options`, kein Reload; Usage-Setter unverändert |
| `tests/test_diagnostics.py` | `models_block_present`/Owner-Feld → `account`-Block (inkl. `auto_update_models`, `models_refresh_minutes`, `backoff_active`); Konto-Zweig ohne Token-Fingerprint |
| `tests/test_sensor_wiring.py` | Konto-Branch (4 Sensoren am `AccountCoordinator`), Workspace-Branch ohne Katalog |
| `tests/test_sensor_logic.py` | Katalog-Sensoren gegen `AccountCoordinator.data`; `available is True` bei leerem `models_block`; Konto-Settings-Attribute statt der v2-Attribute |
| `tests/test_binary_sensor_availability.py` | `ApiReachableBinarySensor` evidence-basiert (statt `last_update_success`+`fetched_at`) |
| `tests/test_api_status_and_subscription.py` | Fixture ohne Owner-Flag; `ApiReachable`-Fälle ziehen in die neuen Tests um |
| `tests/test_coordinator_parsing.py` | bleibt grün via Re-Export; ergänzt `test_snapshot_has_no_models_keys` |
| `tests/test_derived_usage.py`, `test_entity_ws_lookup.py` | `models_block: None` aus Fixtures entfernen (toter Schlüssel) |
| `tests/manual_offline_smoke.py` | Smoke-Pfad auf `AccountCoordinator`-Snapshot umstellen (Katalog + Reachability) |
| `tests/test_ci_scripts.py` | `check_version_sync` mit `TAG_NAME=v2.0.0` / Manifest `2.0.0` |

### 8.6 E2E-Matrix (Mindest- vs. Aktuell-Version)
| Lauf | Umgebung | Pflichtschritte | Abbruchkriterium |
|---|---|---|---|
| **Min-Version** | ephemerer Docker-Container mit **2026.8.0** | install → 1 Workspace → Konto-Entry entsteht → 5 Konto-Entities + Button + Switch + Number am Konto-Device → keine `ERROR`-Zeile | Abbruch, wenn Konto-Device unter dem Workspace-Device entsteht, eine Entity fehlt oder Adoption `TypeError` loggt |
| **v7→v8-Upgrade** | Kopie der geteilten Instanz mit v1.7.0 + 2 Workspaces (danach `bin/reset` **nur nach Abstimmung**) | `bin/sync` → Neustart → prüfen: 5 Entities behalten `entity_id`/History/Area/Labels, hängen am Konto-Device, `unique_id` = `account_*`; genau 1 Konto-Device; alte Workspace-Regler `auto_update_models`/`models_refresh_minutes` sind weg; 0 offene Repair-Issues | jeder `MIGRATION_ERROR`, jede `ValueError`-Zeile, Duplikat-Device, verbliebene Alt-Regler |
| **Aktuell** | `/home/hermes/ha-test` | Adoptionswiederholung (2. Neustart) → 0 Registry-Änderungen im Log; Konto löschen → Re-Create beim nächsten Reload; deaktivieren → Entities `unavailable`, kein Re-Enable; Switch aus → kein Katalog-Fetch mehr (Log), Switch an → Fetch im Intervall | Re-Create trotz `disabled_by`, Doppel-Entry, Fetch trotz Auto-Refresh OFF |
| **Sprach-/Slug-Check** | de-Systemsprache | `entity_id`-Slugs der übernommenen Entities unverändert (kein Re-Slug durch Device-Umzug) | `_2`-Suffix neu entstanden |

Protokoll: `docs/E2E-v8-account-split-<YYYY-MM-DD>.md` (Muster: `docs/E2E-v7-entity-id-migration-2026-09-12.md`).

### 8.7 DoD für Tests
`pytest` grün (heutiger Stand 426 Tests + ~70 neue, 0 skipped, 0 xfail), `ruff` + `mypy` sauber,
`scripts/check_json_consistency.py` sauber, Canon-Check 0 Fehler/0 Warnungen (nach Submodul-Release, §9).

---

## 9. Release-/Doku-/Canon-Änderungen

### 9.1 Repo-Dateien
| Datei | Änderung |
|---|---|
| `custom_components/go_gauge/manifest.json` | `"version": "2.0.0"` (Beta-Lauf: `"2.0.0b0"`, E9) |
| `hacs.json` | `"homeassistant": "2026.8.0"` — festgelegt (nicht mehr offen): ältere Versionen kennen `async_update_device(new_config_entry_id=...)` und die per-Entry-Identifier-Indizierung nicht |
| `CHANGELOG.md` | neuer Block `## [2.0.0] — <Datum>` mit Summary, **`### 💥 Breaking changes`** (Pflicht wegen MAJOR): (1) Entity-Umzug der 5 Konto-Entities auf die Konto-Entry, (2) `unique_id`-Normalisierung `account_*`, (3) HA-Mindestversion 2026.8.0, (4) Konto-Entry ist jetzt eine eigene Integrations-Instanz, (5) **Entfernen der alten Workspace-Settings-Entities `auto_update_models` / `models_refresh_minutes`** (Ersatz: Switch/Number auf der Konto-Entry) — je Punkt Migrationshinweis („einfach updaten — entity_id/History bleiben, keine Nutzeraktion" bzw. „globale Konto-Einstellungen bitte einmal prüfen") + `Added/Changed/Fixed/Verified` |
| `README.md` | Feature-Bullet „Konto-Instanz" (Modell-Katalog + Erreichbarkeit + Konto-Settings + eigener Katalog-Refresh); Abschnitt *Mehrere Workspaces*: Konto-Entry entsteht automatisch, nie manuell anlegen; Abschnitt *Geräte*: Konto-Device gehört der Konto-Entry; **Entitäten-Tabelle**: `button.go_gauge_refresh` = nur Usage, neu `button.go_gauge_konto_refresh` (Konto-Device), Konto-Zeilen mit Vermerk „Konto-Device", Konto-Settings-Zeilen (Switch/Number) statt Workspace-Modell-Reglern; HA-Badge `2024.6+` → `2026.8+`; Hinweis „Konto-Entry löschen/deaktivieren" mit Verhalten (Re-Create beim nächsten Setup / `unavailable`) |
| `docs/dashboards/entity-map.yaml` | `devices.account`: `catalog_owner_only: true` → `entry: "account"` + `owner_note`; alle fünf Konto-Entities: `catalog_owner_only` → `account_entry: true`, `unique_id_template: "account_<kind>"`; neuer Eintrag `logical: account_refresh`, `platform: button`, `device: account`, `unique_id_suffix: "account_refresh"`; neuer Eintrag `logical: account_auto_update_models` (`platform: switch`, `device: account`, `unique_id_suffix: "account_auto_update_models"`, Default ON); neuer Eintrag `logical: account_models_refresh_minutes` (`platform: number`, `device: account`, `unique_id_suffix: "account_models_refresh_minutes"`, min 1, max 1440, Default 60); die Workspace-Einträge `models_refresh_minutes` und `auto_update_models` **entfernen**; `refresh`-Eintrag: `note` „lädt NUR Usage"; `model_catalog.attributes` + `account_auto_update_models`, `account_models_refresh_minutes`, `account_fetch_interval_minutes`, `account_last_models_outcome`; `api_reachable`: `note` → Evidenz-/TTL-Regel + neue Attribute; Header-Kommentar auf 2.0.0 aktualisieren |
| `docs/dashboards/README.md` | HA-Mindestversion (Zeile „Home Assistant >= 2024.6.0") → 2026.8.0; Gotcha-Abschnitt `[[refresh_btn]]`: Konto-Button ist device-präfixiert (`button.go_gauge_konto_refresh`) → die bisherige `_2`/`_3`-Kollision betrifft nur noch Workspace-Buttons; Konto-Sektion um Settings-Entities ergänzen |
| `docs/dashboards/workspace-standard.yaml`, `-mushroom.yaml`, `-matrix.yaml` | Steuerungs-Sektion: Label des bestehenden Buttons → „Refresh (Usage)"; Konto-Sektion: neuer `button.press`-Tap auf `button.go_gauge_konto_refresh` (statt den Workspace-Button für Katalog-Refresh zu missbrauchen); `[[refresh_btn]]`-Doku-Kommentare an das neue Namensschema anpassen; keine strukturellen Umbauten |
| `docs/E2E-v8-account-split-<Datum>.md` | E2E-Protokoll §8.6 |
| `docs/AUDIT-…`/`CHANGELOG-DRAFT-1.0.0.md` | unverändert (historisch) |

### 9.2 Canon — **ausschließlich im Submodul-Repo** `external/ha-gauge-standard`
Kein Edit im Parent-Repo (Submodule-Schutzkonzept); Parent zieht nur den gepinnten Commit nach.

| Datei im Submodul | Änderung |
|---|---|
| `canon/entities.md` | Versionskopf auf **Canon v1.4**; §1 Device-Topologie: Konto-Device gehört einer eigenen Konto-ConfigEntry (`identifiers: {(DOMAIN,"account")}`, Name `Go Gauge Konto`) — die Formulierung „nur vom Catalog Owner" entfällt; §6 Katalog-Sensoren: `catalog_owner_only` → `account_entry`; neue Tabelle §8.3: **zwei** Buttons (`refresh` = Workspace/Usage, `refresh_account` = Konto/Katalog) mit Wortstellungs-/Namensregel; Konto-Settings (Switch + Number, 1–1440 min, Default ON/60) als Konto-Ebene; §5 `api_reachable`: Zustandsregel auf Evidenz+TTL umgestellt (ON/OFF/unknown-Matrix), `last_update_success`-Formulierung ersetzen; §11 OP-1 unverändert |
| `canon/entities.schema.json` | Felder `account_entry: bool`, `owner_entry: "account"`, Button-Liste (2 Einträge), Konto-Settings-Liste, `api_reachable.state_rule` als Text; Schema-Version bumpen |
| `matrix/parity.md` | go_gauge-Spalte auf 2.0.0; command_gauge-Zeilen unverändert; neuer Hinweis, dass der Konto-Split go_gauge-spezifisch ist (command_gauge: 1 Entry = 1 Konto, ⚠️ CG-D1 bleibt) |
| `scripts/check_canon.py` | Prüfung des zweiten Buttons, des `account_entry`-Flags und der Konto-Settings; ohne Anpassung meldet der Check die neuen go_gauge-Abweichungen |
| Release des Submoduls | eigener Tag (`v1.4.0`) **vor** dem Parent-Release; danach im Parent nur der gepinnte Commit (via `git`-Rolle) |

### 9.3 Release-Dreiklang (E9)
1. **optional Beta:** `manifest "2.0.0b0"` ↔ Tag `v2.0.0b0` ↔ GitHub-Release mit Pre-Release-Flag
   (HACS liefert das nur über `switch.<repo>_pre_release`).
2. **Stable:** `manifest "2.0.0"` ↔ Tag `v2.0.0` ↔ echtes Release (Summary, ✨ New features,
   💥 Breaking Changes mit Migrationshinweis, Full-Changelog-Link).
3. `scripts/check_version_sync.py` (Release-Event, `TAG_NAME`) muss grün sein; Tags/Releases sind immutable.
4. Reihenfolge zwingend: Submodul-Release → Canon-Check grün → Parent-Release → HACS-Update-Test + Alt-Entity-Cleanup
   auf der Dev-Instanz (nach dem Release, nie davor).

---

## 10. Umsetzungsreihenfolge (kleine, testbare Schritte)

Jeder Schritt endet mit grüner Suite + Secret-Scan und ist ein eigener Commit (Conventional Commits, Englisch,
≤72 Zeichen, `#agent-meta:agent=<role>` vor `git add`). Umsetzung auf einem Feature-Branch (`feat/account-entry`),
nicht auf `release/v1.7.0`/`main`.

| # | Schritt | Dateien | Grün-Kriterium |
|---|---|---|---|
| 1 | Reine Module extrahieren (Move, kein Verhalten) | `api.py`, `catalog.py`, `coordinator.py` (Re-Exports) | bestehende Suite unverändert grün (426) |
| 2 | HA-freie Konto-Regeln | `account_logic.py`, `const.py` | `test_account_logic.py` grün |
| 3 | Test-Infrastruktur | `tests/conftest.py` (Registry-/Device-/Issue-Fakes, Coordinator-/Timer-Stub) | Suite weiter grün, neue Fakes ungenutzt |
| 4 | Entry-Kind + v8-Bump + `async_setup` | `const.py`, `__init__.py`, `config_flow.py` (`VERSION=8`, `entry_kind` im User-Flow) | `test_init.py`, `test_config_flow.py` angepasst grün; v8-Migration nachweislich ohne Registry-Zugriff |
| 5 | Import-Flow (Konto, Singleton, kein Token) | `config_flow.py`, `strings.json`, `translations/*` | `test_account_flow.py` (Import-Teil) grün |
| 6 | `AccountCoordinator` + Publish-Tick + Fetch-Timer + Backoff + Setter | `account.py` | `test_account_coordinator.py` grün |
| 7 | Konto-Setup/Unload/Remove + `ensure` (ohne Sweeps) | `account.py`, `__init__.py` | `test_account_lifecycle.py` (Grundpfade) grün |
| 8 | Konto-Settings: Switch/Number + Options-Flow + `ACCOUNT_PLATFORMS` | `switch.py`, `number.py`, `config_flow.py`, `entity.py`, `strings.json`, `translations/*` | `test_account_settings.py` (Settings-Teil), `test_account_flow.py` (Options) grün |
| 9 | Alt-Settings-Entities entfernen + Workspace-Plattformen verschlanken | `account.py`, `switch.py`, `number.py` | `test_account_settings.py` (Removal/Idempotenz) grün; Workspace erzeugt die Alt-Klassen nicht mehr |
| 10 | Adoption: Entity-Sweep (`unique_id` → `config_entry` → `device`) | `account.py` | `test_account_adoption.py::test_entity_*` inkl. Permutationen grün |
| 11 | Duplikat-Devices + Device-Move (**nach** Entity-Sweep) | `account.py` | `test_account_adoption.py::test_device_*`, `::test_entities_moved_before_device_move` grün |
| 12 | Kollision + Repair-Issues | `account.py`, `strings.json`, `translations/*` | `test_account_adoption.py::test_collision_*`, `::test_repair_*` grün |
| 13 | Workspace schlanken (Models-Zyklus raus, Evidenz-Timestamps) | `coordinator.py`, `__init__.py` | `test_init.py` (ohne Owner), `test_coordinator_parsing.py`, `test_derived_usage.py` grün |
| 14 | Plattformen umhängen (Konto-Branch) | `entity.py`, `sensor.py`, `binary_sensor.py`, `button.py` | `test_sensor_wiring.py`, `test_sensor_logic.py`, `test_button_split.py`, `test_binary_sensor_availability.py` grün |
| 15 | Diagnostics + Konsumenten-Sweep | `diagnostics.py`, Rest-Greps auf `models_block` und Owner-Reste | `test_diagnostics.py` grün; `grep -rn "models_block"` liefert nur `account.py`/`catalog.py`/Tests; Owner-Reste: 0 Treffer |
| 16 | Doku/Canon/Release-Vorbereitung | §9-Dateien | `pytest` + `ruff` + `mypy` + `check_json_consistency` + Canon-Check grün; E2E-Matrix §8.6 protokolliert |

Reihenfolge-Begründung: Schritte 1–3 sind verhaltensneutral (jederzeit mergebar), 4–12 bauen die neue Entry
additiv auf (v1.x-Verhalten bleibt bis Schritt 13 funktionsfähig), 13–15 schalten den alten Pfad ab, 16 ist
Wahrheit/Distribution. Schritt 8/9 liegen bewusst **vor** dem Plattform-Umhängen (14), damit die Konto-Plattformen
beim Umhängen bereits Switch und Number enthalten und die Alt-Regler zu diesem Zeitpunkt schon aus der Registry
verschwunden sind. Kein Zwischenschritt erzeugt einen Zustand, in dem Konto-Entities doppelt oder gar nicht
existieren: bis Schritt 14 kommen sie weiterhin vom Workspace-Owner, danach ausschließlich von der Konto-Entry.

---

## 11. Restrisiken, akzeptierte Risiken und Festlegungen

### 11.1 Akzeptierte Risiken

| # | Risiko | Wirkung | Umgang / Begründung |
|---|---|---|---|
| R1 | HA-Mindestversion 2026.8.0 reicht für `new_config_entry_id` / `config_entry_id` nicht aus | Adoption schlägt auf der Min-Version fehl | Guard `_entry_transfer_supported` + `TypeError` → Repair `adoption_unsupported`, Integration läuft weiter; Min-Version im E2E-Container (§8.6) verifiziert — bei Abweichung Version anheben |
| R3 | Konto-Entry löschen + Neuanlage: Registry-Einträge waren weg → `entity_id` kann bei vorher deduplizierten Slugs (`_2`) neu vergeben werden | einzelne `entity_id` ändert sich | **akzeptiert**: Löschen der Konto-Entry ist eine explizite Nutzeraktion; History bleibt in der Recorder-DB, Bindung je `entity_id` ggf. neu; in README/CHANGELOG benannt |
| R4 | Übergangsfenster beim ersten v8-Start: 5 Entities kurz `unavailable` (Registry-Eintrag ohne Platform-Instanz) | einmalige UI-Flackerei, keine Datenkorruption | **akzeptiert**: Reihenfolge fixiert (Registry-Arbeit vor Forward); im CHANGELOG als erwartetes Verhalten benannt |
| R5 | Duplikat-Legacy-Entities (zwei Entities pro Kind): Verlierer wird nur deaktiviert, nicht gelöscht | Alt-Eintrag bleibt sichtbar (deaktiviert) | **akzeptiert**: Repair-Issue `account_entity_collision_<kind>` nennt `winner`/`losers`; Löschen bleibt Nutzerentscheidung (nicht destruktiv automatisieren) |
| R6 | Adoption liest Registry, während HA parallel eine Workspace-Entry entfernt | einzelnes `KeyError`/`ValueError` im Sweep | **akzeptiert**: pro Entity gekapselt → `report.errors` + Log; nächster Start wiederholt idempotent; kein `MIGRATION_ERROR` |
| R7 | Publish-Tick (5 min) = bis zu 5 min verzögerte Reachability-Anzeige; zusätzliche State-Writes | kosmetische Trägheit, minimale Recorder-Last | **akzeptiert**: 5 min ist die Obergrenze der Anzeigeträgheit, kein HTTP im Tick |
| R9 | Workspace mit `auto_update_usage = false` pollt gar nicht mehr (`update_interval=None` statt 24 h) | Sensoren zeigen letzten Stand bis Button/Service | **akzeptiert** (gewollt, weniger Last); im CHANGELOG unter *Changed* mit Begründung |

### 11.2 Festlegungen (vormals offen — nicht erneut aufmachen)

| Thema | Festlegung |
|---|---|
| Konto-Fetch-Intervall | einstellbar **1–1440 min**, Default 60, Auto-Refresh Default ON; kein festes 5-Minuten-Limit (Publish-Tick ≠ Fetch) |
| Rate-Limit-Schutz | exponentieller Backoff bei 403/Netzfehler (§6.3), Deckel 60 min, Reset bei Erfolg — ersetzt die v2-Idee einer harten Untergrenze |
| HA-Mindestversion | 2026.8.0 (§2.5/§9.1), Guard bleibt zusätzlich bestehen |
| Konto-Settings-Ort | Options der Konto-Entry; Switch/Number wirken live ohne Reload (§6.4) |
| Alt-Settings-Entities | werden entfernt, nicht migriert (§4.7) — Breaking, in den Release-Notes genannt |

### 11.3 Implementierungs-Verifikation (keine offene Annahme)

| # | Zu verifizieren | Wie |
|---|---|---|
| V1 | `flow.async_init(..., source=import)` läuft bis zum Ergebnis durch | `test_account_lifecycle.py::test_ensure_creates_entry_once` (Entry-Anzahl nach dem `await`) |
| V2 | `async_update_device(new_config_entry_id=...)` verhält sich auf 2026.8.0 wie erwartet (Device wandert, Entities bleiben) | E2E Min-Version (§8.6) + `test_account_adoption.py::test_entities_moved_before_device_move` |

### 11.4 Nicht-blockierende Notizen

- **O2 — Canon-Zweitintegration.** Ob `command_gauge` den Konto-Split übernimmt, ist dortige Produktentscheidung
  (dort gilt 1 Entry = 1 Konto, ⚠️ CG-D1). Keine Auswirkung auf 2.0.0.
- **O3 — Object-ID-Pinning** (Canon OP-1) bleibt wie gehabt koordiniert offen; 2.0.0 ändert keine `entity_id`s
  bestehender Entities (Adoption ist `unique_id`-seitig), verschärft O3 also nicht.

---

## Status

**Datum:** 2026-10-25

**Entscheidungsstand:**
- E1–E9 sind entschieden und in diesem Dokument durchgängig umgesetzt.
- **E4 wurde gegenüber Plan v2 geändert:** `auto_update_models` und `models_refresh_minutes` liegen global auf der
  Konto-Entry (Switch + Number), der Konto-Options-Flow ist Pflicht, die alten Workspace-Modell-Regler werden
  entfernt, und der Konto-Fetch folgt dem konfigurierten Basisintervall mit Backoff (kein 5-Minuten-Limit).
- **Device-Reihenfolge korrigiert:** erst Entities umziehen, dann das Device per
  `async_update_device(new_config_entry_id=...)` verschieben; Duplikat-Devices nur entfernen, wenn leer.
- **HA-Mindestversion 2026.8.0 festgelegt** (ehemalige offene Annahme aufgelöst, Guard bleibt).

**Abweichungen bei der Umsetzung:**
- **Konto-Button-Key `refresh` statt `refresh_account`** — Canon v1.4.0 kennt nur `refresh`;
  Workspace- und Konto-Button unterscheiden sich über das Device, `unique_id` bleibt
  `account_refresh`.
- **Konto-Options werden beim ersten Setup einmalig aus der ersten Workspace-Entry
  übernommen** (Nutzerentscheidung „Migration ohne Datenverlust", abweichend von §4.7).
- **`account.py`-Aufteilung** (Konto-Entry, Reachability, Settings in eigene Module) ist als
  2.1.0-Follow-up vertagt, nicht Teil von 2.0.0.
- **Adoptions-Teilfehler ⇒ kein Device-Move/Forward**, Setup liefert `False`/`SETUP_RETRY`
  (Datenverlust-Schutz statt Teil-Migration).

**Review-Historie:** Plan v1 → `CHANGES_REQUESTED` (5 Blocker, 8 Major) → Plan v2 → Finalentscheidungen E1–E9
(dieses Dokument).

**Nicht Teil dieses Plans:** Code-Änderungen, Commits, Pushes, Branch-Wechsel.
