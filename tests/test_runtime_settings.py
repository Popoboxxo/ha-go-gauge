#!/usr/bin/env python3
"""Regression tests for the runtime settings (number/switch) fixes.

Root-Cause-Analyse 2026-09-13 (RC-1..RC-4):

- RC-1: ``number``/``switch`` entities changed ``coordinator.*`` but never
  wrote their own state. The UI therefore kept showing the old value until
  the coordinator happened to poll again (and with auto-update off that is
  the 24h fallback, i.e. effectively never). Every setter must call
  ``async_write_ha_state()``.
- RC-2: ``GoGaugeCoordinator.recalculate_interval()`` only stored the new
  ``update_interval``; HA's setter does not reschedule the already-armed
  timer, so the next fetch still used the OLD interval. It must call
  ``_schedule_refresh()``.
- RC-3: ``persist_options()`` set ``_skip_reload`` unconditionally. Writing
  the same value twice leaves the flag set (HA only fires listeners on a real
  change), and the NEXT real options change (e.g. options flow) is then
  dropped without a reload. The flag must be reset when nothing changed.

Base homeassistant.* module fakes are installed centrally in
tests/conftest.py, which pytest imports before collecting this file.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


# --- HA base fakes (per-file, loaded before the integration modules) --------

class _RecordingEntity:
    """Records ``async_write_ha_state`` calls (RC-1 assertion hook)."""

    ha_state_writes = 0

    def async_write_ha_state(self) -> None:
        self.ha_state_writes += 1


class _CoordinatorEntity(_RecordingEntity):
    def __init__(self, coordinator):
        self.coordinator = coordinator


class _FakeDataUpdateCoordinator:
    """Captures ``_schedule_refresh`` calls and stores update_interval (RC-2)."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, *args, **kwargs):
        self._update_interval = None
        self._update_interval_seconds = None
        self.schedule_refresh_calls = 0

    @property
    def update_interval(self):
        return self._update_interval

    @update_interval.setter
    def update_interval(self, value):
        self._update_interval = value
        self._update_interval_seconds = value.total_seconds() if value else None

    def _schedule_refresh(self) -> None:
        self.schedule_refresh_calls += 1


class _NumberModeStub:
    BOX = "box"


sys.modules["homeassistant.components.number"].NumberMode = _NumberModeStub
sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = (
    _CoordinatorEntity
)
sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = (
    _FakeDataUpdateCoordinator
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
entity = _load("go_gauge.entity", str(BASE / "entity.py"))
number = _load("go_gauge.number", str(BASE / "number.py"))
switch = _load("go_gauge.switch", str(BASE / "switch.py"))


# --- Test doubles -----------------------------------------------------------

class _FakeCoordinator:
    ws_name = "WS 1"

    def __init__(self):
        self.warn_percent = const.DEFAULT_WARN_PERCENT
        self.pace_red_percent = const.DEFAULT_PACE_RED_PERCENT
        self.usage_minutes = const.DEFAULT_USAGE_REFRESH_MINUTES
        self.models_minutes = const.DEFAULT_MODELS_REFRESH_MINUTES
        self.auto_usage = True
        self.auto_models = True
        self.recalculate_calls = 0

    def recalculate_interval(self) -> None:
        self.recalculate_calls += 1


class _FakeEntry:
    entry_id = "test_entry"

    def __init__(self, options=None):
        self.options = dict(options or {})


class _FakeConfigEntries:
    def __init__(self, changed: bool = True):
        self.changed = changed
        self.calls: list[dict] = []

    def async_update_entry(self, entry, **changes):
        self.calls.append(changes)
        if self.changed:
            entry.options = dict(changes.get("options", entry.options))
        return self.changed


class _FakeHass:
    def __init__(self, changed: bool = True):
        self.config_entries = _FakeConfigEntries(changed=changed)


# --- RC-1: entities write their own state -----------------------------------

def test_number_set_writes_ha_state():
    coordinator = _FakeCoordinator()
    entity_obj = number.UsageRefreshMinutesNumber(coordinator, _FakeEntry())
    entity_obj.hass = _FakeHass()

    asyncio.run(entity_obj.async_set_native_value(3))

    assert coordinator.usage_minutes == 3
    assert coordinator.recalculate_calls == 1
    assert entity_obj.ha_state_writes == 1, "number must write its state after set"


def test_warn_and_pace_numbers_write_ha_state():
    for cls, attr, value in (
        (number.WarnPercentNumber, "warn_percent", 70),
        (number.PaceRedPercentNumber, "pace_red_percent", 120),
    ):
        coordinator = _FakeCoordinator()
        entity_obj = cls(coordinator, _FakeEntry())
        entity_obj.hass = _FakeHass()

        asyncio.run(entity_obj.async_set_native_value(value))

        assert getattr(coordinator, attr) == value
        assert entity_obj.ha_state_writes == 1, f"{cls.__name__} must write state"


def test_switch_turn_on_off_writes_ha_state():
    coordinator = _FakeCoordinator()
    entity_obj = switch.AutoUpdateUsageSwitch(coordinator, _FakeEntry())
    entity_obj.hass = _FakeHass()

    asyncio.run(entity_obj.async_turn_off())
    assert coordinator.auto_usage is False
    assert entity_obj.ha_state_writes == 1

    asyncio.run(entity_obj.async_turn_on())
    assert coordinator.auto_usage is True
    assert entity_obj.ha_state_writes == 2, "each toggle must write state"


def test_models_switch_writes_ha_state():
    coordinator = _FakeCoordinator()
    entity_obj = switch.AutoUpdateModelsSwitch(coordinator, _FakeEntry())
    entity_obj.hass = _FakeHass()

    asyncio.run(entity_obj.async_turn_off())
    assert coordinator.auto_models is False
    assert entity_obj.ha_state_writes == 1


# --- RC-2: recalculate_interval reschedules the timer -----------------------

def _bare_coordinator():
    c = coord.GoGaugeCoordinator.__new__(coord.GoGaugeCoordinator)
    c._update_interval = None
    c._update_interval_seconds = None
    c.schedule_refresh_calls = 0
    return c


def test_recalculate_interval_reschedules_running_timer():
    c = _bare_coordinator()
    c.auto_usage = True
    c.usage_minutes = 3
    c.auto_models = False
    c.models_minutes = 60

    c.recalculate_interval()

    assert c.update_interval.total_seconds() == 180
    assert c.schedule_refresh_calls == 1, "must reschedule the armed timer"


def test_recalculate_interval_with_all_off_uses_fallback_and_reschedules():
    c = _bare_coordinator()
    c.auto_usage = False
    c.usage_minutes = 10
    c.auto_models = False
    c.models_minutes = 60

    c.recalculate_interval()

    assert c.update_interval.total_seconds() == 86400
    assert c.schedule_refresh_calls == 1


# --- RC-3: persist_options must not leak _skip_reload -----------------------

def test_persist_options_clears_flag_when_nothing_changed():
    coordinator = _FakeCoordinator()
    coordinator._skip_reload = False
    entry = _FakeEntry(options={"usage_refresh_minutes": 10})
    hass = _FakeHass(changed=False)

    changed = entity.persist_options(
        hass, entry, coordinator, usage_refresh_minutes=10
    )

    assert changed is False
    assert coordinator._skip_reload is False, (
        "unchanged write must not leave _skip_reload set"
    )


def test_persist_options_keeps_flag_when_changed_for_listener():
    coordinator = _FakeCoordinator()
    coordinator._skip_reload = False
    entry = _FakeEntry(options={"usage_refresh_minutes": 10})
    hass = _FakeHass(changed=True)

    changed = entity.persist_options(
        hass, entry, coordinator, usage_refresh_minutes=5
    )

    assert changed is True
    assert entry.options["usage_refresh_minutes"] == 5
    # Flag stays set: the async update-listener consumes and resets it.
    assert coordinator._skip_reload is True


if __name__ == "__main__":
    test_number_set_writes_ha_state()
    test_warn_and_pace_numbers_write_ha_state()
    test_switch_turn_on_off_writes_ha_state()
    test_models_switch_writes_ha_state()
    test_recalculate_interval_reschedules_running_timer()
    test_recalculate_interval_with_all_off_uses_fallback_and_reschedules()
    test_persist_options_clears_flag_when_nothing_changed()
    test_persist_options_keeps_flag_when_changed_for_listener()
    print("OK")
