#!/usr/bin/env python3
"""Wiring of the three burn-lookback sliders and the reset button (2.1.0).

[REQ-015] Per workspace the number platform must create exactly three extra
sliders (``EntityCategory.CONFIG``, ``NumberMode.SLIDER``, unit ``min``) whose
setter updates the coordinator, persists the matching option key and refreshes
the listeners. The button platform adds one ``EntityCategory.DIAGNOSTIC`` reset
button that restores exactly the three defaults in ONE persist call.

The account branch stays untouched (one catalog interval number, one refresh
button). Loading follows ``tests/test_runtime_settings.py``: the real platforms
on top of the shared fakes from ``tests/conftest.py``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"

conftest.install_ha_stubs()
conftest.install_coordinator_stub()

_uc = sys.modules["homeassistant.helpers.update_coordinator"]


class _RecordingCoordinatorEntity:
    """Minimal ``CoordinatorEntity`` that counts state writes (RC-1 contract)."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.ha_state_writes = 0

    def async_write_ha_state(self) -> None:
        self.ha_state_writes += 1


_uc.CoordinatorEntity = _RecordingCoordinatorEntity


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
_load("go_gauge.account_logic", str(BASE / "account_logic.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
number = _load("go_gauge.number", str(BASE / "number.py"))
button = _load("go_gauge.button", str(BASE / "button.py"))

ENTRY_ID = "ws-entry"
DEFAULT_LOOKBACKS = {"5h": 120, "week": 720, "month": 2880}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _FakeEntry:
    """ConfigEntry double: workspace by default (no ``entry_kind`` marker)."""

    def __init__(self, entry_id: str = ENTRY_ID, options=None, data=None):
        self.entry_id = entry_id
        self.options = dict(options or {})
        self.data = dict(data or {})


class _FakeHass:
    """``hass`` double with an applying ``config_entries.async_update_entry``."""

    def __init__(self):
        self.config_entries = conftest.make_applying_config_entries()
        self.data = {const.DOMAIN: {}}


def _live_coordinator(**attrs):
    """Real ``GoGaugeCoordinator`` without HA init, with recorded listeners."""
    c = coord.GoGaugeCoordinator.__new__(coord.GoGaugeCoordinator)
    c.ws_name = "Work"
    c.burn_lookback_minutes = dict(DEFAULT_LOOKBACKS)
    c.warn_percent = const.DEFAULT_WARN_PERCENT
    c.pace_red_percent = const.DEFAULT_PACE_RED_PERCENT
    c.usage_minutes = const.DEFAULT_USAGE_REFRESH_MINUTES
    c.auto_usage = True
    c.data = {"workspaces": []}
    c.async_update_listeners = MagicMock()
    for key, value in attrs.items():
        setattr(c, key, value)
    return c


SLIDERS = [
    # (class, window, suffix, min, max, step)
    (number.BurnLookback5hNumber, "5h", "burn_lookback_5h_minutes", 15, 300, 5),
    (number.BurnLookbackWeekNumber, "week", "burn_lookback_week_minutes", 60, 10080, 60),
    (number.BurnLookbackMonthNumber, "month", "burn_lookback_month_minutes", 60, 10080, 60),
]


# ---------------------------------------------------------------------------
# [REQ-015] the three sliders
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("cls", "window", "suffix", "minimum", "maximum", "step"), SLIDERS)
def test_slider_identity_and_range(cls, window, suffix, minimum, maximum, step):
    """[REQ-015] unique_id, category, mode, unit, icon, range and step (D6/D7)."""
    entity = cls(_live_coordinator(), _FakeEntry())

    assert entity._attr_unique_id == f"{ENTRY_ID}_{suffix}"
    assert entity._attr_entity_category == "config"
    assert entity._attr_mode == "slider"
    assert entity._attr_native_unit_of_measurement == "min"
    assert entity._attr_icon == "mdi:history"
    assert entity._attr_native_min_value == minimum
    assert entity._attr_native_max_value == maximum
    assert entity._attr_native_step == step


@pytest.mark.parametrize(("cls", "window", "suffix", "minimum", "maximum", "step"), SLIDERS)
def test_slider_native_value_reads_the_coordinator(
    cls, window, suffix, minimum, maximum, step
):
    """[REQ-015] The slider shows the live per-window lookback."""
    coordinator = _live_coordinator()
    coordinator.burn_lookback_minutes = {"5h": 15, "week": 60, "month": 60}
    expected = {"5h": 15, "week": 60, "month": 60}[window]

    assert cls(coordinator, _FakeEntry()).native_value == expected


@pytest.mark.parametrize(("cls", "window", "suffix", "minimum", "maximum", "step"), SLIDERS)
def test_slider_setter_updates_coordinator_options_and_listeners(
    cls, window, suffix, minimum, maximum, step
):
    """[REQ-015] Setter: coordinator live, matching option key, listeners, state."""
    coordinator = _live_coordinator()
    entry = _FakeEntry()
    hass = _FakeHass()
    hass.data[const.DOMAIN][entry.entry_id] = coordinator
    entity = cls(coordinator, entry)
    entity.hass = hass
    new_value = minimum + step

    asyncio.run(entity.async_set_native_value(new_value))

    assert coordinator.burn_lookback_minutes[window] == new_value
    assert entry.options == {
        const.BURN_LOOKBACK_WINDOWS[window]["option"]: new_value
    }
    coordinator.async_update_listeners.assert_called_once_with()
    assert entity.ha_state_writes == 1


def test_slider_setter_does_not_touch_the_other_lookback_options():
    """[REQ-015] A setter persists only its own window key."""
    coordinator = _live_coordinator()
    entry = _FakeEntry(options=dict(DEFAULT_LOOKBACKS_OPTIONS))
    hass = _FakeHass()
    entity = number.BurnLookbackWeekNumber(coordinator, entry)
    entity.hass = hass

    asyncio.run(entity.async_set_native_value(1440))

    assert entry.options[const.CONF_BURN_LOOKBACK_WEEK_MINUTES] == 1440
    assert entry.options[const.CONF_BURN_LOOKBACK_5H_MINUTES] == 120
    assert entry.options[const.CONF_BURN_LOOKBACK_MONTH_MINUTES] == 2880


DEFAULT_LOOKBACKS_OPTIONS = {
    const.CONF_BURN_LOOKBACK_5H_MINUTES: 120,
    const.CONF_BURN_LOOKBACK_WEEK_MINUTES: 720,
    const.CONF_BURN_LOOKBACK_MONTH_MINUTES: 2880,
}


# ---------------------------------------------------------------------------
# [REQ-015] the reset button (D3/D4)
# ---------------------------------------------------------------------------

def test_refresh_buttons_stay_primary_and_reset_is_diagnostic():
    """[Canon 1.8.0] Refresh actions stay uncategorised; reset stays diagnostic."""
    assert getattr(button.RefreshButton, "_attr_entity_category", None) is None
    assert getattr(button.AccountRefreshButton, "_attr_entity_category", None) is None
    assert button.ResetBurnLookbacksButton._attr_entity_category == "diagnostic"


def test_reset_button_identity_and_category():
    """[REQ-015] unique_id, DIAGNOSTIC category, restore icon."""
    entity = button.ResetBurnLookbacksButton(_live_coordinator(), _FakeEntry())

    assert entity._attr_unique_id == f"{ENTRY_ID}_burn_lookback_reset"
    assert entity._attr_entity_category == "diagnostic"
    assert entity._attr_icon == "mdi:restore"
    assert entity._attr_translation_key == "reset_burn_lookbacks"


def test_reset_button_restores_exactly_the_three_defaults_in_one_write():
    """[REQ-015] One persist call resets 120/720/2880 and nothing else (D3)."""
    coordinator = _live_coordinator()
    coordinator.burn_lookback_minutes = {"5h": 300, "week": 10080, "month": 10080}
    entry = _FakeEntry(options={
        const.CONF_BURN_LOOKBACK_5H_MINUTES: 300,
        const.CONF_BURN_LOOKBACK_WEEK_MINUTES: 10080,
        const.CONF_BURN_LOOKBACK_MONTH_MINUTES: 10080,
        const.CONF_WARN_PERCENT: 70,
        const.CONF_PACE_RED_PERCENT: 120,
        const.CONF_USAGE_REFRESH_MINUTES: 15,
    })
    hass = _FakeHass()
    entity = button.ResetBurnLookbacksButton(coordinator, entry)
    entity.hass = hass

    asyncio.run(entity.async_press())

    assert coordinator.burn_lookback_minutes == DEFAULT_LOOKBACKS
    assert entry.options[const.CONF_BURN_LOOKBACK_5H_MINUTES] == 120
    assert entry.options[const.CONF_BURN_LOOKBACK_WEEK_MINUTES] == 720
    assert entry.options[const.CONF_BURN_LOOKBACK_MONTH_MINUTES] == 2880
    # "one persist call" is the contract; warn/pace/refresh stay untouched.
    assert hass.config_entries.async_update_entry.call_count == 1
    assert entry.options[const.CONF_WARN_PERCENT] == 70
    assert entry.options[const.CONF_PACE_RED_PERCENT] == 120
    assert entry.options[const.CONF_USAGE_REFRESH_MINUTES] == 15
    coordinator.async_update_listeners.assert_called_once_with()
    assert entity.ha_state_writes == 1


def test_reset_button_does_not_clear_the_sample_history():
    """[REQ-015] The reset is deliberately narrow: history is untouched (D3)."""
    coordinator = _live_coordinator(
        _usage_samples={"ws1:5h": [(object(), 10.0, None)]}
    )
    entry = _FakeEntry()
    entity = button.ResetBurnLookbacksButton(coordinator, entry)
    entity.hass = _FakeHass()

    asyncio.run(entity.async_press())

    assert "ws1:5h" in coordinator._usage_samples


# ---------------------------------------------------------------------------
# [REQ-015] platform setup branches
# ---------------------------------------------------------------------------

class _FakeAccountCoordinator:
    """Bare account coordinator double for the untouched account branch."""


def test_workspace_number_setup_registers_exactly_six_numbers():
    """[REQ-015] Workspace numbers = the 3 settings + the 3 new sliders."""
    entry = _FakeEntry()
    hass = _FakeHass()
    hass.data[const.DOMAIN][entry.entry_id] = _live_coordinator()

    added: list = []
    asyncio.run(number.async_setup_entry(hass, entry, added.extend))

    assert [type(entity) for entity in added] == [
        number.WarnPercentNumber,
        number.PaceRedPercentNumber,
        number.UsageRefreshMinutesNumber,
        number.BurnLookback5hNumber,
        number.BurnLookbackWeekNumber,
        number.BurnLookbackMonthNumber,
    ]


def test_workspace_button_setup_registers_refresh_and_reset():
    """[REQ-015] Workspace buttons = refresh + the reset button."""
    entry = _FakeEntry()
    hass = _FakeHass()
    hass.data[const.DOMAIN][entry.entry_id] = _live_coordinator()

    added: list = []
    asyncio.run(button.async_setup_entry(hass, entry, added.extend))

    assert [type(entity) for entity in added] == [
        button.RefreshButton,
        button.ResetBurnLookbacksButton,
    ]


def test_account_number_setup_registers_only_the_catalog_interval():
    """[REQ-015] Account branch unchanged: no burn-lookback sliders."""
    entry = _FakeEntry(
        entry_id="account-entry",
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT},
    )
    hass = _FakeHass()
    hass.data[const.DOMAIN][entry.entry_id] = _FakeAccountCoordinator()

    added: list = []
    asyncio.run(number.async_setup_entry(hass, entry, added.extend))

    assert [type(entity) for entity in added] == [
        number.AccountModelsRefreshMinutesNumber
    ]


def test_account_button_setup_registers_only_the_catalog_refresh():
    """[REQ-015] Account branch unchanged: no burn-lookback reset button."""
    entry = _FakeEntry(
        entry_id="account-entry",
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT},
    )
    hass = _FakeHass()
    hass.data[const.DOMAIN][entry.entry_id] = _FakeAccountCoordinator()

    added: list = []
    asyncio.run(button.async_setup_entry(hass, entry, added.extend))

    assert [type(entity) for entity in added] == [button.AccountRefreshButton]
