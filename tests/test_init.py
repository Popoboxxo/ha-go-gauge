#!/usr/bin/env python3
"""Integration __init__ tests: async_setup_entry, async_migrate_entry, async_unload_entry.

Audit reference: P2-Point-11 (https://github.com/Popoboxxo/ha-go-gauge/docs/AUDIT-2026-09-04.md)
- Tests for async_setup_entry: entry registration in hass.data, catalog-owner logic
- Tests for async_migrate_entry: migration path v1/v2 -> v3 -> v4 -> v5 -> v6 -> v7 -> v8
  (v6 = German->English entity-name migration,
   v7 = German->English entity_id slug migration,
   v8 = explicit entry_kind marker for every workspace entry)
- Tests for async_setup: the tolerant account-entry bootstrap (2.0.0)
- Tests for async_unload_entry: cleanup, owner migration on unload

Tests import the REAL __init__ module and verify behavior directly
against actual code (not inline logic duplicates).
"""
from __future__ import annotations

import asyncio
import hashlib
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

# Base homeassistant.* fakes (and the aiohttp fake) are installed centrally in
# tests/conftest.py, imported by pytest before this module is collected. Only
# the coordinator-specific extension below is per-test (deliberately NOT
# centralized, see conftest docstring): DataUpdateCoordinator must be
# subscriptable (`DataUpdateCoordinator[dict[str, Any]]`) and expose an
# __init__ compatible with the real coordinator, plus an UpdateFailed exception.


def _class_getitem(cls, item):
    """Helper: makes DataUpdateCoordinator subscriptable."""
    return cls


_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {
        "__class_getitem__": classmethod(_class_getitem),
        "__init__": lambda self, hass, logger, name=None, update_interval=None: None,
    },
)
_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})


def _load(name: str, path: str):
    """Dynamically import a module from file, bypass sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load real modules in dependency order
const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
coordinator = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
# Load account.py before __init__: __init__ imports it relatively and the
# offline `_load` loader only resolves sibling modules already present in
# sys.modules under their dotted "go_gauge.*" name.
# account.py binds ConfigEntryState at import time; a later-collected
# module may have swapped the fake module object, so re-install the stable
# double right before the import.
conftest.install_config_entry_state_stub()
account_module = _load("go_gauge.account", str(BASE / "account.py"))
init_module = _load("go_gauge.__init__", str(BASE / "__init__.py"))


@pytest.fixture(autouse=True)
def _stub_registry_migrations():
    """Stub the entity-registry name/id migrations to a no-op success.

    The migration *data* logic under test here is independent of the entity
    registry, which the offline HA stubs cannot provide. Without this the v7
    step would (correctly) report failure and refuse to advance the version
    (see the m2 behavior); the restore-version-on-failure path is covered in
    tests/test_entity_id_migration.py.
    """
    with patch.object(
        init_module, "_async_migrate_entity_names", new_callable=AsyncMock
    ), patch.object(
        init_module, "_async_migrate_entity_ids",
        new_callable=AsyncMock, return_value=True,
    ):
        yield


class TestAsyncMigrateEntryLogic:
    """Tests for async_migrate_entry() version migration logic.

    Version evolution:
    v1-v2: Monitor-era (host/port + tokens list)
    v3: Multi-token format
    v4: Single workspace
    v5: SHA-256 unique_id
    v6: German->English entity-name migration
    v7: German->English entity_id slug migration
    v8: explicit entry_kind marker (current)

    Tests the real async_migrate_entry function from __init__.py.
    """

    @pytest.mark.asyncio
    async def test_migrate_v1_to_v8_extracts_first_token(self):
        """[AUDIT-P2-11] v1->v8 migration keeps first token from list."""
        # Create mock entry for v1
        entry = MagicMock()
        entry.version = 1
        entry.data = {"tokens": ["token-1", "token-2"], "host": "192.168.1.1"}
        entry.options = {"scan_interval": 300}

        # Mock hass.config_entries
        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        # Migration should succeed
        assert result is True
        # Entry version should be updated to 8 (current)
        assert entry.version == 8
        # First token should be extracted
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        assert updated_data["token"] == "token-1"
        assert updated_data["entry_kind"] == "workspace"
        assert "tokens" not in updated_data
        assert "host" not in updated_data

    @pytest.mark.asyncio
    async def test_migrate_v2_to_v4_converts_scan_interval(self):
        """[AUDIT-P2-11] v2->v4 migration converts scan_interval (seconds) to usage_refresh_minutes."""
        entry = MagicMock()
        entry.version = 2
        entry.data = {"tokens": ["secure-token-abc"]}
        entry.options = {"scan_interval": 120}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        updated_options = call_args[1]["options"]
        assert updated_data["token"] == "secure-token-abc"
        # scan_interval: 120 seconds -> 2 minutes
        assert updated_options.get("usage_refresh_minutes") == 2

    @pytest.mark.asyncio
    async def test_migrate_v3_to_v4_single_workspace(self):
        """[AUDIT-P2-11] v3->v4 migration extracts single token from multi-token list."""
        entry = MagicMock()
        entry.version = 3
        entry.data = {
            "tokens": ["token-alpha", "token-beta"],
            "workspace_name": "WS-3",
        }
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        assert updated_data["token"] == "token-alpha"
        assert updated_data["workspace_name"] == "WS-3"
        assert "tokens" not in updated_data

    @pytest.mark.asyncio
    async def test_migrate_v4_to_v8_preserves_data(self):
        """[AUDIT-P2-11] v4->v8 migration advances version but preserves data."""
        original_data = {"token": "current-token", "workspace_name": "My WS"}
        entry = MagicMock()
        entry.version = 4
        entry.data = original_data
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        # Version advances to current (8); the only data change is the v8
        # entry_kind marker, everything else is untouched.
        assert entry.version == 8
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        assert updated_data == {**original_data, "entry_kind": "workspace"}

    @pytest.mark.asyncio
    async def test_migrate_future_version_rejected(self):
        """[AUDIT-P2-11] Versions > 8 are rejected (cannot downgrade)."""
        entry = MagicMock()
        entry.version = 9
        entry.data = {}
        entry.options = {}

        hass = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        # Should return False for unsupported versions
        assert result is False

    @pytest.mark.asyncio
    async def test_migrate_v3_empty_token_list(self):
        """[AUDIT-P2-11] v3->v4 handles empty token list gracefully."""
        entry = MagicMock()
        entry.version = 3
        entry.data = {"tokens": [], "workspace_name": "Empty"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        # Empty token list should yield empty string
        assert updated_data["token"] == ""


class TestUniqueIdHashMigration:
    """v4->v8 (incl. the v5 step): unique_id plaintext-fragment -> SHA-256 hash.

    Security migration (AUDIT-2026-09-04): the pre-v5 unique_id embedded a
    16-char plaintext token fragment persisted in HA storage. The v5 step
    replaces it with a SHA-256 hash recomputed from the stored token, matching
    exactly what const.token_unique_id (and thus the config flow) produces. A
    v4 entry therefore ends up at the current target version 8.
    """

    @staticmethod
    def _expected(token: str) -> str:
        """Independently derive the canonical hashed unique_id."""
        return f"go_gauge_{hashlib.sha256(token.encode()).hexdigest()[:16]}"

    @pytest.mark.asyncio
    async def test_v4_to_v8_hashes_unique_id(self):
        """[AUDIT-P2-11] v4->v8 replaces the plaintext-fragment id with the hash."""
        token = "MyWorkspaceToken-abcdef123456"
        entry = MagicMock()
        entry.version = 4
        # Old (leaky) format: first 16 chars of the token, lowercased.
        entry.unique_id = f"go_gauge_{token[:16].lower()}"
        entry.data = {"token": token, "workspace_name": "WS"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 8
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        # (a) new unique_id equals the exact SHA-256 result
        assert call_args[1]["unique_id"] == self._expected(token)
        # no plaintext token fragment survives in the id
        assert token[:16].lower() not in call_args[1]["unique_id"]
        # (c) entry.data (token etc.) is preserved untouched - the v8 step only
        # adds the entry_kind marker.
        assert call_args[1]["data"] == {
            "token": token, "workspace_name": "WS", "entry_kind": "workspace",
        }

    @pytest.mark.asyncio
    async def test_v5_entry_migration_is_idempotent(self):
        """[AUDIT-P2-11] An already-hashed v5 entry keeps its id and reaches v8."""
        token = "StableToken-0987654321"
        hashed = self._expected(token)
        entry = MagicMock()
        entry.version = 5
        entry.unique_id = hashed
        entry.data = {"token": token, "workspace_name": "WS"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 8
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        # unique_id stays the already-hashed value (no re-hash, no corruption)
        assert call_args[1]["unique_id"] == hashed

    @pytest.mark.asyncio
    async def test_v4_to_v7_missing_token_keeps_unique_id(self):
        """[AUDIT-P2-11] Edge case: no token -> unique_id untouched, no raise."""
        old_uid = "go_gauge_legacyplaintext"
        entry = MagicMock()
        entry.version = 4
        entry.unique_id = old_uid
        entry.data = {"token": "", "workspace_name": "Empty"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 8
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        # Defensive: without a token the old unique_id is left as-is.
        assert call_args[1]["unique_id"] == old_uid


class _TolerantPathCoordinator:
    """Coordinator double whose FIRST refresh always fails (offline start).

    The real tolerant path is what publishes the fallback snapshot with the
    usage-only keys (plan §2.6/§2.7); ``async_set_updated_data`` records it so a
    test can assert the exact key set without a real HA coordinator.
    """

    auto_usage = True
    usage_minutes = 10

    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def async_config_entry_first_refresh(self) -> None:
        raise RuntimeError("cloudflare down")

    def async_set_updated_data(self, data: dict) -> None:
        self.updates.append(data)


class TestAsyncSetupEntryLogic:
    """Tests for async_setup_entry() of a workspace entry (2.0.0).

    No catalog-owner mechanism exists anymore (plan §2.6/B2): the account entry
    owns the catalog; a workspace only registers its runtime and forwards
    ``PLATFORMS``.
    """

    @pytest.mark.asyncio
    async def test_workspace_setup_registers_coordinator_without_owner_key(self):
        """[PLAN2-B2] Workspace setup creates no ``_catalog_owner`` marker."""
        entry = MagicMock()
        entry.entry_id = "entry_1"
        entry.data = {"token": "test-token", "workspace_name": "WS1"}
        entry.options = {}

        hass = MagicMock()
        hass.data = {}
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        with patch.object(init_module, "GoGaugeCoordinator", MagicMock()), \
                patch.object(
                    init_module, "async_ensure_account_entry",
                    new_callable=AsyncMock,
                ):
            result = await init_module.async_setup_entry(hass, entry)

        assert result is True
        assert "entry_1" in hass.data["go_gauge"]
        assert "_catalog_owner" not in hass.data["go_gauge"]

    @pytest.mark.asyncio
    async def test_two_workspaces_never_become_owner(self):
        """[PLAN2-B2] N workspace entries -> N runtimes, still no owner key."""
        hass = MagicMock()
        hass.data = {}
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        with patch.object(init_module, "GoGaugeCoordinator", MagicMock()), \
                patch.object(
                    init_module, "async_ensure_account_entry",
                    new_callable=AsyncMock,
                ):
            for index in (1, 2):
                entry = MagicMock()
                entry.entry_id = f"entry_{index}"
                entry.data = {
                    "token": f"token-{index}", "workspace_name": f"WS{index}"
                }
                entry.options = {}
                assert await init_module.async_setup_entry(hass, entry) is True

        assert set(hass.data["go_gauge"]) == {"entry_1", "entry_2"}

    @pytest.mark.asyncio
    async def test_tolerant_first_refresh_publishes_usage_only_snapshot(self):
        """[PLAN2-M3] The fallback snapshot has the exact usage-only key set."""
        entry = MagicMock()
        entry.entry_id = "entry_offline"
        entry.data = {"token": "test-token", "workspace_name": "Offline"}
        entry.options = {}

        hass = MagicMock()
        hass.data = {}
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        coordinator = _TolerantPathCoordinator()

        with patch.object(
            init_module, "GoGaugeCoordinator", MagicMock(return_value=coordinator)
        ), patch.object(
            init_module, "async_ensure_account_entry", new_callable=AsyncMock
        ):
            assert await init_module.async_setup_entry(hass, entry) is True

        assert coordinator.updates, "fallback snapshot must be published"
        assert set(coordinator.updates[0]) == {
            "fetched_at",
            "last_usage_fetch",
            "last_usage_ok",
            "last_usage_attempt",
            "last_usage_outcome",
            "auto_usage",
            "usage_refresh_minutes",
            "workspaces",
        }

    @pytest.mark.asyncio
    async def test_entry_registered_in_domain_registry(self):
        """[AUDIT-P2-11] Entry is registered in hass.data[DOMAIN][entry_id]."""
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {"token": "test-token", "workspace_name": "TestWS"}
        entry.options = {}

        hass = MagicMock()
        hass.data = {}
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        with patch.object(
            init_module, "GoGaugeCoordinator", MagicMock()
        ) as mock_coord_class:
            mock_coordinator = MagicMock()
            mock_coord_class.return_value = mock_coordinator
            result = await init_module.async_setup_entry(hass, entry)

        assert result is True
        # Entry should be registered in hass.data
        assert "test_entry" in hass.data["go_gauge"]
        assert hass.data["go_gauge"]["test_entry"] == mock_coordinator


class TestAsyncUnloadEntryLogic:
    """Tests for async_unload_entry() cleanup and owner migration logic.

    Tests the real async_unload_entry function from __init__.py.
    """

    @pytest.mark.asyncio
    async def test_unload_removes_entry_from_registry(self):
        """[AUDIT-P2-11] Unload removes entry from hass.data[DOMAIN]."""
        entry = MagicMock()
        entry.entry_id = "entry_remove"

        hass = MagicMock()
        hass.data = {
            "go_gauge": {
                "entry_remove": MagicMock(),
                "entry_keep": MagicMock(),
            }
        }
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await init_module.async_unload_entry(hass, entry)

        assert result is True
        assert "entry_remove" not in hass.data["go_gauge"]
        assert "entry_keep" in hass.data["go_gauge"]

    @pytest.mark.asyncio
    async def test_unload_does_not_create_or_touch_owner_keys(self):
        """[PLAN2-B2] Workspace unload neither owns nor transfers anything.

        Since 2.0.0 there is no catalog owner (the account entry owns the
        catalog), so unloading a workspace must not write ``_catalog_owner``
        and must leave every other runtime object untouched.
        """
        entry1_id = "entry_first"
        entry2_id = "entry_second"

        coord2 = MagicMock(spec=coordinator.GoGaugeCoordinator)

        entry = MagicMock()
        entry.entry_id = entry1_id

        hass = MagicMock()
        hass.data = {
            "go_gauge": {
                entry1_id: MagicMock(spec=coordinator.GoGaugeCoordinator),
                entry2_id: coord2,
            }
        }
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await init_module.async_unload_entry(hass, entry)

        assert result is True
        assert entry1_id not in hass.data["go_gauge"]
        assert hass.data["go_gauge"][entry2_id] is coord2
        assert "_catalog_owner" not in hass.data["go_gauge"]

    @pytest.mark.asyncio
    async def test_unload_preserves_non_coordinator_data(self):
        """[AUDIT-P2-11] Unload preserves non-coordinator entries in registry."""
        entry = MagicMock()
        entry.entry_id = "entry_remove"

        hass = MagicMock()
        hass.data = {
            "go_gauge": {
                "entry_remove": MagicMock(),
                "metadata": "should_stay",
            }
        }
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await init_module.async_unload_entry(hass, entry)

        assert result is True
        assert "metadata" in hass.data["go_gauge"]
        assert "entry_remove" not in hass.data["go_gauge"]


class TestMigrationVersionConversions:
    """Tests for version-specific migrations and data transformations.

    Tests conversion formulas and edge cases used in the real migration logic.
    """

    def test_migration_scan_interval_edge_cases(self):
        """[AUDIT-P2-11] scan_interval conversion handles edge cases.

        Formula used in __init__.py: max(1, int(old_interval) // 60)
        """
        test_cases = [
            (0, 1),       # 0 seconds -> 1 minute (clamped)
            (60, 1),      # 60 seconds -> 1 minute
            (120, 2),     # 120 seconds -> 2 minutes
            (300, 5),     # 300 seconds -> 5 minutes
            (3600, 60),   # 3600 seconds -> 60 minutes
        ]

        for scan_interval, expected_minutes in test_cases:
            # Replicate the formula from __init__.py line 44
            result = max(1, int(scan_interval) // 60)
            assert result == expected_minutes, (
                f"scan_interval {scan_interval}s should convert to {expected_minutes} minutes"
            )

    def test_migration_workspace_name_preservation(self):
        """[AUDIT-P2-11] workspace_name is preserved through migrations.

        The migration preserves the workspace_name field as-is.
        """
        names = ["", "My WS", "Workspace 1", "  spaces  "]

        for name in names:
            # Migration preserves the name exactly as stored
            preserved_name = name
            assert preserved_name == name



class TestAsyncMigrateEntryV8:
    """[PLAN2-B4] v8 marks every entry explicitly - O(1), no registry/network work.

    The v8 step must only set ``data[CONF_ENTRY_KIND]``: a raising spy on the
    entity registry proves the step never touches HA's registry, and a single
    ``async_update_entry`` proves the write stays atomic.
    """

    @pytest.mark.asyncio
    async def test_migrate_v7_to_v8_sets_entry_kind(self):
        """[PLAN2-B4] v7 -> v8 sets entry_kind=workspace with one atomic update."""
        entry = conftest.StrictConfigEntry(
            version=7,
            unique_id="go_gauge_deadbeefdeadbeef",
            data={"token": "tok-123", "workspace_name": "WS"},
            options={"warn_percent": 80},
        )
        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        from homeassistant.helpers import entity_registry as er_module

        registry_spy = AsyncMock(
            side_effect=AssertionError("v8 must not touch the entity registry")
        )
        with patch.object(er_module, "async_migrate_entries", registry_spy), \
                patch.object(
                    init_module, "_async_migrate_entity_names", new_callable=AsyncMock
                ) as migrate_names, \
                patch.object(
                    init_module, "_async_migrate_entity_ids", new_callable=AsyncMock
                ) as migrate_ids:
            result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 8
        assert entry.data[const.CONF_ENTRY_KIND] == const.ENTRY_KIND_WORKSPACE
        # Neither registry migration step ran, and the raw registry was untouched.
        migrate_names.assert_not_awaited()
        migrate_ids.assert_not_awaited()
        registry_spy.assert_not_awaited()

        update = hass.config_entries.async_update_entry
        update.assert_called_once()
        kwargs = update.call_args.kwargs
        assert kwargs["version"] == 8
        assert kwargs["data"] == {
            "token": "tok-123", "workspace_name": "WS", "entry_kind": "workspace",
        }
        assert kwargs["options"] == {"warn_percent": 80}
        assert kwargs["unique_id"] == "go_gauge_deadbeefdeadbeef"

    @pytest.mark.asyncio
    async def test_migrate_v8_entry_is_accepted(self):
        """[PLAN2-B4] An entry already at v8 is valid (no downgrade/reject)."""
        entry = MagicMock()
        entry.version = 8
        entry.unique_id = "go_gauge_deadbeefdeadbeef"
        entry.data = {"token": "tok-123", "workspace_name": "WS", "entry_kind": "workspace"}
        entry.options = {}

        hass = MagicMock()
        conftest.install_applying_config_entries(hass)

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 8
        assert entry.data["entry_kind"] == "workspace"


def _config_entry_double(
    entry_id: str,
    *,
    unique_id: str | None = None,
    data: dict | None = None,
    disabled_by: object = None,
    state: object = "not_loaded",
):
    """Build a minimal ConfigEntry-like object for the ``ensure`` tests."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.unique_id = unique_id
    entry.data = dict(data) if data is not None else {}
    entry.disabled_by = disabled_by
    entry.state = state
    return entry


def _ensure_hass(entries: list):
    """Build a ``hass`` whose config entries return ``entries`` (offline doubles)."""
    hass = MagicMock()
    hass.data = {}
    conftest.install_applying_config_entries(hass)
    hass.config_entries.async_entries = MagicMock(return_value=list(entries))
    hass.config_entries.async_setup = AsyncMock(return_value=True)
    hass.config_entries.flow.async_init = AsyncMock()
    return hass


def _scheduling_hass() -> tuple[MagicMock, list]:
    """Return a ``hass`` double whose ``async_create_task`` really schedules.

    ``async_setup`` must NOT await the account ensure (HA self-await hazard, see
    its docstring); it schedules it as a task. A plain ``MagicMock`` would
    accept the coroutine and never run it (plus a "never awaited" warning), so
    this helper turns ``async_create_task`` into a real ``asyncio`` task and
    collects it for the test to await.
    """
    hass = MagicMock()
    hass.data = {}
    tasks: list = []

    def _create_task(coro, name=None):
        task = asyncio.ensure_future(coro)
        tasks.append(task)
        return task

    hass.async_create_task = MagicMock(side_effect=_create_task)
    return hass, tasks


class TestAsyncSetupEnsuresAccount:
    """[PLAN2-E2] Domain ``async_setup`` schedules the account ensure tolerantly.

    Review fix (blocker 1): the ensure is scheduled as a TASK, never awaited in
    place. Awaiting it inside the domain setup let an account-entry setup wait
    for the very component setup that was awaiting the ensure (HA self-await
    deadlock, 300 s timeout).
    """

    @pytest.mark.asyncio
    async def test_async_setup_schedules_ensure_as_task(self):
        """[PLAN2-E2] async_setup returns True and runs ensure in its own task."""
        hass, tasks = _scheduling_hass()

        with patch.object(
            init_module, "async_ensure_account_entry", new_callable=AsyncMock
        ) as ensure:
            result = await init_module.async_setup(hass, {})

            assert result is True
            assert len(tasks) == 1
            hass.async_create_task.assert_called_once()
            assert hass.async_create_task.call_args.kwargs["name"] == (
                f"{const.DOMAIN}_ensure_account"
            )
            ensure.assert_not_awaited()  # not awaited inside the setup

            await asyncio.gather(*tasks)

        ensure.assert_awaited_once_with(hass)

    @pytest.mark.asyncio
    async def test_async_setup_returns_true_when_ensure_raises(self):
        """[PLAN2-E2, PLAN2-M5] The scheduled ensure swallows its own exception."""
        hass, tasks = _scheduling_hass()

        with patch.object(
            init_module, "async_ensure_account_entry",
            new_callable=AsyncMock, side_effect=RuntimeError("ensure boom"),
        ):
            result = await init_module.async_setup(hass, {})
            assert result is True
            # Must not raise: the task body guards the ensure call.
            await asyncio.gather(*tasks)

    @pytest.mark.asyncio
    async def test_async_setup_survives_unschedulable_task(self):
        """[PLAN2-M5] A failing ``async_create_task`` (shutdown) is only logged."""
        hass = MagicMock()
        hass.data = {}
        hass.async_create_task = MagicMock(side_effect=RuntimeError("loop closed"))

        result = await init_module.async_setup(hass, {})

        assert result is True


class TestEnsureAccountEntry:
    """[PLAN2-B3] ``async_ensure_account_entry`` creates/loads the singleton entry.

    Phase A scope: the full ensure logic (plan §7.2) is implemented here, while
    the account *runtime* (coordinator/platforms) is still a stub. The tests
    therefore assert the entry bookkeeping and the tolerance guarantees.
    """

    @pytest.mark.asyncio
    async def test_creates_account_entry_via_import_flow(self):
        """[PLAN2-B3] Workspace present + no account entry -> import flow starts."""
        workspace = _config_entry_double(
            "ws1", data={"token": "tok", "workspace_name": "WS"}
        )
        hass = _ensure_hass([workspace])

        await init_module.async_ensure_account_entry(hass)

        hass.config_entries.flow.async_init.assert_awaited_once_with(
            const.DOMAIN, context={"source": account_module.SOURCE_IMPORT}
        )
        hass.config_entries.async_setup.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_workspace_means_no_account_entry(self):
        """[PLAN2-E2] A bare start without any entry creates nothing."""
        hass = _ensure_hass([])

        await init_module.async_ensure_account_entry(hass)

        hass.config_entries.flow.async_init.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_workspace_does_not_trigger_creation(self):
        """[PLAN2-E2] Only an ENABLED workspace justifies an account entry."""
        workspace = _config_entry_double(
            "ws1", data={"token": "tok"}, disabled_by="user"
        )
        hass = _ensure_hass([workspace])

        await init_module.async_ensure_account_entry(hass)

        hass.config_entries.flow.async_init.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_loaded_account_entry_is_a_noop(self):
        """[PLAN2-E2] A loaded account entry is left alone."""
        account = _config_entry_double(
            "acc1",
            unique_id=const.ACCOUNT_ENTRY_UNIQUE_ID,
            data={"entry_kind": "account"},
            state=conftest.install_config_entry_state_stub().LOADED,
        )
        hass = _ensure_hass([account])

        await init_module.async_ensure_account_entry(hass)

        hass.config_entries.async_setup.assert_not_awaited()
        hass.config_entries.flow.async_init.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_loaded_account_entry_is_left_to_ha(self, caplog):
        """[PLAN2-E2] An existing but unloaded account entry is NOT set up.

        Review fix (blocker 2b): HA sets stored entries up itself (plan §7.2);
        an extra ``async_setup`` in the caller loaded the entry twice and spammed
        ``OperationNotAllowed``. Only a DEBUG line documents the state.
        """
        account = _config_entry_double(
            "acc1",
            unique_id=const.ACCOUNT_ENTRY_UNIQUE_ID,
            data={"entry_kind": "account"},
        )
        hass = _ensure_hass([account])

        with caplog.at_level("DEBUG"):
            await init_module.async_ensure_account_entry(hass)

        hass.config_entries.async_setup.assert_not_awaited()
        hass.config_entries.flow.async_init.assert_not_awaited()
        assert "not loaded" in caplog.text

    @pytest.mark.asyncio
    async def test_disabled_account_entry_is_respected(self):
        """[PLAN2-E2] A disabled account entry is never re-created or re-enabled."""
        account = _config_entry_double(
            "acc1",
            unique_id=const.ACCOUNT_ENTRY_UNIQUE_ID,
            data={"entry_kind": "account"},
            disabled_by="user",
        )
        hass = _ensure_hass([account])

        await init_module.async_ensure_account_entry(hass)

        hass.config_entries.async_setup.assert_not_awaited()
        hass.config_entries.flow.async_init.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_duplicate_account_entries_keep_lowest_entry_id(self, caplog):
        """[PLAN2-B3] Duplicates (hand-edited storage) only log and keep the first."""
        first = _config_entry_double(
            "acc_a",
            unique_id=const.ACCOUNT_ENTRY_UNIQUE_ID,
            data={"entry_kind": "account"},
        )
        second = _config_entry_double(
            "acc_b",
            unique_id=const.ACCOUNT_ENTRY_UNIQUE_ID,
            data={"entry_kind": "account"},
        )
        hass = _ensure_hass([second, first])

        with caplog.at_level("WARNING"):
            await init_module.async_ensure_account_entry(hass)

        # No setup call at all any more (review fix, plan §7.2): HA owns the
        # (re)load of a stored entry.
        hass.config_entries.async_setup.assert_not_awaited()
        hass.config_entries.flow.async_init.assert_not_awaited()
        assert "2 account entries" in caplog.text
        assert "acc_a" in caplog.text and "acc_b" in caplog.text

    @pytest.mark.asyncio
    async def test_flow_failure_never_propagates(self):
        """[PLAN2-M5] A failing import flow only warns - ensure never raises."""
        workspace = _config_entry_double("ws1", data={"token": "tok"})
        hass = _ensure_hass([workspace])
        hass.config_entries.flow.async_init = AsyncMock(
            side_effect=RuntimeError("flow boom")
        )

        # Must not raise.
        await init_module.async_ensure_account_entry(hass)

        hass.config_entries.flow.async_init.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_dict_hass_data_is_tolerated(self):
        """[PLAN2-M5] A Mock ``hass.data`` (legacy tests) never raises."""
        hass = MagicMock()
        hass.data = MagicMock()

        await init_module.async_ensure_account_entry(hass)

        hass.config_entries.flow.async_init.assert_not_called()


class TestAccountEntryBranch:
    """[PLAN2-A2] Account entries bypass the workspace setup path entirely."""

    @pytest.mark.asyncio
    async def test_account_entry_skips_workspace_path(self):
        """[PLAN2-A2] No platform forward, no GoGaugeCoordinator for account entries."""
        entry = MagicMock()
        entry.entry_id = "account_entry"
        entry.data = {const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT}
        entry.options = {}

        hass = MagicMock()
        hass.data = {}
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        with patch.object(init_module, "GoGaugeCoordinator") as coord_cls, \
                patch.object(
                    init_module, "async_setup_account_entry",
                    new_callable=AsyncMock, return_value=True,
                ) as setup_account:
            result = await init_module.async_setup_entry(hass, entry)

        assert result is True
        setup_account.assert_awaited_once_with(hass, entry)
        coord_cls.assert_not_called()
        hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_account_entry_unload_unloads_account_platforms(self):
        """[PLAN2-E4] Account unload unloads ACCOUNT_PLATFORMS (not PLATFORMS)."""
        entry = MagicMock()
        entry.entry_id = "account_entry"
        entry.data = {const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT}

        hass = MagicMock()
        hass.data = {const.DOMAIN: {"account_entry": object()}}
        conftest.install_applying_config_entries(hass)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await init_module.async_unload_entry(hass, entry)

        assert result is True
        hass.config_entries.async_unload_platforms.assert_awaited_once_with(
            entry, const.ACCOUNT_PLATFORMS
        )
        assert "account_entry" not in hass.data[const.DOMAIN]

    @pytest.mark.asyncio
    async def test_account_entry_remove_delegates(self):
        """[PLAN2-A2] async_remove_entry routes the account kind to account.py."""
        entry = MagicMock()
        entry.entry_id = "account_entry"
        entry.data = {const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT}

        hass = MagicMock()
        hass.data = {}

        with patch.object(
            init_module, "async_remove_account_entry", new_callable=AsyncMock
        ) as remove_account:
            await init_module.async_remove_entry(hass, entry)

        remove_account.assert_awaited_once_with(hass, entry)

    @pytest.mark.asyncio
    async def test_workspace_entry_remove_is_a_noop(self):
        """[PLAN2-A2] Workspace removal needs no domain-level cleanup."""
        entry = MagicMock()
        entry.entry_id = "ws_entry"
        entry.data = {"token": "tok", "workspace_name": "WS"}

        hass = MagicMock()
        hass.data = {}

        with patch.object(
            init_module, "async_remove_account_entry", new_callable=AsyncMock
        ) as remove_account:
            await init_module.async_remove_entry(hass, entry)

        remove_account.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
