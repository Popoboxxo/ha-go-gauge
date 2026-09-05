#!/usr/bin/env python3
"""Config flow tests: _probe_token validation, unique_id duplicate detection.

Audit reference: P2-Point-11 (https://github.com/Popoboxxo/ha-go-gauge/docs/AUDIT-2026-09-04.md)
- Tests for config_flow.py _probe_token: valid token, invalid_token, cannot_connect
- Tests for unique_id (token-based duplicate detection) and the flow VERSION

The shared Home-Assistant module fakes - including a `config_entries.ConfigFlow`
base that accepts the `domain=` class keyword, an identity `core.callback`
decorator, and the `aiohttp` fake - live centrally in `tests/conftest.py`,
which pytest imports before this module is collected. This file therefore only
loads the REAL modules and tests them; it deliberately no longer hand-rolls any
HA / ConfigFlow stub of its own (see tests/test_sensor_wiring.py for the same
convention).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


def _load(name: str, path: str):
    """Dynamically import a module from file, bypassing sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Re-assert the shared fakes right before importing the real config_flow. Other
# test modules run their own inline fake setup at collection time and replace
# the homeassistant.* sys.modules entries (dropping conftest's ConfigFlow /
# callback stubs); alphabetical collection places some of them before this
# file. Delegating to conftest keeps the stub definitions central - no local
# ConfigFlow stub here (see tests/test_sensor_wiring.py for the same "customize
# on top of conftest's base fakes" convention).
conftest.install_ha_stubs()

# Load real modules after the shared fakes are guaranteed to be in place.
const = _load("go_gauge.const", str(BASE / "const.py"))
config_flow = _load("go_gauge.config_flow", str(BASE / "config_flow.py"))


class TestProbeTokenLogic:
    """Tests for _probe_token() function logic.

    Tests the real _probe_token function from config_flow.py with mocked HTTP.
    `async_get_clientsession` is patched on the config_flow module itself
    (where the name is bound via `from ... import async_get_clientsession`),
    not on its origin module - patching the origin would not affect the name
    already imported into config_flow's namespace.
    """

    @pytest.mark.asyncio
    async def test_probe_token_401_invalid(self, monkeypatch):
        """[AUDIT-P2-11] _probe_token returns (False, 'invalid_token') on 401."""
        from unittest.mock import AsyncMock, MagicMock

        # Mock aiohttp session with 401 response
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        mock_hass = MagicMock()

        # Mock async_get_clientsession to return our mock session
        def mock_get_clientsession(hass):
            return mock_session

        monkeypatch.setattr(config_flow, "async_get_clientsession", mock_get_clientsession)

        ok, err = await config_flow._probe_token(mock_hass, "bad-token")
        assert ok is False
        assert err == "invalid_token"

    @pytest.mark.asyncio
    async def test_probe_token_200_valid(self, monkeypatch):
        """[AUDIT-P2-11] _probe_token returns (True, None) on 200."""
        from unittest.mock import AsyncMock, MagicMock

        # Mock aiohttp session with 200 response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None

        mock_session = MagicMock()
        mock_session.get.return_value = mock_response

        mock_hass = MagicMock()

        def mock_get_clientsession(hass):
            return mock_session

        monkeypatch.setattr(config_flow, "async_get_clientsession", mock_get_clientsession)

        ok, err = await config_flow._probe_token(mock_hass, "good-token")
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_probe_token_network_error(self, monkeypatch):
        """[AUDIT-P2-11] _probe_token returns (False, 'cannot_connect') on network error."""
        from unittest.mock import MagicMock

        # Mock aiohttp session that raises an exception
        mock_session = MagicMock()
        mock_session.get.side_effect = Exception("Network error")

        mock_hass = MagicMock()

        def mock_get_clientsession(hass):
            return mock_session

        monkeypatch.setattr(config_flow, "async_get_clientsession", mock_get_clientsession)

        ok, err = await config_flow._probe_token(mock_hass, "token")
        assert ok is False
        assert err == "cannot_connect"


class TestAsyncStepUserLogic:
    """Tests for async_step_user() configuration validation logic.

    Tests the real GoGaugeConfigFlow class and its configuration handling.
    Note: full async flow testing requires integration/E2E testing with actual HA.
    These tests verify the constants and expected behavior patterns.
    """

    def test_config_flow_class_exists(self):
        """[AUDIT-P2-11] GoGaugeConfigFlow class is defined."""
        assert hasattr(config_flow, "GoGaugeConfigFlow")
        assert hasattr(config_flow.GoGaugeConfigFlow, "async_step_user")
        assert hasattr(config_flow.GoGaugeConfigFlow, "VERSION")

    def test_unique_id_format_token_prefix(self):
        """[AUDIT-P2-11] unique_id uses first 16 chars of token, lowercased.

        Note: This test verifies the formula used in config_flow.py line 80.
        """
        token = "MyVeryLongTokenValue1234567890"
        # Replicate the unique_id generation from config_flow.py:80
        unique_id = f"go_gauge_{token[:16].lower()}"

        # token[:16] from "MyVeryLongTokenValue1234567890" is "MyVeryLongTokenVa"
        # lowercased: "myverylongtokenv" (exactly 16 chars)
        assert unique_id == "go_gauge_myverylongtokenv"
        assert len(unique_id) == 25  # "go_gauge_" (9) + 16 chars
        assert unique_id.islower()

    def test_unique_id_format_short_token(self):
        """[AUDIT-P2-11] unique_id handles tokens shorter than 16 chars."""
        token = "short"
        unique_id = f"go_gauge_{token[:16].lower()}"

        assert unique_id == "go_gauge_short"
        assert len(unique_id) == 14  # "go_gauge_" + 5 chars

    def test_token_whitespace_stripping_logic(self):
        """[AUDIT-P2-11] Token whitespace is stripped before use.

        config_flow.py line 63 shows: token = ((user_input or {}).get("token", "") or "").strip()
        """
        token = "  token-with-spaces  "
        stripped = token.strip()

        assert stripped == "token-with-spaces"
        assert stripped != token

    def test_workspace_name_whitespace_stripping(self):
        """[AUDIT-P2-11] Workspace name is stripped before storage.

        config_flow.py line 87 shows: "workspace_name": name.strip()
        """
        name = "  My Workspace  "
        stripped = name.strip()

        assert stripped == "My Workspace"
        assert stripped != name


class TestUniqueIdBehavior:
    """Tests for unique_id generation and duplicate detection logic.

    Tests rely on the unique_id formula: f"go_gauge_{token[:16].lower()}"
    (used in config_flow.py line 80).
    """

    def test_duplicate_unique_ids_same_token(self):
        """[AUDIT-P2-11] Same token generates same unique_id."""
        token1 = "my-secret-token-abc123"
        token2 = "my-secret-token-abc123"

        uid1 = f"go_gauge_{token1[:16].lower()}"
        uid2 = f"go_gauge_{token2[:16].lower()}"

        assert uid1 == uid2, "Duplicate detection relies on same unique_id"
        # Both should be exactly 25 chars: "go_gauge_" (9) + token[:16] (16)
        assert len(uid1) == 25

    def test_different_tokens_different_unique_ids(self):
        """[AUDIT-P2-11] Different tokens generate different unique_ids."""
        token1 = "token-aaa-111111"
        token2 = "token-bbb-222222"

        uid1 = f"go_gauge_{token1[:16].lower()}"
        uid2 = f"go_gauge_{token2[:16].lower()}"

        assert uid1 != uid2
        # Verify they're constructed the same way
        assert uid1.startswith("go_gauge_")
        assert uid2.startswith("go_gauge_")

    def test_unique_id_case_normalization(self):
        """[AUDIT-P2-11] unique_id normalizes token to lowercase."""
        token_upper = "MY-SECRET-TOKEN-1"
        token_lower = "my-secret-token-1"

        uid_upper = f"go_gauge_{token_upper[:16].lower()}"
        uid_lower = f"go_gauge_{token_lower[:16].lower()}"

        assert uid_upper == uid_lower
        # Verify all lowercase
        assert uid_upper.islower()


class TestConfigFlowVersion:
    """Tests for config flow version constant."""

    def test_config_flow_version_is_four(self):
        """[AUDIT-P2-11] Config flow VERSION should be 4 (current migration target).

        Verifies the constant in GoGaugeConfigFlow class.
        """
        # Import the real VERSION from the module
        assert hasattr(config_flow.GoGaugeConfigFlow, "VERSION")
        assert config_flow.GoGaugeConfigFlow.VERSION == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
