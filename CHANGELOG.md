# Changelog

## [1.5.0] - 2026-09-12

> **SemVer note:** this is released as **1.5.0 (MINOR)** by explicit maintainer
> decision. Strictly, renaming entity IDs is a user-visible breaking change (per
> this project's rule that entity/naming changes are always MAJOR); the breaking
> change is therefore called out explicitly below, including the required user
> action. `v1.5.0` ↔ `manifest.json` `"version": "1.5.0"`.

### Summary

Breaking entity-ID release. Existing entities get their **`entity_id` slug
renamed from German to English** through a Home Assistant Entity-Registry
migration (config-entry schema version 5 → 7; the v7 step does the slug
rename). Only the slug changes:
`unique_id` is never touched, so the entity — including its device assignment,
history and long-term statistics — stays the same. In the same change, entity
display names move from hardcoded `_attr_name` values to
`has_entity_name` + `translation_key` lookups (English master `strings.json`,
German overlay `translations/de.json`).

Because automations, scripts, dashboards, templates and any other YAML/UI
reference entities by their `entity_id`, this release requires user action for
setups that reference the affected Go Gauge entities by their old German slugs.

### Changed

- Entity display names are now resolved from `translation_key` translations:
  `has_entity_name = True` on the shared base `GoGaugeEntityBase`, English
  master `strings.json` and German overlay `translations/de.json`. No platform
  class sets `_attr_name` anymore (it would short-circuit the translation
  lookup); the device name (`Go Gauge <workspace>`) is prefixed by HA.
- Config-entry schema `VERSION` bumps from 5 to 7. `async_migrate_entry()` runs
  automatically on the next Home Assistant restart and is idempotent:
  - **v6** rewrites legacy German `original_name` values to the canonical
    English names.
  - **v7** rewrites legacy German `entity_id` slugs to the mapped English
    slugs via the Entity Registry (`new_entity_id`); `unique_id` stays stable.

### Breaking Changes

**Entity-ID slug migration German → English (automatic rename, manual follow-up in your configs)**

- **What changed:** every entity that was registered under a legacy German
  slug is renamed in the entity registry. A `sensor.go_gauge_team_monthly_nutzung`
  becomes `sensor.go_gauge_team_monthly_usage`, and so on.
- **What stays the same:** the entity itself. The migration leaves `unique_id`
  untouched, so the entity keeps its device assignment, its history and its
  long-term statistics — no new/duplicate entity is created and no history is
  lost.
- **What you must do:** update every reference to an **old** Go Gauge
  `entity_id` in
  - **automations** (triggers, conditions, actions),
  - **scripts**,
  - **dashboards / Lovelace cards** (and any UI entity pickers that stored the
    old ID),
  - **templates** (`states('sensor.…')`, `state_attr(...)`, template sensors),
  - **helpers / groups / scenes / other integrations** that point at the entity.
- **Automatic, not optional:** the rename runs on the next Home Assistant
  restart. There is no separate manual migration step and no way to keep the old
  slug through a config option.
- **Already-English entities are unaffected:** `reset`, `pace`, `burn_rate` and
  `rate_limited` were never German and are intentionally not part of the
  mapping.

Affected slug mapping (legacy German `entity_id` → new English `entity_id`):

| Legacy (migrated from) | New (migrated to) |
|---|---|
| `sensor.go_gauge_<ws>_<window>_nutzung` | `sensor.go_gauge_<ws>_<window>_usage` |
| `sensor.go_gauge_<ws>_<window>_prognose` | `sensor.go_gauge_<ws>_<window>_forecast` |
| `sensor.go_gauge_<ws>_<window>_restbudget` | `sensor.go_gauge_<ws>_<window>_remaining` |
| `sensor.go_gauge_<ws>_<window>_restzeit` | `sensor.go_gauge_<ws>_<window>_time_to_reset` |
| `sensor.go_gauge_modelle` | `sensor.go_gauge_models` |
| `sensor.go_gauge_live_modelle` | `sensor.go_gauge_live_models` |
| `sensor.go_gauge_gunstigstes_modell` | `sensor.go_gauge_cheapest_model` |
| `sensor.go_gauge_free_modelle` | `sensor.go_gauge_free_models` |
| `binary_sensor.go_gauge_<ws>_abo_aktiv` | `binary_sensor.go_gauge_<ws>_subscription_active` |
| `binary_sensor.go_gauge_api_erreichbar` | `binary_sensor.go_gauge_api_reachable` |
| `button.go_gauge_aktualisieren` | `button.go_gauge_refresh` |
| `number.go_gauge_warnschwelle_<ws>` | `number.go_gauge_warning_threshold_<ws>` |
| `number.go_gauge_ampel_rot_grenze_<ws>` | `number.go_gauge_pace_red_limit_<ws>` |
| `number.go_gauge_nutzung_refresh_minuten_<ws>` | `number.go_gauge_usage_refresh_min_<ws>` |
| `number.go_gauge_modelle_refresh_minuten_<ws>` | `number.go_gauge_models_refresh_min_<ws>` |
| `switch.go_gauge_<ws>_nutzung_auto_update` | `switch.go_gauge_<ws>_auto_update_usage` |
| `switch.go_gauge_<ws>_modelle_auto_update` | `switch.go_gauge_<ws>_auto_update_models` |

`<ws>` is the workspace slug derived from the configured workspace name and
`<window>` is one of `5h_rolling`, `weekly` or `monthly`.

### Migration target vs. fresh-install slug (important)

The v7 migration target follows the explicit **legacy → English mapping** in the
table above. It is a position-preserving phrase replacement of the legacy
object_id: the workspace/device prefix and the position of the entity-specific
token are kept, only the German phrase is translated
(`number.go_gauge_warnschwelle_<ws>` → `number.go_gauge_warning_threshold_<ws>`,
`switch.go_gauge_<ws>_nutzung_auto_update` → `switch.go_gauge_<ws>_auto_update_usage`).

This means the migrated target is **not** always the slug Home Assistant would
derive for a **fresh install**. A fresh `entity_id` is composed from the active
translation's display name plus the device name, so for **10 entity classes** the
two intentionally differ:

| Entity | Migrated target (this release) | Fresh-install slug (English HA) |
|---|---|---|
| Model catalog | `sensor.go_gauge_models` | `sensor.go_gauge_konto_models` |
| Live models | `sensor.go_gauge_live_models` | `sensor.go_gauge_konto_live_models` |
| Cheapest model | `sensor.go_gauge_cheapest_model` | `sensor.go_gauge_konto_cheapest_model` |
| Free models | `sensor.go_gauge_free_models` | `sensor.go_gauge_konto_free_models` |
| API reachable | `binary_sensor.go_gauge_api_reachable` | `binary_sensor.go_gauge_konto_api_reachable` |
| Refresh button | `button.go_gauge_refresh` | `button.go_gauge_<ws>_refresh` |
| Warning threshold | `number.go_gauge_warning_threshold_<ws>` | `number.go_gauge_<ws>_warning_threshold` |
| Pace red limit | `number.go_gauge_pace_red_limit_<ws>` | `number.go_gauge_<ws>_pace_red_limit` |
| Usage refresh | `number.go_gauge_usage_refresh_min_<ws>` | `number.go_gauge_<ws>_usage_refresh_min` |
| Models refresh | `number.go_gauge_models_refresh_min_<ws>` | `number.go_gauge_<ws>_models_refresh_min` |

Reasons: the workspace-independent catalog/API entities live on the separate
"Go Gauge Konto" device (fresh slug gets the `go_gauge_konto` device prefix),
while `number`/`button` entities belong to a workspace device (a fresh slug puts
the workspace name before the entity name). The remaining entities
(`sensor.*` window sensors, `binary_sensor.*` subscription, `switch.*`) happen to
match the fresh-install slug.

**Upgraded install:** use the mapped targets from the table above — do **not**
assume a fresh install's slug. **Fresh install:** its entity IDs are created new
and are not part of this migration.

Also note a fresh install derives its slug from the **active translation** (a
German HA system yields German-derived slugs for the translated display names),
whereas the migration always applies the fixed English mapping regardless of the
HA system language.

Collision handling is deliberately strict: if the mapped target `entity_id` is
already occupied — in the entity registry **or** as a state — the rename for that
entity is **skipped** and a warning is logged, so the migration never aborts and
`unique_id` stays stable, at the cost of that one entity keeping its legacy
slug. Check the HA log for
`Go Gauge: entity_id … ist bereits belegt` after upgrading.

### Full Changelog

https://github.com/Popoboxxo/ha-go-gauge/compare/v1.4.0...v1.5.0

## [1.4.0] — 2026-09-10

### Summary

Adds three derived usage sensors per workspace window (5h/week/month) on top
of the existing usage/forecast/pace entities: the remaining budget percent,
the time until the window resets, and the current burn rate in %/h. All three
are computed on-read by pure kernel functions from the existing coordinator
data plus an in-memory usage-sample history — no new polling, no reset jobs.

### Added

- `RemainingBudgetSensor` per window: `100 − used percent` (`sensor.go_gauge_<ws>_<window>_restbudget`); `None` for `no_subscription`/`error` instead of a misleading full budget
- `TimeUntilResetSensor` per window: time to the window reset in hours as a HA `duration` entity, clamped at 0 (`..._restzeit`)
- `BurnRateSensor` per window: consumption slope in %/h derived from the usage-sample history (`..._burn_rate`)
- Coordinator records one `(timestamp, percent)` sample per workspace window per successful usage fetch; the history is fed to the burn-rate kernel on read

### Design decisions

- **Burn-rate lookback of 2 h.** Only samples from the last 2 hours feed the slope, and at least two samples spanning 5 minutes are required. This keeps the short 5h rolling window responsive while ignoring long-past consumption, and suppresses poll jitter below the 5-minute minimum span.
- **Window reset clears the sample history.** A percent drop for the same window means the window rolled over; the stored history is cleared before appending the new sample, so the burn-rate never reports a bogus negative slope after a reset. The pure kernel additionally returns `None` on a negative slope as a defensive fallback.

### Full Changelog

https://github.com/Popoboxxo/ha-go-gauge/compare/v1.3.0...v1.4.0

## [1.3.0] — 2026-09-07

### Added

- New "Go Gauge Konto" device for the workspace-independent entities (model catalog, live-model count, cheapest model, free models, API-reachable) — previously attached to the first workspace instance's device
- Forecast sensor per workspace window (5h/week/month): linear pace projection of the current usage percent onto the full window (can exceed 100%)
- Pace-status sensor per workspace window: green/yellow/red classification of the forecast, thresholds configurable via the existing "Warnschwelle" Number entity (green/yellow boundary) and a new "Ampel Rot-Grenze" Number entity (yellow/red boundary, default 100%)

### Fixed

- Every workspace device was hardcoded to the same name "Go Gauge HA" regardless of workspace — now shows "Go Gauge {workspace_name}"

### Full Changelog

https://github.com/Popoboxxo/ha-go-gauge/compare/v1.2.0...v1.3.0

## [1.2.0] — 2026-09-06

### Summary

Bug-fix release: the "Free-Modelle" sensor and the `cheapest_overall`
attribute could permanently show a model that the live API no longer lists
(`ox-alpha-free`), because the static `PRICING` table outranked live
availability. Free models, cheapest model and the cost ranking are now
computed only over models currently listed by the live API. No device or
entity changes — entity IDs, unique IDs and automations are unaffected.

### Fixed

- `free_models`, `cheapest_overall`, `cheapest_model` and the cost ranking
  only consider models currently listed by the live API; PRICING entries
  removed from the API stay visible in the catalog listing with
  `live: false` but can no longer occupy the Free/Günstigstes sensors
- Explicitly empty live model list now yields empty cheapest/free sensors
  instead of falling back to the static table; a missing live fetch
  (`live_ids=None`, e.g. first poll not completed) keeps the legacy fallback
- Catalog sensor attributes relay the coordinator-block values (including a
  new `live` flag per ranking entry) instead of re-computing over the full
  model list

### Full Changelog

https://github.com/Popoboxxo/ha-go-gauge/compare/v1.1.0...v1.2.0

## [1.1.0] — 2026-09-05

### Summary

Maintenance release without integration code changes: the agent-meta project
configuration now documents the shared headless Home Assistant test instance
on this server (endpoints, token location, sync/reset etiquette) and ships
concrete E2E commands against it, and the HACS platform layer points at the
local dev instance. The v1.0.0 release notes are additionally committed as
`CHANGELOG.md` at the repo root. Nothing changed inside
`custom_components/go_gauge/` — existing users do not need to update for
functionality reasons.

### Added

- Shared HA test instance documented in agent-meta `PROJECT_CONTEXT`
  (`/home/hermes/ha-test`, headless Docker HA, localhost-only) and
  `TEST_COMMANDS` (E2E sync via `bin/sync`, state inspection via
  `bin/ha GET /api/states`)
- HACS platform-config `dev_instance_url` pointing at the local dev instance
  (`http://127.0.0.1:8123`), clearing the sync `[WARN]` for the empty
  required field
- `CHANGELOG.md` at the repo root (v1.0.0 notes backfilled from the release
  draft in `docs/`)

### Full Changelog

https://github.com/Popoboxxo/ha-go-gauge/compare/v1.0.0...v1.1.0

## [1.0.0] — 2026-09-05

### Summary

This release concludes a comprehensive system audit triggered by a user report that entities had stopped reliably fetching data from the OpenCode-Go API. The audit found and fixed a sensor availability bug that masked failed data refreshes as "available" with stale data, removed a duplicate entity registration bug, added defensive logging for unexpected API response fields, and closed a token-leak risk in diagnostics and config-entry storage. Coordinator test coverage went from 0% to comprehensive (mocked unit tests + config-flow/init tests), and the config-entry `unique_id` scheme was migrated to a non-reversible hash. As v0.x was never officially published as stable, the transition to 1.0.0 reflects the SemVer consequence of this release's breaking change (unique_id schema migration), not a claim of production readiness — early adopters should still expect refinements.

### Added

- Coordinator unit tests with mock API data (`_pnum`, `efficiency`, `_parse_reset`, `build_models_block`, plus dedicated window field-mapping tests guarding against a v0.6.1-style argument/field-order regression)
- Config flow and `__init__.py` tests (27 tests: token validation, unique_id format, duplicate-entry detection, `async_migrate_entry` v1–v5, `async_setup_entry` catalog-owner logic, `async_unload_entry` cleanup)
- Sensor logic unit tests (21 mocked tests for `ModelCatalogSensor`, `UsagePercentSensor`, `ResetTimestampSensor`, `CheapestModelSensor`, `FreeModelsSensor`)
- Centralized, reusable Home Assistant stub registry in `tests/conftest.py` (fixed a gap where `config_flow.py`'s `from homeassistant import config_entries` attribute-style import wasn't resolved by the fake package, and `ConfigFlow` lacked a working `__init_subclass__(domain=...)`)
- `ModelCatalogSensor.extra_state_attributes` caching, keyed on the catalog's `models_updated_at`, avoiding a redundant dict copy and `json.dumps()` call on every attribute read when the catalog hasn't changed
- Type hints on all entity constructors across `sensor.py`, `binary_sensor.py`, `switch.py`, `button.py`, `number.py`
- Manual smoke-test harness for live API validation (`tests/manual_offline_smoke.py`, intentionally excluded from pytest auto-discovery — needs a real token and network access)

### Fixed

- **[Critical]** `RateLimitedBinarySensor.available` now respects the Coordinator's update success (`super().available`) in addition to the `no_subscription` case — previously stayed "available" and kept showing stale/wrong data after a failed refresh instead of surfacing the failure
- **[Critical]** Removed a duplicate entity registration: the catalog-owner branch in `sensor.py` was registering `WarnPercentNumber`/`UsageRefreshMinutesNumber`/`ModelsRefreshMinutesNumber`/`AutoUpdateUsageSwitch`/`AutoUpdateModelsSwitch` a second time over the wrong (`sensor`) platform, colliding with their proper registration in `number.py`/`switch.py`
- **[Critical]** Coordinator now logs an aggregated warning when expected usage-API response fields (`usage`, `status`, window blocks, `resetsAt`) are missing, instead of silently falling back to `None` — makes a possible API field-name drift visible instead of failing quietly (root-cause candidate for the reported "entities don't fetch clean data" issue; not verified against a live API response in this environment)
- Guarded `GoGaugeEntityBase._ws()` against a missing `key` field in a workspace entry (was a raw `ws["key"]` access that could raise `KeyError`)
- `config_flow._probe_token` now uses Home Assistant's shared HTTP client session (`async_get_clientsession`) instead of creating and leaking its own unmanaged `aiohttp.ClientSession`
- Diagnostics export: token fingerprint is now a non-reversible SHA-256 hash instead of the first 8 characters of the raw token; `workspace_name` is now consistently redacted via `async_redact_data`, matching its `REDACT_KEYS` declaration
- Removed a dead `_rename_workspace` branch in the options flow (referenced a field that never existed in any schema) and bundled the workspace-rename/options update into a single `async_update_entry` call, avoiding a redundant second reload
- Corrected an error-path note in the coordinator that implied a `fetched_at` timestamp not reliably available at that point in the flow

### Changed

- Config entry schema `VERSION` bumped from 4 to 5 (unique_id migration, see Breaking Changes)
- Test harness split: the previously pytest-invisible `tests/test_offline_logic.py` (no `test_*` function, so it was never actually collected) is now `tests/manual_offline_smoke.py` (documented manual-only harness) plus `tests/test_sensor_logic.py` (21 real, mocked pytest tests for the logic that used to only run manually)

### Removed

- Dead monitor-era constants (`CONF_HOST`, `CONF_PORT`, `DEFAULT_HOST` — including a hardcoded private IP, `DEFAULT_PORT`) and unused imports (`typing.Any` in `number.py`, an unused top-level `CONF_WORKSPACE_NAME` import and a redundant local re-import of `CONF_USAGE_REFRESH_MINUTES` in `__init__.py`)

### Breaking Changes

**ConfigEntry `unique_id` format migration (automatic, no user action required)**

The ConfigEntry `unique_id` changes from a 16-character raw token prefix to a SHA-256 hash (`custom_components/go_gauge/const.py::token_unique_id()`). This closes a security risk: token prefixes were previously stored in plaintext in HA's storage (`.storage/core.config_entries`) and could surface in support exports.

- **What changed:** ConfigEntry schema VERSION bumps from 4 to 5. Existing entries are automatically migrated on the next Home Assistant restart via `async_migrate_entry()` (v4 → v5 step).
- **What stays the same:** Entity `unique_id` values are unaffected (they are already entry_id-based, not derived from the token). Entity history and automations continue to work without modification.
- **Migration is idempotent:** the same token always produces the same SHA-256 hash; `entry.data` (token, workspace name) is left unchanged.
- **No manual step needed:** users do not need to reconfigure the integration; migration happens automatically.

**Version 1.0.0 disclaimer:** This integration was never officially released as production-stable while on the 0.x line. The bump to 1.0.0 is purely the SemVer consequence of the breaking change above (per this project's rule that unique_id/entity changes are always MAJOR), not an assertion of feature completeness or battle-tested status. Adopters should continue to expect refinements and edge-case fixes in upcoming releases.

### Full Changelog

https://github.com/Popoboxxo/ha-go-gauge/compare/v0.6.0...v1.0.0
