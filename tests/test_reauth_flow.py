#!/usr/bin/env python3
"""Reauth-flow tests (spec H, REQ-017).

[REQ-017] An HTTP 401 from opencode.ai is an authentication problem, not a
transient network error: the client raises ``GoGaugeAuthError``, the coordinator
marks the affected workspace with the STRUCTURED status ``"auth_error"`` and
re-raises ``ConfigEntryAuthFailed`` so Home Assistant starts the reauth UI. The
reauth config-flow step validates a fresh token and replaces ONLY the token in
``entry.data`` - ``entry_id`` and ``unique_id`` stay untouched (iron rule).
HTTP 403 without an ``EntitlementError`` stays a transient
``error``/``no_subscription`` and must NEVER be folded into the auth path.

There is no ``docs/REQUIREMENTS.md`` in this repo; REQ-017 continues the
numbering of tests/test_derived_usage.py (REQ-013..REQ-016) for the reauth key
contract.

Base ``homeassistant.*`` fakes (including a real ``Exception`` subclass for
``homeassistant.exceptions.ConfigEntryAuthFailed``) come from tests/conftest.py.
Compounding convention of this repo: ``_load`` the real modules dynamically and
customize only the coordinator/flow base classes per test.
"""
from __future__ import annotations

import asyncio
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

# Re-assert the shared fakes immediately before importing the real modules:
# other test modules run their own inline sys.modules setup at collection time
# and can replace the entries conftest installed (alphabetical collection order
# places several of them before this file). install_ha_stubs() also guarantees
# the ConfigEntryAuthFailed fake is a real BaseException subclass usable in
# `pytest.raises` and in the integration's `except` clause.
conftest.install_ha_stubs()

# NOTE: the former "voluptuous" test-isolation repair is gone - the three test
# modules that used to install a `_Flexible` FAKE for the real pip package into
# sys.modules no longer do (see tests/test_api_status_and_subscription.py,
# test_coordinator_field_validation.py, test_binary_sensor_availability.py and
# tests/conftest.py). The genuine module is therefore used directly.

# DataUpdateCoordinator must be subscriptable (`DataUpdateCoordinator[dict]`)
# and constructible; UpdateFailed must be a real exception because the model
# cycle raises it. These per-test base-class customizations are intentionally
# NOT centralized (see conftest docstring).
_uc = sys.modules["homeassistant.helpers.update_coordinator"]


def _class_getitem(cls, item):
    """Make the fake DataUpdateCoordinator subscriptable."""
    return cls


_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {
        "__class_getitem__": classmethod(_class_getitem),
        "__init__": lambda self, hass, logger, name=None, update_interval=None: None,
    },
)
_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})


class _CoordinatorEntity:
    """Fake mirroring HA's CoordinatorEntity.available semantics.

    ``available`` is ``False`` whenever the last coordinator refresh failed -
    exactly the real property the ApiStatusSensor availability override has to
    bypass on the auth path. Without this (a bare _Flexible throwaway class
    would have no ``available``) the regression test below could not
    distinguish the override from the inherited behaviour.
    """

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


_uc.CoordinatorEntity = _CoordinatorEntity


def _enum_like(*members):
    """Enum-member stand-in (upper-cased members), as the sensors read them."""
    return type("EnumStub", (), {m.upper(): m for m in members})


# sensor.py reads these enum members at class-definition time.
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like(
    "measurement", "total", "total_increasing")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like(
    "timestamp", "duration", "enum")


def _load(name: str, path: str):
    """Dynamically import a module from file, bypassing sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
sensor_module = _load("go_gauge.sensor", str(BASE / "sensor.py"))
config_flow = _load("go_gauge.config_flow", str(BASE / "config_flow.py"))
init_module = _load("go_gauge.__init__", str(BASE / "__init__.py"))


# ---------------------------------------------------------------------------
# HTTP response / session doubles
# ---------------------------------------------------------------------------

def _mock_response(status: int, json_body: dict | None = None) -> AsyncMock:
    """Aiohttp-style async context manager response with a fixed status."""
    resp = AsyncMock()
    resp.status = status
    resp.__aenter__.return_value = resp
    resp.__aexit__.return_value = None
    if json_body is not None:
        resp.json = AsyncMock(return_value=json_body)
    return resp


def _session_with(response) -> MagicMock:
    """A session whose ``get()`` always yields ``response``."""
    session = MagicMock()
    session.get.return_value = response
    return session


def _patch_probe_session(monkeypatch, status: int) -> None:
    """Route ``_probe_token``'s HTTP call through a mocked session.

    Patched on the config_flow module itself (where the name is bound via
    ``from ... import async_get_clientsession``), not on the origin module.
    """
    session = _session_with(_mock_response(status))
    monkeypatch.setattr(config_flow, "async_get_clientsession", lambda hass: session)


# ---------------------------------------------------------------------------
# Coordinator double: real _async_update_data / _snapshot on lightweight state
# ---------------------------------------------------------------------------

class _StubClient:
    """Minimal API client returning a fixed usage dict or raising an error."""

    def __init__(self, usage_response: dict | None = None, exc: Exception | None = None):
        self._usage_response = usage_response
        self._exc = exc

    async def fetch_usage(self, token: str) -> dict:
        if self._exc is not None:
            raise self._exc
        assert self._usage_response is not None
        return self._usage_response

    async def fetch_models(self) -> list:
        return []


class _StubCoordinator(coord.GoGaugeCoordinator):
    """Binds the REAL _async_update_data without running the real __init__.

    Subclassing (rather than duck-typing) keeps the inherited ``_snapshot`` /
    ``_record_usage_sample`` helpers intact while the attribute set mirrors
    tests/test_coordinator_parsing.py's ``_MockCoordinator``.
    """

    def __init__(self, client, data: dict | None = None) -> None:
        self.hass = None
        self._tokens = ["ws-token"]
        self._client = client
        self.auto_usage = True
        self.usage_minutes = const.DEFAULT_USAGE_REFRESH_MINUTES
        self.auto_models = False
        self.models_minutes = const.DEFAULT_MODELS_REFRESH_MINUTES
        self.warn_percent = const.DEFAULT_WARN_PERCENT
        self._skip_reload = False
        self.last_models_fetch = None
        self.last_usage_fetch = None
        self.data = data
        self.is_catalog_owner = False
        self.last_update_success = True


# ---------------------------------------------------------------------------
# [REQ-017] 401 -> GoGaugeAuthError at the client
# ---------------------------------------------------------------------------

def test_client_fetch_usage_401_raises_go_gauge_auth_error():
    """[REQ-017] HTTP 401 raises GoGaugeAuthError, not a bare aiohttp error."""
    client = coord.OpenCodeGoApiClient(_session_with(_mock_response(401)))

    with pytest.raises(const.GoGaugeAuthError) as excinfo:
        asyncio.run(client.fetch_usage("expired-token"))

    # Exact-type assertion: a bare aiohttp.ClientResponseError (or any wrapper)
    # would let the coordinator's generic `except Exception` swallow the 401
    # into a transient "error" instead of triggering reauth.
    assert type(excinfo.value) is const.GoGaugeAuthError
    assert "401" in str(excinfo.value)


def test_client_fetch_usage_403_entitlement_stays_no_subscription():
    """[REQ-017] HTTP 403 EntitlementError is NOT an auth case -> no_subscription."""
    body = {"error": {"type": "EntitlementError",
                      "message": "OpenCode Go subscription required."}}
    client = coord.OpenCodeGoApiClient(_session_with(_mock_response(403, body)))

    result = asyncio.run(client.fetch_usage("valid-token"))

    assert result["status"] == "no_subscription"
    assert result["usage"] == {}
    assert "subscription" in result["note"].lower()


# ---------------------------------------------------------------------------
# [REQ-017] 401 -> ConfigEntryAuthFailed in the coordinator
# ---------------------------------------------------------------------------

def test_coordinator_401_marks_auth_error_and_raises_auth_failed():
    """[REQ-017] GoGaugeAuthError -> status auth_error, last_update_success False."""
    client = _StubClient(exc=const.GoGaugeAuthError("HTTP 401 - token invalid/expired"))
    c = _StubCoordinator(client)

    with pytest.raises(coord.ConfigEntryAuthFailed):
        asyncio.run(c._async_update_data())

    # The snapshot must be published BEFORE the raise so the ApiStatusSensor
    # shows auth_error instead of stale/unknown data.
    ws = c.data["workspaces"][0]
    assert ws["status"] == "auth_error"
    assert "401" in ws["note"]
    assert c.last_update_success is False


def test_coordinator_401_via_real_client_marks_auth_error():
    """[REQ-017] End-to-end: real 401 session -> client -> coordinator reauth."""
    client = coord.OpenCodeGoApiClient(_session_with(_mock_response(401)))
    c = _StubCoordinator(client)

    with pytest.raises(coord.ConfigEntryAuthFailed):
        asyncio.run(c._async_update_data())

    assert c.data["workspaces"][0]["status"] == "auth_error"
    assert c.last_update_success is False


def test_coordinator_403_no_subscription_does_not_raise_auth_failed():
    """[REQ-017] A no_subscription response must not be treated as auth failure."""
    client = _StubClient(usage_response={
        "usage": {}, "status": "no_subscription",
        "note": "OpenCode Go subscription required.",
    })
    c = _StubCoordinator(client)

    result = asyncio.run(c._async_update_data())  # must NOT raise

    assert result["workspaces"][0]["status"] == "no_subscription"
    assert c.last_update_success is True


def test_api_status_sensor_stays_available_on_auth_path():
    """[REQ-017 / spec F] The diagnostic stays available and names auth_error.

    Regression guard for the availability hole: on the auth path
    ``last_update_success`` is False - set by the coordinator AND by Home
    Assistant when ``ConfigEntryAuthFailed`` is raised. The inherited
    ``CoordinatorEntity.available`` would therefore render this sensor
    ``unavailable`` and HA would drop its state/attributes, so the structured
    ``auth_error`` cause would be invisible. The ApiStatusSensor override must
    return True (and ONLY for this diagnostic - the data sensors stay
    unavailable on auth failure).
    """
    client = _StubClient(exc=const.GoGaugeAuthError("HTTP 401 - token invalid/expired"))
    c = _StubCoordinator(client)

    with pytest.raises(coord.ConfigEntryAuthFailed):
        asyncio.run(c._async_update_data())

    # Premise of the regression: the coordinator really is in the failed state,
    # so the inherited availability would be False.
    assert c.last_update_success is False

    entry = conftest.StrictConfigEntry(entry_id="entry-stable")
    status_sensor = sensor_module.ApiStatusSensor(c, entry, key="ws1")

    assert status_sensor.available is True
    assert status_sensor.native_value == "auth_error"


# ---------------------------------------------------------------------------
# [REQ-017] Reauth config-flow steps
# ---------------------------------------------------------------------------

def _new_flow(entry) -> config_flow.GoGaugeConfigFlow:
    """Build a flow with just enough HA surface for the reauth steps."""
    flow = config_flow.GoGaugeConfigFlow()
    flow.hass = MagicMock()
    flow.hass.config_entries = conftest.make_applying_config_entries()
    flow.hass.config_entries.async_get_entry = MagicMock(return_value=entry)
    flow.context = {"entry_id": entry.entry_id} if entry is not None else {}
    flow.async_abort = MagicMock(
        side_effect=lambda reason: {"type": "abort", "reason": reason}
    )
    flow.async_show_form = MagicMock(
        side_effect=lambda step_id=None, data_schema=None, errors=None, **kw: {
            "type": "form", "step_id": step_id, "errors": errors or {},
        }
    )
    return flow


def _reauth_entry() -> conftest.StrictConfigEntry:
    """Entry double with a stable entry_id/unique_id and an expired token."""
    return conftest.StrictConfigEntry(
        entry_id="entry-stable",
        unique_id="go_gauge_0123456789abcdef",
        data={"workspace_name": "My Workspace", "token": "old-expired-token"},
    )


@pytest.mark.asyncio
async def test_reauth_confirm_valid_token_keeps_identity(monkeypatch):
    """[REQ-017] Reauth replaces ONLY the token; entry_id/unique_id stay stable."""
    _patch_probe_session(monkeypatch, 200)
    entry = _reauth_entry()
    flow = _new_flow(entry)

    form = await flow.async_step_reauth(entry.data)
    assert form["type"] == "form"
    assert form["step_id"] == "reauth_confirm"

    result = await flow.async_step_reauth_confirm({"token": "new-valid-token"})

    assert result == {"type": "abort", "reason": "reauth_successful"}
    assert entry.data["token"] == "new-valid-token"
    assert entry.data["workspace_name"] == "My Workspace"  # preserved
    assert entry.entry_id == "entry-stable"                # unchanged
    assert entry.unique_id == "go_gauge_0123456789abcdef"  # unchanged


@pytest.mark.asyncio
async def test_reauth_confirm_whitespace_token_is_stripped(monkeypatch):
    """[REQ-017] The validated/stored token is whitespace-stripped."""
    _patch_probe_session(monkeypatch, 200)
    entry = _reauth_entry()
    flow = _new_flow(entry)
    await flow.async_step_reauth(entry.data)

    await flow.async_step_reauth_confirm({"token": "  padded-token  "})

    assert entry.data["token"] == "padded-token"


@pytest.mark.asyncio
async def test_reauth_confirm_invalid_token_surfaces_error_without_change(monkeypatch):
    """[REQ-017] invalid_token probe -> base error, entry left untouched."""
    async def _invalid_probe(hass, token):
        return False, "invalid_token"

    monkeypatch.setattr(config_flow, "_probe_token", _invalid_probe)
    entry = _reauth_entry()
    flow = _new_flow(entry)
    await flow.async_step_reauth(entry.data)

    result = await flow.async_step_reauth_confirm({"token": "still-bad-token"})

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_token"
    # No write happened: token and identity are exactly as before.
    assert entry.data["token"] == "old-expired-token"
    assert entry.unique_id == "go_gauge_0123456789abcdef"
    flow.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_confirm_empty_token_is_missing_fields(monkeypatch):
    """[REQ-017] Empty token -> missing_fields, entry left untouched."""
    entry = _reauth_entry()
    flow = _new_flow(entry)
    await flow.async_step_reauth(entry.data)

    result = await flow.async_step_reauth_confirm({"token": "   "})

    assert result["errors"]["base"] == "missing_fields"
    assert entry.data["token"] == "old-expired-token"
    flow.hass.config_entries.async_update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_reauth_without_context_aborts_reauth_failed():
    """[REQ-017] Missing entry context -> abort reauth_failed."""
    flow = _new_flow(None)

    result = await flow.async_step_reauth({})

    assert result == {"type": "abort", "reason": "reauth_failed"}


@pytest.mark.asyncio
async def test_reauth_confirm_without_entry_aborts_reauth_failed():
    """[REQ-017] Confirm step reached without a resolved entry -> reauth_failed."""
    flow = _new_flow(None)

    result = await flow.async_step_reauth_confirm({"token": "some-token"})

    assert result == {"type": "abort", "reason": "reauth_failed"}


# ---------------------------------------------------------------------------
# [REQ-017] async_setup_entry auth propagation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_setup_entry_reraises_auth_failed_and_cleans_registry():
    """[REQ-017] First refresh auth failure re-raises and drops the entry."""
    entry = MagicMock()
    entry.entry_id = "entry-auth"
    entry.data = {"token": "expired", "workspace_name": "WS"}
    entry.options = {}

    hass = MagicMock()
    hass.data = {"go_gauge": {"entry-auth": MagicMock(), "_catalog_owner": "entry-auth"}}

    async def _fail_first_refresh():
        raise init_module.ConfigEntryAuthFailed("HTTP 401")

    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = _fail_first_refresh

    with patch.object(init_module, "GoGaugeCoordinator",
                      MagicMock(return_value=coordinator)):
        with pytest.raises(init_module.ConfigEntryAuthFailed):
            await init_module.async_setup_entry(hass, entry)

    # Cleaned up for reauth, not silently swallowed into the tolerant path.
    assert "entry-auth" not in hass.data["go_gauge"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
