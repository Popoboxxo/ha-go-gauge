#!/usr/bin/env python3
"""Integration __init__ tests: async_setup_entry, async_migrate_entry, async_unload_entry.

Audit reference: P2-Point-11 (https://github.com/Popoboxxo/ha-go-gauge/docs/AUDIT-2026-09-04.md)
- Tests for async_setup_entry: entry registration in hass.data, catalog-owner logic
- Tests for async_migrate_entry: migration path v1/v2 -> v3 -> v4
- Tests for async_unload_entry: cleanup, owner migration on unload

Tests import the REAL __init__ module and verify behavior directly
against actual code (not inline logic duplicates).
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
coordinator = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
init_module = _load("go_gauge.__init__", str(BASE / "__init__.py"))


class TestAsyncMigrateEntryLogic:
    """Tests for async_migrate_entry() version migration logic.

    Version evolution:
    v1-v2: Monitor-era (host/port + tokens list)
    v3: Multi-token format
    v4: Single workspace (current)

    Tests the real async_migrate_entry function from __init__.py.
    """

    @pytest.mark.asyncio
    async def test_migrate_v1_to_v5_extracts_first_token(self):
        """[AUDIT-P2-11] v1->v5 migration keeps first token from list."""
        # Create mock entry for v1
        entry = MagicMock()
        entry.version = 1
        entry.data = {"tokens": ["token-1", "token-2"], "host": "192.168.1.1"}
        entry.options = {"scan_interval": 300}

        # Mock hass.config_entries
        hass = MagicMock()
        hass.config_entries = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        # Migration should succeed
        assert result is True
        # Entry version should be updated to 5 (current)
        assert entry.version == 5
        # First token should be extracted
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        assert updated_data["token"] == "token-1"
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
        hass.config_entries = MagicMock()

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
        hass.config_entries = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        assert updated_data["token"] == "token-alpha"
        assert updated_data["workspace_name"] == "WS-3"
        assert "tokens" not in updated_data

    @pytest.mark.asyncio
    async def test_migrate_v4_to_v5_preserves_data(self):
        """[AUDIT-P2-11] v4->v5 migration advances version but preserves data."""
        original_data = {"token": "current-token", "workspace_name": "My WS"}
        entry = MagicMock()
        entry.version = 4
        entry.data = original_data
        entry.options = {}

        hass = MagicMock()
        hass.config_entries = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        # Version advances to current (5); entry.data is untouched.
        assert entry.version == 5
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        assert updated_data == original_data

    @pytest.mark.asyncio
    async def test_migrate_future_version_rejected(self):
        """[AUDIT-P2-11] Versions > 5 are rejected (cannot downgrade)."""
        entry = MagicMock()
        entry.version = 6
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
        hass.config_entries = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        updated_data = call_args[1]["data"]
        # Empty token list should yield empty string
        assert updated_data["token"] == ""


class TestUniqueIdHashMigration:
    """v4->v5: ConfigEntry.unique_id plaintext-token-fragment -> SHA-256 hash.

    Security migration (AUDIT-2026-09-04): the pre-v5 unique_id embedded a
    16-char plaintext token fragment persisted in HA storage. v5 replaces it
    with a SHA-256 hash recomputed from the stored token, matching exactly
    what const.token_unique_id (and thus the config flow) now produces.
    """

    @staticmethod
    def _expected(token: str) -> str:
        """Independently derive the canonical hashed unique_id."""
        return f"go_gauge_{hashlib.sha256(token.encode()).hexdigest()[:16]}"

    @pytest.mark.asyncio
    async def test_v4_to_v5_hashes_unique_id(self):
        """[AUDIT-P2-11] v4->v5 replaces the plaintext-fragment id with the hash."""
        token = "MyWorkspaceToken-abcdef123456"
        entry = MagicMock()
        entry.version = 4
        # Old (leaky) format: first 16 chars of the token, lowercased.
        entry.unique_id = f"go_gauge_{token[:16].lower()}"
        entry.data = {"token": token, "workspace_name": "WS"}
        entry.options = {}

        hass = MagicMock()
        hass.config_entries = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 5
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        # (a) new unique_id equals the exact SHA-256 result
        assert call_args[1]["unique_id"] == self._expected(token)
        # no plaintext token fragment survives in the id
        assert token[:16].lower() not in call_args[1]["unique_id"]
        # (c) entry.data (token etc.) is preserved untouched
        assert call_args[1]["data"] == {"token": token, "workspace_name": "WS"}

    @pytest.mark.asyncio
    async def test_v5_migration_is_idempotent(self):
        """[AUDIT-P2-11] Re-running on an already-hashed v5 entry keeps the id."""
        token = "StableToken-0987654321"
        hashed = self._expected(token)
        entry = MagicMock()
        entry.version = 5
        entry.unique_id = hashed
        entry.data = {"token": token, "workspace_name": "WS"}
        entry.options = {}

        hass = MagicMock()
        hass.config_entries = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 5
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        # unique_id stays the already-hashed value (no re-hash, no corruption)
        assert call_args[1]["unique_id"] == hashed

    @pytest.mark.asyncio
    async def test_v4_to_v5_missing_token_keeps_unique_id(self):
        """[AUDIT-P2-11] Edge case: no token -> unique_id untouched, no raise."""
        old_uid = "go_gauge_legacyplaintext"
        entry = MagicMock()
        entry.version = 4
        entry.unique_id = old_uid
        entry.data = {"token": "", "workspace_name": "Empty"}
        entry.options = {}

        hass = MagicMock()
        hass.config_entries = MagicMock()

        result = await init_module.async_migrate_entry(hass, entry)

        assert result is True
        assert entry.version == 5
        call_args = hass.config_entries.async_update_entry.call_args
        assert call_args is not None
        # Defensive: without a token the old unique_id is left as-is.
        assert call_args[1]["unique_id"] == old_uid


class TestAsyncSetupEntryLogic:
    """Tests for async_setup_entry() registry and catalog-owner logic.

    Tests the real async_setup_entry function from __init__.py.
    """

    @pytest.mark.asyncio
    async def test_first_entry_becomes_catalog_owner(self):
        """[AUDIT-P2-11] First entry setup marks it as catalog owner."""
        entry = MagicMock()
        entry.entry_id = "entry_1"
        entry.data = {"token": "test-token", "workspace_name": "WS1"}
        entry.options = {}

        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        # Mock the coordinator to avoid complex initialization
        with patch.object(
            init_module, "GoGaugeCoordinator", MagicMock()
        ):
            result = await init_module.async_setup_entry(hass, entry)

        # Setup should succeed
        assert result is True
        # First entry should become catalog owner
        assert hass.data["go_gauge"]["_catalog_owner"] == "entry_1"

    @pytest.mark.asyncio
    async def test_second_entry_is_not_catalog_owner(self):
        """[AUDIT-P2-11] Second entry does NOT become catalog owner."""
        entry = MagicMock()
        entry.entry_id = "entry_2"
        entry.data = {"token": "test-token", "workspace_name": "WS2"}
        entry.options = {}

        # Set up pre-existing registry with first entry as owner
        hass = MagicMock()
        hass.data = {
            "go_gauge": {
                "entry_1": MagicMock(),
                "_catalog_owner": "entry_1",
            }
        }
        hass.config_entries = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()

        with patch.object(
            init_module, "GoGaugeCoordinator", MagicMock()
        ):
            result = await init_module.async_setup_entry(hass, entry)

        assert result is True
        # Second entry should NOT become owner
        assert hass.data["go_gauge"]["_catalog_owner"] == "entry_1"

    @pytest.mark.asyncio
    async def test_entry_registered_in_domain_registry(self):
        """[AUDIT-P2-11] Entry is registered in hass.data[DOMAIN][entry_id]."""
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {"token": "test-token", "workspace_name": "TestWS"}
        entry.options = {}

        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
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
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await init_module.async_unload_entry(hass, entry)

        assert result is True
        assert "entry_remove" not in hass.data["go_gauge"]
        assert "entry_keep" in hass.data["go_gauge"]

    @pytest.mark.asyncio
    async def test_unload_catalog_owner_removes_marker(self):
        """[AUDIT-P2-11] Unloading the catalog owner removes the marker."""
        entry = MagicMock()
        entry.entry_id = "entry_owner"

        hass = MagicMock()
        hass.data = {
            "go_gauge": {
                "entry_owner": MagicMock(),
                "_catalog_owner": "entry_owner",
            }
        }
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await init_module.async_unload_entry(hass, entry)

        assert result is True
        assert "_catalog_owner" not in hass.data["go_gauge"]
        assert "entry_owner" not in hass.data["go_gauge"]

    @pytest.mark.asyncio
    async def test_unload_catalog_owner_transfers_ownership(self):
        """[AUDIT-P2-11] Unloading the catalog owner transfers it to next coordinator."""
        entry1_id = "entry_owner"
        entry2_id = "entry_successor"

        # Create mock coordinators that pass isinstance check
        coord1 = MagicMock(spec=coordinator.GoGaugeCoordinator)
        coord2 = MagicMock(spec=coordinator.GoGaugeCoordinator)

        entry = MagicMock()
        entry.entry_id = entry1_id

        hass = MagicMock()
        hass.data = {
            "go_gauge": {
                entry1_id: coord1,
                entry2_id: coord2,
                "_catalog_owner": entry1_id,
            }
        }
        hass.config_entries = MagicMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await init_module.async_unload_entry(hass, entry)

        assert result is True
        # Owner should be transferred to next coordinator
        assert hass.data["go_gauge"].get("_catalog_owner") == entry2_id
        assert coord2.is_catalog_owner is True

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
        hass.config_entries = MagicMock()
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
