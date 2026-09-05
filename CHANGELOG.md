# Changelog

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
