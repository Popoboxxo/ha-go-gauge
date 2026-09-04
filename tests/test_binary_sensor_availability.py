#!/usr/bin/env python3
"""Regression test: RateLimitedBinarySensor.available must respect the
coordinator's update success (super().available), not just the
no_subscription case.

Root cause of the audited bug: `available` overrode the CoordinatorEntity
property without calling super().available, so a failed coordinator refresh
(e.g. last_update_success=False) never surfaced - the sensor kept reporting
stale/wrong data as "available" instead of "unavailable".
"""
import importlib.util
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
    "homeassistant.components.sensor", "homeassistant.components.binary_sensor",
    "homeassistant.config_entries", "homeassistant.data_entry_flow",
    "voluptuous", "aiohttp",
]:
    sys.modules[_name] = _Flexible(_name)
sys.modules["homeassistant"].__path__ = []


def _class_getitem(cls, item):
    return cls


_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)})


class _CoordinatorEntity:
    """Minimal stand-in for HA's real CoordinatorEntity.available semantics:
    unavailable whenever the last coordinator refresh failed."""

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


_uc.CoordinatorEntity = _CoordinatorEntity


def _enum_like(*members):
    return type("EnumStub", (), {m.upper(): m for m in members})


sys.modules["homeassistant.components.binary_sensor"].BinarySensorDeviceClass = _enum_like(
    "problem", "connectivity")
sys.modules["homeassistant.components.binary_sensor"].BinarySensorEntity = object


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
binary_sensor = _load("go_gauge.binary_sensor", str(BASE / "binary_sensor.py"))


class _FakeCoordinator:
    def __init__(self, data, last_update_success: bool = True):
        self.data = data
        self.last_update_success = last_update_success


class _FakeEntry:
    entry_id = "test_entry"


def _make_sensor(status: str, last_update_success: bool) -> "binary_sensor.RateLimitedBinarySensor":
    data = {
        "workspaces": [{
            "key": "ws1",
            "status": status,
            "windows": {"month": {"status": "ok"}},
        }],
    }
    coordinator = _FakeCoordinator(data, last_update_success=last_update_success)
    return binary_sensor.RateLimitedBinarySensor(
        coordinator, _FakeEntry(), key="ws1", win="month", ws_name="Work")


def test_available_when_update_succeeds_and_subscription_active():
    sensor = _make_sensor(status="ok", last_update_success=True)
    assert sensor.available is True


def test_unavailable_when_no_subscription_even_if_update_succeeded():
    sensor = _make_sensor(status="no_subscription", last_update_success=True)
    assert sensor.available is False


def test_unavailable_when_coordinator_update_failed():
    """Regression: a failed refresh must NOT be masked as available."""
    sensor = _make_sensor(status="ok", last_update_success=False)
    assert sensor.available is False


def test_unavailable_when_update_failed_and_no_subscription():
    sensor = _make_sensor(status="no_subscription", last_update_success=False)
    assert sensor.available is False


if __name__ == "__main__":
    test_available_when_update_succeeds_and_subscription_active()
    test_unavailable_when_no_subscription_even_if_update_succeeded()
    test_unavailable_when_coordinator_update_failed()
    test_unavailable_when_update_failed_and_no_subscription()
    print("OK")
