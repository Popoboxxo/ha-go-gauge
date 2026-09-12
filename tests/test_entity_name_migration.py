#!/usr/bin/env python3
"""Tests for the v6 German -> English entity-name migration.

Covers the interface contract implemented by the developer:

- ``const.ENTITY_NAME_MIGRATION`` + ``const.migrate_entity_name``
  (ordered, most-specific-first legacy-German -> English fragment pairs)
- ``__init__._entity_name_migration_callback`` (registry-entry callback)
- ``__init__._async_migrate_entity_names`` (awaits
  ``homeassistant.helpers.entity_registry.async_migrate_entries`` defensively)
- ``async_migrate_entry`` targeting ConfigEntry version 7 (v6 name + v7 id steps)

These are offline unit tests: the ``homeassistant.*`` fakes (including
``homeassistant.helpers.entity_registry``, which ``__init__`` imports
function-locally) are installed centrally in ``tests/conftest.py``.

Implementation status: the migration described above is implemented and every
test in this file is green against it. Nothing here may be satisfied by
production code outside the contract above.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"

# Earlier-collected test modules replace parts of the homeassistant.* fake tree
# with their own local `_Flexible` instances. Re-asserting the shared stubs here
# also (re)wires homeassistant.helpers.entity_registry, which __init__ imports
# function-locally during the v6 migration.
conftest.install_ha_stubs()

# Coordinator-specific extension required to import go_gauge.coordinator (see
# tests/test_init.py for the same pattern - deliberately not centralized).
_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {
        "__class_getitem__": classmethod(lambda cls, item: cls),
        "__init__": lambda self, hass, logger, name=None, update_interval=None: None,
    },
)
_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})

# The fake registry module the production code's function-local import resolves
# against at call time; tests patch its `async_migrate_entries` attribute.
ENTITY_REGISTRY = sys.modules["homeassistant.helpers.entity_registry"]


def _load(name: str, path: str):
    """Dynamically import a module from file, bypassing sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load real modules in dependency order (const, coordinator, __init__).
const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
init_module = _load("go_gauge.__init__", str(BASE / "__init__.py"))


# Full legacy entity names and the exact English name each must migrate to.
# Longest/most-specific fragments must win (ordering guarantee), while the
# common fragment ("Nutzung", "Modelle") still applies to the plain cases.
FULL_NAME_CASES = [
    ("Go Gauge Work 5h rolling Nutzung", "Go Gauge Work 5h rolling Usage"),
    ("Go Gauge Work 5h rolling Prognose", "Go Gauge Work 5h rolling Forecast"),
    ("Go Gauge Work 5h rolling Restbudget", "Go Gauge Work 5h rolling Remaining"),
    ("Go Gauge Work 5h rolling Restzeit", "Go Gauge Work 5h rolling Time to Reset"),
    ("Go Gauge Modelle", "Go Gauge Models"),
    ("Go Gauge Live-Modelle", "Go Gauge Live Models"),
    ("Go Gauge Günstigstes Modell", "Go Gauge Cheapest Model"),
    ("Go Gauge Free-Modelle", "Go Gauge Free Models"),
    ("Go Gauge Work Abo aktiv", "Go Gauge Work Subscription Active"),
    ("Go Gauge API erreichbar", "Go Gauge API Reachable"),
    ("Go Gauge Warnschwelle · Work", "Go Gauge Warning Threshold · Work"),
    ("Go Gauge Ampel Rot-Grenze · Work", "Go Gauge Pace Red Limit · Work"),
    ("Go Gauge Nutzung Refresh (Minuten) · Work", "Go Gauge Usage Refresh (min) · Work"),
    ("Go Gauge Modelle Refresh (Minuten) · Work", "Go Gauge Models Refresh (min) · Work"),
    ("Go Gauge Work Nutzung Auto-Update", "Go Gauge Work Auto Update Usage"),
    ("Go Gauge Work Modelle Auto-Update", "Go Gauge Work Auto Update Models"),
    ("Go Gauge Aktualisieren", "Go Gauge Refresh"),
]

# Names that contain no legacy German fragment must be left untouched
# (migrate_entity_name returns None to signal "nothing changed").
UNCHANGED_NAMES = [
    "Go Gauge Work 5h rolling Reset",
    "Go Gauge Pace",
    "Go Gauge Burn-Rate",
    "Go Gauge rate-limited",
]


def _registry_entry(original_name):
    """Fake EntityRegistry entry exposing only ``original_name``."""
    entry = MagicMock()
    entry.original_name = original_name
    return entry


class TestEntityNameMapping:
    """[REQ-MIG] const.ENTITY_NAME_MIGRATION + migrate_entity_name()."""

    def test_migration_table_is_ordered_and_complete(self):
        """[REQ-MIG] Every table pair maps its legacy fragment to the English one.

        This is the collision/ordering guard: because migrate_entity_name applies
        the pairs as ordered replacements, a more general fragment placed before
        a more specific one would corrupt the result - so each pair's own
        ``old`` value must round-trip to exactly its own ``new`` value.
        """
        pairs = const.ENTITY_NAME_MIGRATION
        assert isinstance(pairs, tuple), "ENTITY_NAME_MIGRATION must be a tuple"
        assert pairs, "ENTITY_NAME_MIGRATION must not be empty"

        for pair in pairs:
            assert isinstance(pair, tuple) and len(pair) == 2, (
                f"migration pair must be a 2-tuple, got {pair!r}"
            )
            legacy, expected = pair
            assert isinstance(legacy, str) and isinstance(expected, str)
            assert const.migrate_entity_name(legacy) == expected, (
                f"{legacy!r} should migrate to {expected!r}, "
                f"got {const.migrate_entity_name(legacy)!r}"
            )

    @pytest.mark.parametrize("legacy, expected", FULL_NAME_CASES)
    def test_full_legacy_name_migrates(self, legacy: str, expected: str):
        """[REQ-MIG] Full legacy entity names migrate to the exact English name."""
        assert const.migrate_entity_name(legacy) == expected

    @pytest.mark.parametrize("legacy, expected", FULL_NAME_CASES)
    def test_migration_is_idempotent(self, legacy: str, expected: str):
        """[REQ-MIG] Re-running the migration on an English name changes nothing."""
        assert const.migrate_entity_name(expected) is None

    @pytest.mark.parametrize("name", UNCHANGED_NAMES)
    def test_unchanged_name_returns_none(self, name: str):
        """[REQ-MIG] Names without a legacy fragment report 'nothing changed'."""
        assert const.migrate_entity_name(name) is None

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_input_returns_none(self, value):
        """[REQ-MIG] None/empty input yields None (no exception)."""
        assert const.migrate_entity_name(value) is None


class TestEntityNameMigrationCallback:
    """[REQ-MIG] __init__._entity_name_migration_callback()."""

    def test_matching_entry_returns_new_original_name(self):
        """[REQ-MIG] A legacy German name yields {'original_name': <english>}."""
        callback = init_module._entity_name_migration_callback
        result = callback(_registry_entry("Go Gauge Work 5h rolling Nutzung"))

        assert result == {"original_name": "Go Gauge Work 5h rolling Usage"}
        # unique_id must never be touched by the entity-name migration.
        assert "unique_id" not in result

    def test_matching_entry_keeps_longest_first_ordering(self):
        """[REQ-MIG] 'Live-Modelle' resolves to 'Live Models', not 'Live Models'."""
        callback = init_module._entity_name_migration_callback
        result = callback(_registry_entry("Go Gauge Live-Modelle"))
        assert result == {"original_name": "Go Gauge Live Models"}

    def test_already_english_entry_returns_none(self):
        """[REQ-MIG] An already-English entity name reports 'nothing changed'."""
        callback = init_module._entity_name_migration_callback
        assert callback(_registry_entry("Go Gauge Work 5h rolling Usage")) is None

    def test_none_original_name_returns_none(self):
        """[REQ-MIG] None original_name yields None without raising."""
        callback = init_module._entity_name_migration_callback
        assert callback(_registry_entry(None)) is None

    def test_missing_original_name_returns_none(self):
        """[REQ-MIG] A registry entry without original_name yields None (defensive)."""
        callback = init_module._entity_name_migration_callback

        class _NoNameEntry:
            pass

        assert callback(_NoNameEntry()) is None


class TestAsyncMigrateEntityNames:
    """[REQ-MIG] __init__._async_migrate_entity_names()."""

    @pytest.mark.asyncio
    async def test_awaits_async_migrate_entries_with_entry_id_and_callback(self):
        """[REQ-MIG] Delegates to async_migrate_entries(hass, entry_id, callback)."""
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        with patch.object(
            ENTITY_REGISTRY, "async_migrate_entries", new_callable=AsyncMock
        ) as migrate:
            await init_module._async_migrate_entity_names(hass, entry)

        migrate.assert_awaited_once()
        args = migrate.await_args.args
        assert args[0] is hass
        assert args[1] == "entry-abc"
        assert args[2] is init_module._entity_name_migration_callback

    @pytest.mark.asyncio
    async def test_registry_update_receives_only_new_name(self):
        """[REQ-MIG] Simulated registry update carries only original_name."""
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-abc"
        observed: dict[str, object] = {}

        async def _fake_migrate(hass_arg, entry_id_arg, callback):
            observed["callback"] = callback
            observed["result"] = callback(_registry_entry("Go Gauge Modelle"))
            observed["hass"] = hass_arg
            observed["entry_id"] = entry_id_arg

        with patch.object(
            ENTITY_REGISTRY,
            "async_migrate_entries",
            new=AsyncMock(side_effect=_fake_migrate),
        ):
            await init_module._async_migrate_entity_names(hass, entry)

        assert observed["hass"] is hass
        assert observed["entry_id"] == "entry-abc"
        assert observed["result"] == {"original_name": "Go Gauge Models"}
        assert "unique_id" not in observed["result"]

    @pytest.mark.asyncio
    async def test_registry_error_is_swallowed(self):
        """[REQ-MIG] A broken/odd registry must not raise out of the migration."""
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        with patch.object(
            ENTITY_REGISTRY,
            "async_migrate_entries",
            new=AsyncMock(side_effect=RuntimeError("registry boom")),
        ):
            await init_module._async_migrate_entity_names(hass, entry)


class TestAsyncMigrateEntryTargetVersion:
    """[REQ-MIG] async_migrate_entry() targets version 7."""

    @pytest.mark.asyncio
    async def test_v5_entry_runs_entity_migration_and_bumps_to_7(self):
        """[REQ-MIG] v5 -> v7 runs both registry migrations and advances the version."""
        entry = MagicMock()
        entry.version = 5
        entry.unique_id = "go_gauge_deadbeefdeadbeef"
        entry.data = {"token": "tok-123", "workspace_name": "WS"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_names", new_callable=AsyncMock
        ) as migrate_names, patch.object(
            init_module, "_async_migrate_entity_ids", new_callable=AsyncMock
        ) as migrate_ids:
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 7
        migrate_names.assert_awaited_once_with(hass, entry)
        migrate_ids.assert_awaited_once_with(hass, entry)

    @pytest.mark.asyncio
    async def test_v4_entry_runs_older_steps_and_entity_migration_in_one_call(self):
        """[REQ-MIG] v4 runs the older data steps AND the entity migration, then 7."""
        original_data = {"token": "tok-456", "workspace_name": "My WS"}
        entry = MagicMock()
        entry.version = 4
        entry.unique_id = "go_gauge_legacy"
        entry.data = original_data
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_names", new_callable=AsyncMock
        ) as migrate_names, patch.object(
            init_module, "_async_migrate_entity_ids", new_callable=AsyncMock
        ):
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 7
        migrate_names.assert_awaited_once_with(hass, entry)
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        assert call_args[1]["data"] == original_data

    @pytest.mark.asyncio
    async def test_v6_entry_runs_entity_id_migration_and_bumps_to_7(self):
        """[REQ-MIG] A v6 entry runs only the v7 entity_id migration, then advances."""
        entry = MagicMock()
        entry.version = 6
        entry.unique_id = "go_gauge_deadbeefdeadbeef"
        entry.data = {"token": "tok-123", "workspace_name": "WS"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_names", new_callable=AsyncMock
        ) as migrate_names, patch.object(
            init_module, "_async_migrate_entity_ids", new_callable=AsyncMock
        ) as migrate_ids:
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 7
        # v6 body must not re-run for an entry already at v6.
        migrate_names.assert_not_awaited()
        migrate_ids.assert_awaited_once_with(hass, entry)

    @pytest.mark.asyncio
    async def test_future_version_8_is_rejected(self):
        """[REQ-MIG] ConfigEntry versions > 7 are rejected (no downgrade)."""
        entry = MagicMock()
        entry.version = 8
        entry.data = {}
        entry.options = {}

        hass = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
