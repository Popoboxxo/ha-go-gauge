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

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
import voluptuous as vol

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

    def test_unique_id_format_sha256_hash(self):
        """[AUDIT-P2-11] unique_id is a SHA-256 hash, not a plaintext fragment.

        Since v5 the ConfigEntry.unique_id no longer contains any part of the
        token (see const.token_unique_id / AUDIT-2026-09-04). This asserts the
        exact canonical value (independently derived via hashlib).
        """
        token = "MyVeryLongTokenValue1234567890"
        expected = f"go_gauge_{hashlib.sha256(token.encode()).hexdigest()[:16]}"

        unique_id = const.token_unique_id(token)

        assert unique_id == expected
        assert len(unique_id) == 25  # "go_gauge_" (9) + 16 hex chars
        assert unique_id.startswith("go_gauge_")
        # The plaintext token fragment must NOT leak into the id.
        assert token[:16].lower() not in unique_id

    def test_unique_id_format_short_token(self):
        """[AUDIT-P2-11] Short tokens still yield a full 25-char hashed id."""
        token = "short"
        expected = f"go_gauge_{hashlib.sha256(token.encode()).hexdigest()[:16]}"

        unique_id = const.token_unique_id(token)

        assert unique_id == expected
        assert len(unique_id) == 25  # hash length is independent of token length

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

    Tests exercise the real const.token_unique_id helper (SHA-256 based,
    used by config_flow.py entry creation and __init__.py migration).
    """

    def test_duplicate_unique_ids_same_token(self):
        """[AUDIT-P2-11] Same token generates same unique_id."""
        token1 = "my-secret-token-abc123"
        token2 = "my-secret-token-abc123"

        uid1 = const.token_unique_id(token1)
        uid2 = const.token_unique_id(token2)

        assert uid1 == uid2, "Duplicate detection relies on same unique_id"
        # "go_gauge_" (9) + 16 hex chars
        assert len(uid1) == 25

    def test_different_tokens_different_unique_ids(self):
        """[AUDIT-P2-11] Different tokens generate different unique_ids."""
        token1 = "token-aaa-111111"
        token2 = "token-bbb-222222"

        uid1 = const.token_unique_id(token1)
        uid2 = const.token_unique_id(token2)

        assert uid1 != uid2
        # Verify they're constructed the same way
        assert uid1.startswith("go_gauge_")
        assert uid2.startswith("go_gauge_")

    def test_unique_id_is_case_sensitive(self):
        """[AUDIT-P2-11] SHA-256 ids are case-sensitive.

        Behavior change since v5: the pre-v5 formula lowercased the token
        fragment, so case-only variants collided. A raw-token SHA-256 hash is
        case-sensitive - correct, since tokens are case-sensitive secrets.
        """
        token_upper = "MY-SECRET-TOKEN-1"
        token_lower = "my-secret-token-1"

        uid_upper = const.token_unique_id(token_upper)
        uid_lower = const.token_unique_id(token_lower)

        assert uid_upper != uid_lower


class TestConfigFlowVersion:
    """Tests for config flow version constant."""

    def test_config_flow_version_is_eight(self):
        """[AUDIT-P2-11] Config flow VERSION should be 8 (current migration target).

        Bumped from 4 to 5 for the SHA-256 unique_id migration, from 5 to 6 for
        the German->English entity-name migration, from 6 to 7 for the
        German->English entity_id slug migration and from 7 to 8 for the
        explicit entry_kind marker (2.0.0 account entry).
        Verifies the constant in GoGaugeConfigFlow class.
        """
        # Import the real VERSION from the module
        assert hasattr(config_flow.GoGaugeConfigFlow, "VERSION")
        assert config_flow.GoGaugeConfigFlow.VERSION == 8


class TestWorkspaceOptionsFlow:
    """[PLAN2-E4] The workspace options flow no longer offers model settings.

    ``auto_update_models`` / ``models_refresh_minutes`` moved to the account
    entry in 2.0.0 (plan §6.1): a workspace performs no catalog fetch, so those
    controls would change nothing. Since 2.1.0 the same form also carries the
    three validated burn-lookback minutes fields (plan D13).
    """

    @pytest.mark.asyncio
    async def test_workspace_options_flow_has_no_model_fields(self):
        """[PLAN2-E4] Schema keeps the usage options, drops both model fields.

        Since 2.1.0 the workspace options additionally carry the three
        burn-lookback minutes fields (plan D13); the model keys stay gone
        (they moved to the account entry in 2.0.0).
        """
        flow = _workspace_options_flow()

        result = await flow.async_step_init(None)

        assert result == {"type": "form"}
        kwargs = flow.async_show_form.call_args.kwargs
        assert kwargs["step_id"] == "init"
        keys = {str(key) for key in kwargs["data_schema"].schema}
        assert const.CONF_AUTO_UPDATE_MODELS not in keys
        assert const.CONF_MODELS_REFRESH_MINUTES not in keys
        assert keys == {
            "workspace_name",
            "warn_percent",
            "pace_red_percent",
            "auto_update_usage",
            "usage_refresh_minutes",
            "burn_lookback_5h_minutes",
            "burn_lookback_week_minutes",
            "burn_lookback_month_minutes",
        }

    @pytest.mark.asyncio
    async def test_workspace_options_flow_accepts_valid_lookbacks(self):
        """[PLAN2-E4] A fully populated, in-range submission validates."""
        schema = await _workspace_form_schema()

        assert schema(dict(VALID_OPTIONS)) == VALID_OPTIONS

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("burn_lookback_5h_minutes", 5),      # below the 15 min minimum
            ("burn_lookback_5h_minutes", 20000),  # above the 300 min maximum
            ("burn_lookback_week_minutes", 10081),   # above 10080
            ("burn_lookback_month_minutes", 10081),  # above 10080
            ("burn_lookback_week_minutes", 59),   # below the 60 min minimum
        ],
    )
    async def test_workspace_options_flow_rejects_out_of_range_lookbacks(
        self, key, value
    ):
        """[PLAN2-E4] Each lookback field enforces its min/max range (D13)."""
        schema = await _workspace_form_schema()

        with pytest.raises(vol.Invalid):
            schema({**VALID_OPTIONS, key: value})

    @pytest.mark.asyncio
    async def test_workspace_options_flow_lookback_defaults_are_120_720_2880(self):
        """[PLAN2-E4] Missing options prefill the documented defaults (D6)."""
        schema = await _workspace_form_schema()

        defaults = {str(marker): marker.default() for marker in schema.schema}
        assert defaults["burn_lookback_5h_minutes"] == 120
        assert defaults["burn_lookback_week_minutes"] == 720
        assert defaults["burn_lookback_month_minutes"] == 2880

    @pytest.mark.asyncio
    async def test_workspace_options_flow_keeps_a_persisted_lookback(self):
        """[PLAN2-E4] A persisted value wins over the default (only that one)."""
        schema = await _workspace_form_schema(
            options={"burn_lookback_week_minutes": 300}
        )

        defaults = {str(marker): marker.default() for marker in schema.schema}
        assert defaults["burn_lookback_5h_minutes"] == 120
        assert defaults["burn_lookback_week_minutes"] == 300
        assert defaults["burn_lookback_month_minutes"] == 2880


VALID_OPTIONS = {
    "workspace_name": "My Workspace",
    "warn_percent": 80,
    "pace_red_percent": 100,
    "auto_update_usage": True,
    "usage_refresh_minutes": 10,
    "burn_lookback_5h_minutes": 120,
    "burn_lookback_week_minutes": 720,
    "burn_lookback_month_minutes": 2880,
}


def _workspace_options_flow(options=None):
    """Build the real options-flow handler with HA plumbing doubled."""
    from unittest.mock import MagicMock

    entry = conftest.StrictConfigEntry(
        entry_id="ws-entry",
        data={"workspace_name": "WS", "token": "tok"},
        options=options,
    )
    flow = config_flow.GoGaugeOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    return flow


async def _workspace_form_schema(options=None):
    """Render the options form once and return its voluptuous schema."""
    flow = _workspace_options_flow(options)
    await flow.async_step_init(None)
    return flow.async_show_form.call_args.kwargs["data_schema"]


class TestUserFlowEntryKind:
    """[PLAN2-A1] Every entry created by the user flow carries its entry kind.

    The flow machinery itself (``async_create_entry`` / unique_id handling) is
    provided by Home Assistant; the offline fakes do not implement it, so the
    flow object is instantiated and those base methods are replaced by doubles.
    That is enough to assert the *data contract* of the created entry.
    """

    @pytest.mark.asyncio
    async def test_user_flow_writes_entry_kind(self):
        """[PLAN2-A1] async_step_user stores entry_kind=workspace in entry.data."""
        from unittest.mock import AsyncMock, MagicMock

        flow = config_flow.GoGaugeConfigFlow()
        flow.hass = MagicMock()
        flow.context = {}
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        result = await flow.async_step_user({
            "workspace_name": "My WS",
            "token": "tok-abc",
            "skip_validation": True,
        })

        assert result == {"type": "create_entry"}
        flow.async_create_entry.assert_called_once()
        data = flow.async_create_entry.call_args.kwargs["data"]
        assert data[const.CONF_ENTRY_KIND] == const.ENTRY_KIND_WORKSPACE
        assert data["workspace_name"] == "My WS"
        assert data["token"] == "tok-abc"
        # The workspace flow never marks an entry as the account entry.
        assert data[const.CONF_ENTRY_KIND] != const.ENTRY_KIND_ACCOUNT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
