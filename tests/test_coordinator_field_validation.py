#!/usr/bin/env python3
"""Regression/audit test (2026-09-04): the usage-response parser in
coordinator.py uses several dict.get(...) calls that silently return None
if the opencode.ai API renames a field. This test verifies the defensive
validation added on top of the (unchanged) parsing logic: a missing
expected field must produce ONE aggregated _LOGGER.warning() per update
cycle, WITHOUT changing the existing fallback behaviour (still None / old
value, no crash, no altered return shape).

NOTE: this does NOT verify real API field names against the live
opencode.ai API (no network access here) - it only verifies that the
new "make it visible instead of silent" logging works as intended.
"""
import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


class _Flexible(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


for _name in [
    "homeassistant", "homeassistant.core", "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.entity_platform", "homeassistant.components",
    "homeassistant.components.sensor", "homeassistant.config_entries",
    "homeassistant.data_entry_flow", "voluptuous", "aiohttp",
]:
    sys.modules[_name] = _Flexible(_name)
sys.modules["homeassistant"].__path__ = []


def _class_getitem(cls, item):
    return cls


_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,),
    {
        "__class_getitem__": classmethod(_class_getitem),
        "__init__": lambda self, hass, logger, name=None, update_interval=None: None,
    },
)
_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))


class _FakeClient:
    """Stand-in for OpenCodeGoApiClient - returns a canned response."""

    def __init__(self, response: dict):
        self._response = response

    async def fetch_usage(self, token: str) -> dict:
        return self._response

    async def fetch_models(self) -> list:
        return []


def _make_coordinator(response: dict) -> "coord.GoGaugeCoordinator":
    c = coord.GoGaugeCoordinator.__new__(coord.GoGaugeCoordinator)
    c.hass = None
    c._tokens = ["dummy-token"]
    c._client = _FakeClient(response)
    c.auto_usage = True
    c.usage_minutes = const.DEFAULT_USAGE_REFRESH_MINUTES
    c.auto_models = False
    c.models_minutes = const.DEFAULT_MODELS_REFRESH_MINUTES
    c.warn_percent = const.DEFAULT_WARN_PERCENT
    c._skip_reload = False
    c.last_models_fetch = None
    c.last_usage_fetch = None
    c.data = None
    c.is_catalog_owner = False
    return c


def test_missing_fields_logged_once_but_fallback_behaviour_unchanged(caplog):
    """A response missing 'resetsAt' on one window and 'weekly' entirely must
    still parse without crashing (None fallback preserved) AND must produce
    exactly one aggregated warning naming the missing fields."""
    response = {
        "usage": {
            "rolling": {"percent": 12.5, "status": "ok"},  # resetsAt missing
            # "weekly" missing entirely
            "monthly": {"percent": 40.0, "status": "ok", "resetsAt": "2026-10-01T00:00:00Z"},
        },
        "status": "ok",
    }
    c = _make_coordinator(response)

    with caplog.at_level(logging.WARNING, logger=coord._LOGGER.name):
        result = asyncio.run(c._async_update_data())

    ws = result["workspaces"][0]
    # Fallback behaviour unchanged: missing field -> None, no crash.
    assert ws["windows"]["5h"]["resets_at"] is None
    assert ws["windows"]["week"]["percent"] is None
    assert ws["windows"]["month"]["percent"] == 40.0

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "erwartete Felder fehlen" in r.getMessage()]
    assert len(warnings) == 1, f"expected exactly one aggregated warning, got {warnings}"
    msg = warnings[0].getMessage()
    assert "usage.weekly" in msg
    assert "usage.rolling.resetsAt" in msg
    assert "usage.monthly.resetsAt" not in msg  # present field must not be flagged


def test_no_warning_when_all_expected_fields_present(caplog):
    response = {
        "usage": {
            "rolling": {"percent": 1.0, "status": "ok", "resetsAt": "2026-09-05T00:00:00Z"},
            "weekly": {"percent": 2.0, "status": "ok", "resetsAt": "2026-09-05T00:00:00Z"},
            "monthly": {"percent": 3.0, "status": "ok", "resetsAt": "2026-09-05T00:00:00Z"},
        },
        "status": "ok",
    }
    c = _make_coordinator(response)

    with caplog.at_level(logging.WARNING, logger=coord._LOGGER.name):
        asyncio.run(c._async_update_data())

    warnings = [r for r in caplog.records if "erwartete Felder fehlen" in r.getMessage()]
    assert warnings == []


def test_synthetic_no_subscription_response_does_not_trigger_false_positive(caplog):
    """The client's own synthesized 403/EntitlementError response has an
    intentionally empty 'usage': {} - this must NOT be flagged as schema
    drift (it's a known, handled state, not a field-name mismatch)."""
    response = {"usage": {}, "status": "no_subscription", "note": "no active subscription"}
    c = _make_coordinator(response)

    with caplog.at_level(logging.WARNING, logger=coord._LOGGER.name):
        result = asyncio.run(c._async_update_data())

    assert result["workspaces"][0]["status"] == "no_subscription"
    warnings = [r for r in caplog.records if "erwartete Felder fehlen" in r.getMessage()]
    assert warnings == []


if __name__ == "__main__":
    import _pytest.logging  # noqa: F401  (ensures caplog fixture available if run standalone)
