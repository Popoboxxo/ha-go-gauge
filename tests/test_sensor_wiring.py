#!/usr/bin/env python3
"""Regression test: async_setup_entry must wire UsagePercentSensor /
ResetTimestampSensor so each entity reads ITS OWN window's data.

Root cause of the v0.5.0-v0.6.0 regression (all "Limits" sensors showed
no data): the constructor call in sensor.async_setup_entry passed
positional args (key, win, label, ws_name) while __init__ expected
(key, ws_name, win, label) - silently swapping win/label/ws_name so
`self._win` held a label string that never matched a window key.

This test drives the REAL async_setup_entry (not a hand-built entity, like
the old offline test did) so a future argument-order regression fails here.
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

# Base homeassistant.* module fakes (incl. the _Flexible ModuleType helper)
# are installed centrally in tests/conftest.py, which pytest always imports
# before collecting this file - see conftest.py docstring for why.

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


def _class_getitem(cls, item):
    return cls


_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)})


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


_uc.CoordinatorEntity = _CoordinatorEntity


def _enum_like(*members):
    return type("EnumStub", (), {m.upper(): m for m in members})


sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like("measurement")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like("timestamp")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
sensor = _load("go_gauge.sensor", str(BASE / "sensor.py"))


class _FakeCoordinator:
    is_catalog_owner = False
    ws_name = "Work"

    def __init__(self, data):
        self.data = data


class _FakeEntry:
    entry_id = "test_entry"


class _FakeHass:
    def __init__(self, coordinator):
        self.data = {const.DOMAIN: {_FakeEntry.entry_id: coordinator}}


def test_usage_percent_sensor_reads_its_own_window():
    data = {
        "workspaces": [{
            "key": "ws1",
            "status": "ok",
            "windows": {
                "5h": {"percent": 12.5, "status": "ok", "resets_at": None},
                "week": {"percent": 40.0, "status": "ok", "resets_at": None},
                "month": {"percent": 77.0, "status": "ok", "resets_at": None},
            },
        }],
        "models_block": None,
    }
    coordinator = _FakeCoordinator(data)
    hass = _FakeHass(coordinator)
    entry = _FakeEntry()

    added: list = []
    asyncio.run(sensor.async_setup_entry(hass, entry, added.extend))

    percent_sensors = {
        e._win: e for e in added if isinstance(e, sensor.UsagePercentSensor)
    }
    assert set(percent_sensors) == {"5h", "week", "month"}
    assert percent_sensors["5h"].native_value == 12.5
    assert percent_sensors["week"].native_value == 40.0
    assert percent_sensors["month"].native_value == 77.0

    reset_sensors = {
        e._win: e for e in added if isinstance(e, sensor.ResetTimestampSensor)
    }
    assert set(reset_sensors) == {"5h", "week", "month"}


if __name__ == "__main__":
    test_usage_percent_sensor_reads_its_own_window()
    print("OK")
