#!/usr/bin/env python3
"""Tests for the v7 German -> English entity_id slug migration.

Covers the interface contract:

- ``const.ENTITY_ID_MIGRATION`` + ``const.migrate_entity_id``
  (ordered, most-specific-first legacy-German -> English slug pairs)
- ``__init__._make_entity_id_migration_callback`` (registry-entry callback
  closure with defensive collision handling)
- ``__init__._async_migrate_entity_ids`` (awaits
  ``homeassistant.helpers.entity_registry.async_migrate_entries`` defensively)
- ``async_migrate_entry`` targeting ConfigEntry version 7
- translation-key wiring: every ``_attr_translation_key`` used by a platform
  class has a matching ``entity.<platform>.<key>.name`` entry in
  strings.json / translations/en.json / translations/de.json, and no platform
  class sets ``_attr_name`` anymore (that would short-circuit translation).

The ``homeassistant.*`` fakes (incl. ``homeassistant.helpers.entity_registry``,
which ``__init__`` imports function-locally) are installed centrally in
``tests/conftest.py``.
"""
from __future__ import annotations

import importlib.util
import json
import re
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
# function-locally during the v7 migration.
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
# against at call time; tests patch its `async_get` / `async_migrate_entries`.
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


# Legacy entity_id -> mapped English entity_id. The targets follow the explicit
# legacy->English mapping in const.ENTITY_ID_MIGRATION (a position-preserving
# phrase replacement of the legacy slug) - NOT necessarily the slug Home
# Assistant would derive for a fresh install. For 10 entity classes the two
# intentionally differ (see TestMappingIsNotFreshInstallCanonical below):
# device-name composition puts a fresh install's entity-specific token in a
# different position (catalog entities get the "Go Gauge Konto" device prefix,
# numbers/buttons get the workspace before the entity name).
FULL_ID_CASES = [
    ("sensor.go_gauge_work_5h_rolling_nutzung",
     "sensor.go_gauge_work_5h_rolling_usage"),
    ("sensor.go_gauge_work_5h_rolling_prognose",
     "sensor.go_gauge_work_5h_rolling_forecast"),
    ("sensor.go_gauge_work_5h_rolling_restbudget",
     "sensor.go_gauge_work_5h_rolling_remaining"),
    ("sensor.go_gauge_work_5h_rolling_restzeit",
     "sensor.go_gauge_work_5h_rolling_time_to_reset"),
    ("sensor.go_gauge_modelle", "sensor.go_gauge_models"),
    ("sensor.go_gauge_live_modelle", "sensor.go_gauge_live_models"),
    ("sensor.go_gauge_gunstigstes_modell", "sensor.go_gauge_cheapest_model"),
    ("sensor.go_gauge_free_modelle", "sensor.go_gauge_free_models"),
    ("binary_sensor.go_gauge_work_abo_aktiv",
     "binary_sensor.go_gauge_work_subscription_active"),
    ("binary_sensor.go_gauge_api_erreichbar",
     "binary_sensor.go_gauge_api_reachable"),
    ("number.go_gauge_warnschwelle_work",
     "number.go_gauge_warning_threshold_work"),
    ("number.go_gauge_ampel_rot_grenze_work",
     "number.go_gauge_pace_red_limit_work"),
    ("number.go_gauge_nutzung_refresh_minuten_work",
     "number.go_gauge_usage_refresh_min_work"),
    ("number.go_gauge_modelle_refresh_minuten_work",
     "number.go_gauge_models_refresh_min_work"),
    ("switch.go_gauge_work_nutzung_auto_update",
     "switch.go_gauge_work_auto_update_usage"),
    ("switch.go_gauge_work_modelle_auto_update",
     "switch.go_gauge_work_auto_update_models"),
    ("button.go_gauge_aktualisieren", "button.go_gauge_refresh"),
]

# Already-English slugs (Reset/Pace/Burn-Rate/rate-limited are intentionally
# excluded from the mapping) must be left untouched.
UNCHANGED_IDS = [
    "sensor.go_gauge_work_5h_rolling_reset",
    "sensor.go_gauge_work_5h_rolling_pace",
    "sensor.go_gauge_work_5h_rolling_burn_rate",
    "binary_sensor.go_gauge_work_5h_rolling_rate_limited",
    "sensor.go_gauge_work_5h_rolling_usage",
    "sensor.go_gauge_models",
    "number.go_gauge_usage_refresh_min_work",
]

# Real entity_ids observed on the shared HA dev instance (HA Core 2026.9.1,
# system language de) BEFORE the v7 migration run. Source of truth:
# docs/E2E-v7-entity-id-migration-2026-09-12.md (before-registry table). Every
# real id carries the device-name-slug prefix ``go_gauge_ha_`` that HA 2026.9
# prepends; the migration MUST preserve that prefix verbatim (m3 guarantee)
# while rewriting the German fragment in place. These cases were added because
# the idealized ``FULL_ID_CASES`` above could not catch RC-2 (the number branch
# returned None for every real prefixed slug).
REAL_INSTANCE_ID_CASES = [
    ("binary_sensor.go_gauge_ha_go_gauge_api_erreichbar",
     "binary_sensor.go_gauge_ha_go_gauge_api_reachable"),
    ("binary_sensor.go_gauge_ha_go_gauge_e2e_abo_aktiv",
     "binary_sensor.go_gauge_ha_go_gauge_e2e_subscription_active"),
    ("button.go_gauge_ha_go_gauge_aktualisieren",
     "button.go_gauge_ha_go_gauge_refresh"),
    ("number.go_gauge_ha_go_gauge_modelle_refresh_minuten_e2e",
     "number.go_gauge_ha_go_gauge_models_refresh_min_e2e"),
    ("number.go_gauge_ha_go_gauge_nutzung_refresh_minuten_e2e",
     "number.go_gauge_ha_go_gauge_usage_refresh_min_e2e"),
    ("number.go_gauge_ha_go_gauge_warnschwelle_e2e",
     "number.go_gauge_ha_go_gauge_warning_threshold_e2e"),
    ("sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_nutzung",
     "sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_usage"),
    ("sensor.go_gauge_ha_go_gauge_e2e_monthly_nutzung",
     "sensor.go_gauge_ha_go_gauge_e2e_monthly_usage"),
    ("sensor.go_gauge_ha_go_gauge_e2e_weekly_nutzung",
     "sensor.go_gauge_ha_go_gauge_e2e_weekly_usage"),
    ("sensor.go_gauge_ha_go_gauge_free_modelle",
     "sensor.go_gauge_ha_go_gauge_free_models"),
    ("sensor.go_gauge_ha_go_gauge_gunstigstes_modell",
     "sensor.go_gauge_ha_go_gauge_cheapest_model"),
    ("sensor.go_gauge_ha_go_gauge_live_modelle",
     "sensor.go_gauge_ha_go_gauge_live_models"),
    ("sensor.go_gauge_ha_go_gauge_modelle",
     "sensor.go_gauge_ha_go_gauge_models"),
    ("switch.go_gauge_ha_go_gauge_e2e_modelle_auto_update",
     "switch.go_gauge_ha_go_gauge_e2e_auto_update_models"),
    ("switch.go_gauge_ha_go_gauge_e2e_nutzung_auto_update",
     "switch.go_gauge_ha_go_gauge_e2e_auto_update_usage"),
]

# Already-English real ids (Reset / rate-limited are intentionally excluded
# from the mapping) must be left untouched.
REAL_INSTANCE_UNCHANGED_IDS = [
    "binary_sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_rate_limited",
    "binary_sensor.go_gauge_ha_go_gauge_e2e_monthly_rate_limited",
    "binary_sensor.go_gauge_ha_go_gauge_e2e_weekly_rate_limited",
    "sensor.go_gauge_ha_go_gauge_e2e_5h_rolling_reset",
    "sensor.go_gauge_ha_go_gauge_e2e_monthly_reset",
    "sensor.go_gauge_ha_go_gauge_e2e_weekly_reset",
]

# Independent inventory of every legacy German object_id *fragment* the v7
# migration must cover and the mapped English fragment it must become.
# Deliberately duplicated from the production table (not derived from it), so a
# missing, swapped or duplicated table entry fails here even though the table's
# own old->new round-trip would still pass. The suffixed variants (``_<ws>``)
# are exercised separately via FULL_ID_CASES.
REQUIRED_SLUG_MIGRATION = {
    "nutzung": "usage",
    "prognose": "forecast",
    "restbudget": "remaining",
    "restzeit": "time_to_reset",
    "modelle": "models",
    "live_modelle": "live_models",
    "gunstigstes_modell": "cheapest_model",
    "free_modelle": "free_models",
    "abo_aktiv": "subscription_active",
    "api_erreichbar": "api_reachable",
    "warnschwelle": "warning_threshold",
    "ampel_rot_grenze": "pace_red_limit",
    "nutzung_refresh_minuten": "usage_refresh_min",
    "modelle_refresh_minuten": "models_refresh_min",
    "nutzung_auto_update": "auto_update_usage",
    "modelle_auto_update": "auto_update_models",
    "aktualisieren": "refresh",
}

# Compound phrases that must be replaced *before* the bare base token they
# contain; used by the ordering guard below.
COMPOUND_BEFORE_BASE = [
    ("nutzung_auto_update", "nutzung"),
    ("modelle_auto_update", "modelle"),
    ("nutzung_refresh_minuten", "nutzung"),
    ("modelle_refresh_minuten", "modelle"),
]


def _registry_entry(entity_id):
    """Fake EntityRegistry entry exposing only ``entity_id``."""
    entry = MagicMock()
    entry.entity_id = entity_id
    return entry


def _callback_with_registry(registry=None, states_available=True, skipped=None):
    """Build the production callback against a stubbed registry/state machine.

    A default registry reports every target id as free (``async_get`` -> None)
    and the fake state machine reports every target id as available; pass
    ``states_available=False`` to simulate a state-only occupant, or a registry
    with ``async_get.return_value`` set to simulate a registry collision.
    """
    if registry is None:
        registry = MagicMock()
        registry.async_get.return_value = None
    hass = MagicMock()
    hass.states.async_available.return_value = states_available
    with patch.object(ENTITY_REGISTRY, "async_get", return_value=registry):
        return init_module._make_entity_id_migration_callback(hass, skipped)


class TestMigrateEntityIdMapping:
    """[REQ-MIG7] const.ENTITY_ID_MIGRATION + migrate_entity_id()."""

    def test_migration_table_is_ordered_and_complete(self):
        """[REQ-MIG7] Every table pair maps its own legacy slug to its own target.

        Because the pairs are applied in order (first matching anchor wins), a
        more general fragment placed before a more specific one would corrupt
        the result - so each pair's ``old`` value must round-trip to exactly its
        own ``new``. The ``go_gauge_`` prefix makes each entry a real (exact)
        object_id so the anchored matcher can apply it.
        """
        pairs = const.ENTITY_ID_MIGRATION
        assert isinstance(pairs, tuple), "ENTITY_ID_MIGRATION must be a tuple"
        assert pairs, "ENTITY_ID_MIGRATION must not be empty"

        for pair in pairs:
            assert isinstance(pair, tuple) and len(pair) == 2, (
                f"migration pair must be a 2-tuple, got {pair!r}"
            )
            legacy, expected = pair
            assert isinstance(legacy, str) and isinstance(expected, str)
            assert const.migrate_entity_id(
                f"sensor.go_gauge_{legacy}"
            ) == f"sensor.go_gauge_{expected}", (
                f"{legacy!r} should migrate to {expected!r}"
            )

    @pytest.mark.parametrize("legacy, expected", FULL_ID_CASES)
    def test_full_legacy_entity_id_migrates(self, legacy: str, expected: str):
        """[REQ-MIG7] Full legacy entity_ids migrate to the mapped English slug."""
        assert const.migrate_entity_id(legacy) == expected

    @pytest.mark.parametrize("legacy, expected", FULL_ID_CASES)
    def test_migration_is_idempotent(self, legacy: str, expected: str):
        """[REQ-MIG7] Re-running the migration on an English id changes nothing."""
        assert const.migrate_entity_id(expected) is None

    @pytest.mark.parametrize("entity_id", UNCHANGED_IDS)
    def test_unchanged_entity_id_returns_none(self, entity_id: str):
        """[REQ-MIG7] Ids without a legacy fragment report 'nothing changed'."""
        assert const.migrate_entity_id(entity_id) is None

    @pytest.mark.parametrize("value", [None, "", "sensor", "not_an_entity_id"])
    def test_invalid_input_returns_none(self, value):
        """[REQ-MIG7] None/empty/domain-less input yields None (no exception)."""
        assert const.migrate_entity_id(value) is None

    def test_domain_is_preserved_verbatim(self):
        """[REQ-MIG7] Only the object_id is rewritten, never the domain."""
        assert const.migrate_entity_id(
            "binary_sensor.go_gauge_live_modelle"
        ) == "binary_sensor.go_gauge_live_models"

    def test_table_covers_required_inventory_exactly(self):
        """[REQ-MIG7] The table's legacy/target sets equal the known inventory.

        Independent completeness check: neither a missing legacy slug nor an
        extra/unknown mapping may slip through.
        """
        table_legacy = {legacy for legacy, _ in const.ENTITY_ID_MIGRATION}
        table_targets = {target for _, target in const.ENTITY_ID_MIGRATION}

        assert table_legacy == set(REQUIRED_SLUG_MIGRATION), (
            "ENTITY_ID_MIGRATION legacy set diverges from the inventory"
        )
        assert table_targets == set(REQUIRED_SLUG_MIGRATION.values()), (
            "ENTITY_ID_MIGRATION target set diverges from the inventory"
        )

    def test_target_slugs_are_collision_free(self):
        """[REQ-MIG7] No two legacy slugs share a migration target."""
        targets = [target for _, target in const.ENTITY_ID_MIGRATION]
        assert len(targets) == len(set(targets)), (
            f"duplicate migration targets: {sorted(targets)}"
        )
        # The legacy keys must be unique too, else ordered replace is ambiguous.
        legacy = [old for old, _ in const.ENTITY_ID_MIGRATION]
        assert len(legacy) == len(set(legacy)), (
            f"duplicate legacy slugs: {sorted(legacy)}"
        )

    @pytest.mark.parametrize(
        "legacy_fragment, expected_fragment",
        sorted(REQUIRED_SLUG_MIGRATION.items()),
    )
    def test_every_required_fragment_migrates(
        self, legacy_fragment: str, expected_fragment: str
    ):
        """[REQ-MIG7] Each inventory fragment maps to its mapped English slug."""
        assert const.migrate_entity_id(f"sensor.go_gauge_{legacy_fragment}") == (
            f"sensor.go_gauge_{expected_fragment}"
        )

    def test_compound_phrases_precede_base_tokens(self):
        """[REQ-MIG7] Ordered table puts compound phrases before their base token."""
        order = [legacy for legacy, _ in const.ENTITY_ID_MIGRATION]
        for compound, base in COMPOUND_BEFORE_BASE:
            assert order.index(compound) < order.index(base), (
                f"{compound!r} must be migrated before {base!r}"
            )

    def test_reversed_order_would_corrupt_compound(self):
        """[REQ-MIG7] Order is load-bearing: base-first would yield a wrong slug."""
        corrupted = "nutzung_auto_update"
        # Simulate the anti-pattern: apply the exact same pairs in reverse order.
        for legacy, english in reversed(const.ENTITY_ID_MIGRATION):
            corrupted = corrupted.replace(legacy, english)

        assert corrupted != "auto_update_usage", (
            "reversed order must produce a corrupted slug, proving ordering matters"
        )
        # The real (correctly ordered) migration yields the mapped target.
        assert const.migrate_entity_id(
            "switch.go_gauge_work_nutzung_auto_update"
        ) == "switch.go_gauge_work_auto_update_usage"

    # --- m3: the workspace/device prefix must survive verbatim --------------

    @pytest.mark.parametrize(
        "legacy_id, expected_id",
        [
            # Workspace slug "modelle" repeats a mapping token; the anchored
            # matcher must rewrite only the entity-specific segment.
            ("number.go_gauge_warnschwelle_modelle",
             "number.go_gauge_warning_threshold_modelle"),
            ("number.go_gauge_modelle_refresh_minuten_modelle",
             "number.go_gauge_models_refresh_min_modelle"),
            ("switch.go_gauge_modelle_nutzung_auto_update",
             "switch.go_gauge_modelle_auto_update_usage"),
            ("switch.go_gauge_modelle_modelle_auto_update",
             "switch.go_gauge_modelle_auto_update_models"),
            ("sensor.go_gauge_modelle_monthly_nutzung",
             "sensor.go_gauge_modelle_monthly_usage"),
            ("binary_sensor.go_gauge_modelle_abo_aktiv",
             "binary_sensor.go_gauge_modelle_subscription_active"),
            # Workspace slug equals the entity token itself.
            ("number.go_gauge_warnschwelle_warnschwelle",
             "number.go_gauge_warning_threshold_warnschwelle"),
            ("number.go_gauge_warnschwelle_nutzung",
             "number.go_gauge_warning_threshold_nutzung"),
            ("switch.go_gauge_nutzung_nutzung_auto_update",
             "switch.go_gauge_nutzung_auto_update_usage"),
        ],
    )
    def test_workspace_prefix_is_preserved(self, legacy_id: str, expected_id: str):
        """[REQ-MIG7] A workspace slug containing a mapping token is not rewritten."""
        assert const.migrate_entity_id(legacy_id) == expected_id

    @pytest.mark.parametrize(
        "legacy_id",
        [
            "number.go_gauge_warnschwelle_modelle",
            "number.go_gauge_modelle_refresh_minuten_modelle",
            "switch.go_gauge_modelle_modelle_auto_update",
            "sensor.go_gauge_modelle_monthly_nutzung",
            "binary_sensor.go_gauge_modelle_abo_aktiv",
        ],
    )
    def test_workspace_prefix_migration_is_idempotent(self, legacy_id: str):
        """[REQ-MIG7] The anchored workspace-safety migration is idempotent."""
        once = const.migrate_entity_id(legacy_id)
        assert once is not None
        assert const.migrate_entity_id(once) is None

    @pytest.mark.parametrize(
        "legacy_id, expected_id",
        [
            # HA appends "_<n>" to de-duplicate a colliding entity_id; it must
            # survive verbatim without blocking the migration ...
            ("sensor.go_gauge_modelle_2", "sensor.go_gauge_models_2"),
            ("sensor.go_gauge_work_monthly_nutzung_2",
             "sensor.go_gauge_work_monthly_usage_2"),
            ("switch.go_gauge_work_nutzung_auto_update_2",
             "switch.go_gauge_work_auto_update_usage_2"),
            ("number.go_gauge_warnschwelle_work_2",
             "number.go_gauge_warning_threshold_work_2"),
            ("binary_sensor.go_gauge_work_abo_aktiv_2",
             "binary_sensor.go_gauge_work_subscription_active_2"),
            # ... and it must not be confused with a numeric workspace tail.
            ("sensor.go_gauge_2_monthly_nutzung",
             "sensor.go_gauge_2_monthly_usage"),
        ],
    )
    def test_dedup_suffix_is_preserved(self, legacy_id: str, expected_id: str):
        """[REQ-MIG7] A ``_<n>`` de-duplication suffix is kept verbatim."""
        assert const.migrate_entity_id(legacy_id) == expected_id

    @pytest.mark.parametrize(
        "migrated_id",
        [
            "sensor.go_gauge_models_2",
            "sensor.go_gauge_work_monthly_usage_2",
            "switch.go_gauge_work_auto_update_usage_2",
        ],
    )
    def test_dedup_migration_is_idempotent(self, migrated_id: str):
        """[REQ-MIG7] Re-running on a de-duplicated mapped id changes nothing."""
        assert const.migrate_entity_id(migrated_id) is None


class TestMappingIsNotFreshInstallCanonical:
    """[REQ-MIG7] Mapping targets are position-preserving, not fresh-install slugs.

    The migration applies the user-mandated legacy->English phrase mapping and
    keeps the legacy token position. For 10 entity classes that intentionally
    differs from the slug HA derives for a fresh install, because a fresh
    entity_id is composed from the device name plus the translated display name:

    - workspace-independent catalog/API entities live on the "Go Gauge Konto"
      device, so a fresh install yields ``go_gauge_konto_<name>`` - the mapped
      target stays ``go_gauge_<name>``;
    - ``number``/``button`` entities belong to a workspace device, so a fresh
      install yields ``go_gauge_<ws>_<name>`` - the mapped target keeps the
      legacy position ``go_gauge_<name>_<ws>`` (numbers) / ``go_gauge_<name>``
      (button).

    These tests document that divergence; they must not be "fixed" by switching
    the mapping to fresh-install slugs.
    """

    def test_number_target_keeps_legacy_suffix_position(self):
        """[REQ-MIG7] Number target preserves the ``_<ws>`` position."""
        migrated = const.migrate_entity_id("number.go_gauge_warnschwelle_team")
        assert migrated == "number.go_gauge_warning_threshold_team"
        # A fresh English install would place the workspace before the name.
        assert migrated != "number.go_gauge_team_warning_threshold"

    def test_account_catalog_target_has_no_device_prefix(self):
        """[REQ-MIG7] Catalog target stays ``go_gauge_*``, not ``go_gauge_konto_*``."""
        migrated = const.migrate_entity_id("sensor.go_gauge_modelle")
        assert migrated == "sensor.go_gauge_models"
        assert migrated != "sensor.go_gauge_konto_models"

    def test_button_target_has_no_workspace_prefix(self):
        """[REQ-MIG7] Button target stays ``go_gauge_refresh``, no workspace."""
        migrated = const.migrate_entity_id("button.go_gauge_aktualisieren")
        assert migrated == "button.go_gauge_refresh"
        assert migrated != "button.go_gauge_team_refresh"


class TestEntityIdMigrationCallback:
    """[REQ-MIG7] __init__._make_entity_id_migration_callback()."""

    def test_legacy_slug_returns_new_entity_id(self):
        """[REQ-MIG7] A legacy German slug yields {'new_entity_id': <english>}."""
        callback = _callback_with_registry()
        result = callback(_registry_entry("sensor.go_gauge_modelle"))

        assert result == {"new_entity_id": "sensor.go_gauge_models"}
        # unique_id must never be touched by the entity_id migration.
        assert "unique_id" not in result

    def test_already_english_slug_returns_none(self):
        """[REQ-MIG7] An already-English slug reports 'nothing changed'."""
        callback = _callback_with_registry()
        assert callback(_registry_entry("sensor.go_gauge_models")) is None

    def test_none_entity_id_returns_none(self):
        """[REQ-MIG7] A missing entity_id yields None without raising."""
        callback = _callback_with_registry()
        assert callback(_registry_entry(None)) is None

    def test_collision_is_skipped(self):
        """[REQ-MIG7] An occupied target id skips the rename instead of raising."""
        occupied = MagicMock()
        registry = MagicMock()
        registry.async_get.return_value = occupied
        callback = _callback_with_registry(registry)

        assert callback(_registry_entry("sensor.go_gauge_modelle")) is None
        registry.async_get.assert_called_with("sensor.go_gauge_models")

    def test_state_only_occupant_is_skipped(self):
        """[REQ-MIG7] A state-only occupant (no registry entry) is skipped too.

        Mirrors ``EntityRegistry._entity_id_available``: the target must be free
        both in the registry AND in the state machine, otherwise HA's
        ``async_update_entity`` would raise ``ValueError`` and abort the pass.
        """
        callback = _callback_with_registry(states_available=False)

        assert callback(_registry_entry("sensor.go_gauge_modelle")) is None

    def test_skipped_pairs_are_collected(self):
        """[REQ-MIG7] Suppressed renames are reported to the caller for logging."""
        skipped: list[tuple[str, str]] = []
        callback = _callback_with_registry(states_available=False, skipped=skipped)

        assert callback(_registry_entry("sensor.go_gauge_modelle")) is None
        assert skipped == [("sensor.go_gauge_modelle", "sensor.go_gauge_models")]


class TestAsyncMigrateEntityIds:
    """[REQ-MIG7] __init__._async_migrate_entity_ids()."""

    @pytest.mark.asyncio
    async def test_awaits_async_migrate_entries_with_entry_id_and_callback(self):
        """[REQ-MIG7] Delegates to async_migrate_entries(hass, entry_id, callback)."""
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-abc"
        registry = MagicMock()

        with patch.object(ENTITY_REGISTRY, "async_get", return_value=registry), \
                patch.object(
                    ENTITY_REGISTRY, "async_migrate_entries", new_callable=AsyncMock
                ) as migrate:
            result = await init_module._async_migrate_entity_ids(hass, entry)

        assert result is True
        migrate.assert_awaited_once()
        args = migrate.await_args.args
        assert args[0] is hass
        assert args[1] == "entry-abc"
        assert callable(args[2])

    @pytest.mark.asyncio
    async def test_registry_error_is_swallowed_and_reports_failure(self):
        """[REQ-MIG7] A broken registry does not raise but reports failure (False)."""
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        with patch.object(
            ENTITY_REGISTRY, "async_get", side_effect=RuntimeError("registry boom")
        ):
            result = await init_module._async_migrate_entity_ids(hass, entry)

        assert result is False

    @pytest.mark.asyncio
    async def test_skipped_collision_is_logged_and_still_succeeds(self, caplog):
        """[REQ-MIG7] A skipped collision is logged (count + pairs) but is not failure.

        An occupied target cannot be freed by retrying, so the pass still counts
        as completed (returns True) - but the incomplete migration is visible in
        the log.
        """
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry-abc"
        registry = MagicMock()
        registry.async_get.return_value = MagicMock()  # every target occupied

        async def _run_callback(hass_, entry_id_, callback):
            callback(_registry_entry("sensor.go_gauge_modelle"))

        with patch.object(ENTITY_REGISTRY, "async_get", return_value=registry), \
                patch.object(
                    ENTITY_REGISTRY, "async_migrate_entries", side_effect=_run_callback
                ), caplog.at_level("WARNING"):
            result = await init_module._async_migrate_entity_ids(hass, entry)

        assert result is True
        assert "1 Rename(s)" in caplog.text
        assert "sensor.go_gauge_modelle -> sensor.go_gauge_models" in caplog.text

    @pytest.mark.asyncio
    async def test_v7_entry_is_idempotent(self):
        """[REQ-MIG7] An entry already at v7 returns True without re-migrating."""
        entry = MagicMock()
        entry.version = 7
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
        migrate_names.assert_not_awaited()
        migrate_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_v6_to_v7_runs_id_migration_and_preserves_unique_id(self):
        """[REQ-MIG7] v6 -> v7 renames ids and keeps unique_id/data intact."""
        entry = MagicMock()
        entry.version = 6
        entry.unique_id = "go_gauge_deadbeefdeadbeef"
        entry.data = {"token": "tok-123", "workspace_name": "WS"}
        entry.options = {"warn_percent": 80}

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
        # Only the v7 step runs for an entry already at v6.
        migrate_names.assert_not_awaited()
        migrate_ids.assert_awaited_once_with(hass, entry)
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        assert call_args[1]["unique_id"] == "go_gauge_deadbeefdeadbeef"
        assert call_args[1]["data"] == {"token": "tok-123", "workspace_name": "WS"}
        assert call_args[1]["options"] == {"warn_percent": 80}

    @pytest.mark.asyncio
    async def test_v7_failure_does_not_advance_version(self):
        """[REQ-MIG7] A failed id pass leaves the entry at v6 so HA retries.

        m2: the v7 step must not silently advance when the helper failed - only
        a successful pass may set ``entry.version = 7``.
        """
        entry = MagicMock()
        entry.version = 6
        entry.unique_id = "go_gauge_deadbeefdeadbeef"
        entry.data = {"token": "tok-123", "workspace_name": "WS"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_ids",
            new_callable=AsyncMock, return_value=False,
        ) as migrate_ids:
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 6
        migrate_ids.assert_awaited_once_with(hass, entry)

    @pytest.mark.asyncio
    async def test_future_version_above_seven_is_rejected(self):
        """[REQ-MIG7] A version > 7 returns False without touching the entry."""
        entry = MagicMock()
        entry.version = 8
        entry.data = {}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_ids", new_callable=AsyncMock
        ) as migrate_ids:
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is False
        migrate_ids.assert_not_awaited()
        hass.config_entries.async_update_entry.assert_not_called()


class TestMigrationPassEndToEnd:
    """[REQ-MIG7] Simulated full registry pass (no abort, stable unique_ids)."""

    @staticmethod
    def _entry(entity_id: str, unique_id: str):
        entry = MagicMock()
        entry.entity_id = entity_id
        entry.unique_id = unique_id
        entry.config_entry_id = "entry-abc"
        return entry

    def test_full_pass_renames_all_and_never_raises(self):
        """[REQ-MIG7] Every legacy slug is renamed, unique_ids are untouched.

        Emulates HA's ``async_migrate_entries`` loop with the *real* production
        callback and an ``async_update_entity`` stand-in that raises on an
        occupied target (exactly like the real EntityRegistry). Because the
        callback pre-checks availability, the pass must complete without error.
        """
        registry_entries: dict[str, object] = {
            legacy: self._entry(legacy, f"uid-{i}")
            for i, (legacy, _expected) in enumerate(FULL_ID_CASES)
        }
        unique_ids_before = {
            e.entity_id: e.unique_id for e in registry_entries.values()
        }

        registry = MagicMock()
        registry.async_get.side_effect = registry_entries.get
        callback = _callback_with_registry(registry)

        # The act of iterating the real callback and applying its updates must
        # never hit the ValueError an occupied target would trigger.
        for current in list(registry_entries):
            entry = registry_entries[current]
            updates = callback(entry)
            if updates is None:
                continue
            target = updates["new_entity_id"]
            assert registry.async_get(target) is None
            del registry_entries[current]
            entry.entity_id = target
            registry_entries[target] = entry

        expected_targets = {expected for _legacy, expected in FULL_ID_CASES}
        assert set(registry_entries) == expected_targets
        # unique_id is immutable throughout the whole pass.
        for entry in registry_entries.values():
            assert entry.unique_id.startswith("uid-")

        # Second pass over the renamed entities is a no-op (idempotent).
        for entry in registry_entries.values():
            assert callback(entry) is None
        assert set(registry_entries) == expected_targets
        assert len(unique_ids_before) == len(expected_targets)

    def test_occupied_target_does_not_abort_other_renames(self):
        """[REQ-MIG7] A collision skips only that entity, the rest still migrate."""
        blocker = self._entry("sensor.go_gauge_models", "uid-blocker")
        legacy = self._entry("sensor.go_gauge_modelle", "uid-legacy")
        free = self._entry("button.go_gauge_aktualisieren", "uid-free")
        registry = MagicMock()
        registry.async_get.side_effect = lambda eid: (
            blocker if eid == blocker.entity_id else None
        )
        callback = _callback_with_registry(registry)

        assert callback(legacy) is None  # skipped, no raise
        assert callback(free) == {"new_entity_id": "button.go_gauge_refresh"}

    def test_real_instance_snapshot_migrates_completely(self):
        """[REQ-MIG7] RC-2/retry: the real 21-entry snapshot migrates fully.

        Emulates the retry of the failed ``migration_error`` entry: the registry
        holds the 15 real German ``go_gauge_ha_*`` slugs plus the 6 real
        already-English ids. Every German slug must be renamed, the 6 English
        ids left untouched, and no ``unique_id`` may change.
        """
        registry_entries: dict[str, object] = {}
        for i, (legacy, _expected) in enumerate(REAL_INSTANCE_ID_CASES):
            registry_entries[legacy] = self._entry(legacy, f"uid-{i}")
        for j, unchanged in enumerate(REAL_INSTANCE_UNCHANGED_IDS):
            registry_entries[unchanged] = self._entry(unchanged, f"uid-unchanged-{j}")

        registry = MagicMock()
        registry.async_get.side_effect = registry_entries.get
        callback = _callback_with_registry(registry)

        renamed = 0
        for current in list(registry_entries):
            entry = registry_entries[current]
            updates = callback(entry)
            if updates is None:
                continue
            target = updates["new_entity_id"]
            assert registry.async_get(target) is None
            del registry_entries[current]
            entry.entity_id = target
            registry_entries[target] = entry
            renamed += 1

        assert renamed == len(REAL_INSTANCE_ID_CASES) == 15
        expected_targets = {expected for _legacy, expected in REAL_INSTANCE_ID_CASES}
        assert expected_targets <= set(registry_entries)
        assert set(REAL_INSTANCE_UNCHANGED_IDS) <= set(registry_entries)
        for entry in registry_entries.values():
            assert entry.unique_id.startswith("uid-")

        # A second pass over the migrated snapshot is a no-op (idempotent).
        assert all(callback(entry) is None for entry in registry_entries.values())


# --- Translation wiring (static, no HA import needed) ----------------------

_PLATFORM_FILES = {
    "sensor": "sensor.py",
    "binary_sensor": "binary_sensor.py",
    "number": "number.py",
    "switch": "switch.py",
    "button": "button.py",
}

_KEY_RE = re.compile(r'_attr_translation_key = "([a-z0-9_]+)"')
_ATTR_NAME_RE = re.compile(r"\b_attr_name\b")


def _translation_keys(platform: str) -> set[str]:
    """Extract every ``_attr_translation_key`` declared in a platform module."""
    source = (BASE / _PLATFORM_FILES[platform]).read_text(encoding="utf-8")
    return set(_KEY_RE.findall(source))


def _load_translations(filename: str) -> dict:
    return json.loads((BASE / filename).read_text(encoding="utf-8"))


class TestTranslationWiring:
    """[REQ-MIG7] has_entity_name + translation_key refactor consistency."""

    def test_base_class_enables_has_entity_name(self):
        """[REQ-MIG7] The shared base enables has_entity_name globally."""
        source = (BASE / "entity.py").read_text(encoding="utf-8")
        assert "_attr_has_entity_name = True" in source

    @pytest.mark.parametrize("platform", list(_PLATFORM_FILES))
    def test_platform_no_longer_sets_attr_name(self, platform: str):
        """[REQ-MIG7] No platform sets ``_attr_name`` (would break translations)."""
        source = (BASE / _PLATFORM_FILES[platform]).read_text(encoding="utf-8")
        assert _ATTR_NAME_RE.search(source) is None, (
            f"{_PLATFORM_FILES[platform]} still sets _attr_name"
        )

    @pytest.mark.parametrize("platform", list(_PLATFORM_FILES))
    def test_every_translation_key_exists_in_all_strings(self, platform: str):
        """[REQ-MIG7] Every code key has entity.<platform>.<key>.name in all files."""
        keys = _translation_keys(platform)
        assert keys, f"no _attr_translation_key found in {_PLATFORM_FILES[platform]}"

        strings = _load_translations("strings.json")
        en = _load_translations("translations/en.json")
        de = _load_translations("translations/de.json")

        for key in keys:
            for label, doc in (("strings.json", strings), ("en.json", en), ("de.json", de)):
                entity_block = doc.get("entity", {}).get(platform, {})
                assert key in entity_block, f"{label}: missing {platform}.{key}"
                assert "name" in entity_block[key], (
                    f"{label}: {platform}.{key} has no 'name'"
                )

    def test_english_master_matches_en_translation(self):
        """[REQ-MIG7] strings.json (master) and en.json carry identical entity text."""
        assert _load_translations("strings.json")["entity"] == (
            _load_translations("translations/en.json")["entity"]
        )

    def test_german_translation_covers_same_keys(self):
        """[REQ-MIG7] de.json covers every key of the English master."""
        master = _load_translations("strings.json")["entity"]
        german = _load_translations("translations/de.json")["entity"]
        for platform, keys in master.items():
            assert set(keys) <= set(german.get(platform, {})), (
                f"de.json is missing keys for {platform}"
            )


class TestRealInstanceEntityIds:
    """[REQ-MIG7] RC-2: the real 2026.9 device-prefixed slugs migrate.

    Source of truth is the before-registry table in
    docs/E2E-v7-entity-id-migration-2026-09-12.md, not a reconstruction.
    """

    @pytest.mark.parametrize("legacy, expected", REAL_INSTANCE_ID_CASES)
    def test_real_prefixed_entity_id_migrates(self, legacy: str, expected: str):
        """[REQ-MIG7] Every real device-prefixed German slug maps to English."""
        assert const.migrate_entity_id(legacy) == expected

    @pytest.mark.parametrize("legacy, expected", REAL_INSTANCE_ID_CASES)
    def test_real_migration_is_idempotent(self, legacy: str, expected: str):
        """[REQ-MIG7] Feeding the real English target back changes nothing."""
        assert const.migrate_entity_id(expected) is None

    @pytest.mark.parametrize("entity_id", REAL_INSTANCE_UNCHANGED_IDS)
    def test_real_english_entity_id_returns_none(self, entity_id: str):
        """[REQ-MIG7] Real already-English ids report 'nothing changed'."""
        assert const.migrate_entity_id(entity_id) is None

    @pytest.mark.parametrize("legacy, expected", REAL_INSTANCE_ID_CASES)
    def test_real_device_prefix_is_preserved(self, legacy: str, expected: str):
        """[REQ-MIG7] m3: the ``go_gauge_ha_`` device prefix survives verbatim."""
        assert legacy.split(".", 1)[1].startswith("go_gauge_ha_")
        assert expected.split(".", 1)[1].startswith("go_gauge_ha_")

    def test_all_three_real_number_slugs_migrate(self):
        """[REQ-MIG7] RC-2 regression guard: number slugs no longer return None."""
        number_cases = [
            (legacy, expected)
            for legacy, expected in REAL_INSTANCE_ID_CASES
            if legacy.startswith("number.")
        ]
        assert len(number_cases) == 3, "expected the three real number slugs"
        for legacy, expected in number_cases:
            assert const.migrate_entity_id(legacy) == expected, legacy

    def test_real_number_slug_callback_returns_target(self):
        """[REQ-MIG7] The registry callback renames a real prefixed number id."""
        callback = _callback_with_registry()
        result = callback(
            _registry_entry("number.go_gauge_ha_go_gauge_warnschwelle_e2e")
        )

        assert result == {
            "new_entity_id": "number.go_gauge_ha_go_gauge_warning_threshold_e2e"
        }


class TestStrictConfigEntryMigration:
    """[REQ-MIG7] RC-1/RC-3: migration must use ``async_update_entry``.

    The permissive ``MagicMock`` entries could not catch the real-instance crash
    (``entry.version = 6`` raised ``AttributeError`` on HA 2026.9). These tests
    run the real ``async_migrate_entry`` against
    :class:`conftest.StrictConfigEntry`, which rejects direct managed-attribute
    assignment exactly like the real ``ConfigEntry``.
    """

    def test_strict_entry_forbids_direct_version_assignment(self):
        """[REQ-MIG7] Sanity: the double mirrors HA's ``__setattr__`` guard."""
        entry = conftest.StrictConfigEntry(version=5)

        with pytest.raises(AttributeError, match="version cannot be changed directly"):
            entry.version = 6

        assert entry.version == 5

    def test_strict_entry_allows_async_update_entry_version(self):
        """[REQ-MIG7] Only ``async_update_entry`` may advance the version."""
        entry = conftest.StrictConfigEntry(version=5)
        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        hass.config_entries.async_update_entry(entry, version=7)

        assert entry.version == 7

    @pytest.mark.asyncio
    async def test_v5_entry_migrates_on_strict_entry(self):
        """[REQ-MIG7] v5 -> v7 runs on a strict entry and sets version via update."""
        entry = conftest.StrictConfigEntry(
            version=5,
            unique_id="go_gauge_deadbeefdeadbeef",
            data={"token": "tok-123", "workspace_name": "WS"},
            options={"warn_percent": 80},
        )
        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_names", new_callable=AsyncMock
        ) as migrate_names, patch.object(
            init_module, "_async_migrate_entity_ids",
            new_callable=AsyncMock, return_value=True,
        ) as migrate_ids:
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 7
        migrate_names.assert_awaited_once_with(hass, entry)
        migrate_ids.assert_awaited_once_with(hass, entry)

        update = hass.config_entries.async_update_entry
        update.assert_called_once()
        kwargs = update.call_args.kwargs
        assert kwargs["version"] == 7
        assert kwargs["unique_id"] == "go_gauge_deadbeefdeadbeef"
        assert kwargs["data"] == {"token": "tok-123", "workspace_name": "WS"}
        assert kwargs["options"] == {"warn_percent": 80}

    @pytest.mark.asyncio
    async def test_v1_entry_migrates_on_strict_entry(self):
        """[REQ-MIG7] v1 -> v7 with all data steps on a strict entry."""
        entry = conftest.StrictConfigEntry(
            version=1,
            data={"tokens": ["token-1", "token-2"], "host": "192.168.1.1"},
            options={"scan_interval": 300},
        )
        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_names", new_callable=AsyncMock
        ), patch.object(
            init_module, "_async_migrate_entity_ids",
            new_callable=AsyncMock, return_value=True,
        ):
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 7
        kwargs = hass.config_entries.async_update_entry.call_args.kwargs
        assert kwargs["version"] == 7
        assert kwargs["data"] == {"token": "token-1", "workspace_name": ""}
        assert kwargs["options"]["usage_refresh_minutes"] == 5

    @pytest.mark.asyncio
    async def test_failed_id_pass_keeps_version_on_strict_entry(self):
        """[REQ-MIG7] m2: a failed v7 pass persists version 6, not 7."""
        entry = conftest.StrictConfigEntry(
            version=6,
            unique_id="go_gauge_deadbeefdeadbeef",
            data={"token": "tok-123", "workspace_name": "WS"},
            options={},
        )
        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        with patch.object(
            init_module, "_async_migrate_entity_ids",
            new_callable=AsyncMock, return_value=False,
        ):
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 6
        kwargs = hass.config_entries.async_update_entry.call_args.kwargs
        assert kwargs["version"] == 6


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
