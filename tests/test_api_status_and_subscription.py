#!/usr/bin/env python3
"""Tests fuer die Abo-Semantik und den neuen ApiStatusSensor (2026-09-25).

Zwei Aenderungen werden hier festgeschrieben:

1. `SubscriptionActiveBinarySensor` darf KEIN device_class
   `CONNECTIVITY` mehr tragen. HA rendert das als "Getrennt" - bei
   no_subscription ist aber genau die API erreichbar, nur das Abo fehlt.
   Der Nutzer las daraus einen API-Ausfall ab (falscher Diagnose-Weg).

2. `ApiStatusSensor` benennt die Ursache statt nur ja/nein zu liefern,
   und zwar PRO WORKSPACE. Der accountweite ApiReachableBinarySensor
   aggregiert ("irgendwas ging schief") und verdeckt genau den Fall, den
   man sehen will: WS-A liefert Daten, WS-B nicht.
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
# Der ENUM-Sensor braucht SensorDeviceClass.ENUM als echtes Attribut.
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like(
    "timestamp", "duration", "enum")
sys.modules["homeassistant.components.sensor"].SensorEntity = object
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like(
    "measurement", "total")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
binary_sensor = _load("go_gauge.binary_sensor", str(BASE / "binary_sensor.py"))
sensor_mod = _load("go_gauge.sensor", str(BASE / "sensor.py"))


class _FakeCoordinator:
    def __init__(self, data, last_update_success: bool = True):
        self.data = data
        self.last_update_success = last_update_success
        self.is_catalog_owner = True


class _FakeEntry:
    entry_id = "test_entry"


def _ws(status, windows=None, note=None, key="ws1"):
    ws = {"key": key, "status": status, "windows": windows or {}}
    if note is not None:
        ws["note"] = note
    return ws


def _make_status_sensor(ws):
    coordinator = _FakeCoordinator({"workspaces": [ws]})
    return sensor_mod.ApiStatusSensor(coordinator, _FakeEntry(), key=ws["key"])


# --- 1. Abo-Sensor: kein connectivity device_class ------------------------

def test_subscription_sensor_has_no_device_class():
    """Regression: CONNECTIVITY erzeugte "Getrennt" bei no_subscription."""
    coordinator = _FakeCoordinator({"workspaces": [_ws("no_subscription")]})
    sensor = binary_sensor.SubscriptionActiveBinarySensor(coordinator, _FakeEntry(), key="ws1")
    # Gerade _attr_device_class waere hier der Fehler - er muss fehlen.
    assert getattr(sensor, "_attr_device_class", None) is None, (
        "Abo-Sensor darf kein device_class tragen - HA rendert "
        "CONNECTIVITY als 'Getrennt' und verwechselt Abo mit Netzwerk")


def test_subscription_sensor_still_reports_state_correctly():
    """Der Fix darf das eigentliche Verhalten nicht brechen."""
    for status, expected in (("ok", True), ("no_subscription", False)):
        coordinator = _FakeCoordinator({"workspaces": [_ws(status)]})
        sensor = binary_sensor.SubscriptionActiveBinarySensor(
            coordinator, _FakeEntry(), key="ws1")
        assert sensor.is_on is expected, status


# --- 2. ApiStatusSensor: Ursache statt Ja/Nein ---------------------------

def test_api_status_ok():
    ws = _ws("ok", windows={"week": {"status": "ok", "percent": 14}})
    assert _make_status_sensor(ws).native_value == "ok"


def test_api_status_no_subscription():
    ws = _ws("no_subscription", note="OpenCode Go subscription required.")
    assert _make_status_sensor(ws).native_value == "no_subscription"


def test_api_status_auth_error_recognized_from_note():
    """401 = Token ungueltig. Das ist Konfig, kein transienter API-Fehler."""
    ws = _ws("error", note="HTTP 403 (AuthError) - moeglicherweise Rate-Limit")
    assert _make_status_sensor(ws).native_value == "auth_error"


def test_api_status_generic_error():
    ws = _ws("error", note="Abruf fehlgeschlagen, letzter Stand beibehalten: timeout")
    assert _make_status_sensor(ws).native_value == "api_error"


def test_api_status_rate_limited_shadows_ok():
    """status 'ok' + Fensterapost 'rate-limited' -> rate_limited ist relevanter.

    Sonst wuerde der Sensor "ok" melden, waehrend die Nutzung bei 100%
    steht - genau die Fehlinterpretation, die wir vermeiden wollen.
    """
    ws = _ws("ok", windows={
        "5h": {"status": "ok", "percent": 0},
        "month": {"status": "rate-limited", "percent": 100},
    })
    assert _make_status_sensor(ws).native_value == "rate_limited"


def test_api_status_unknown_when_workspace_missing():
    coordinator = _FakeCoordinator({"workspaces": []})
    sensor = sensor_mod.ApiStatusSensor(coordinator, _FakeEntry(), key="ws9")
    assert sensor.native_value == "unknown"


def test_api_status_is_per_workspace_not_aggregate():
    """Zwei Workspaces, nur einer defekt -> beide Sensoren unterscheiden sich.

    Kern der Design-Entscheidung: ein accountweiter Status-Sensor wuerde
    beide als "ok" zeigen und den Fehler verdecken.
    """
    coordinator = _FakeCoordinator({"workspaces": [
        _ws("ok", windows={"week": {"status": "ok"}}, key="ws1"),
        _ws("no_subscription", key="ws2"),
    ]})
    good = sensor_mod.ApiStatusSensor(coordinator, _FakeEntry(), key="ws1")
    bad = sensor_mod.ApiStatusSensor(coordinator, _FakeEntry(), key="ws2")
    assert good.native_value == "ok"
    assert bad.native_value == "no_subscription"


def test_api_status_options_cover_all_emitted_states():
    """Jeder zurueckgegebene Wert muss in _attr_options stehen, sonst
    verweigert HA den Enum-State (ValueError) und die Entity fehlt."""
    options = set(sensor_mod.ApiStatusSensor._attr_options)
    cases = [
        _ws("ok"),
        _ws("no_subscription"),
        _ws("error", note="AuthError"),
        _ws("error", note="timeout"),
        _ws("ok", windows={"month": {"status": "rate-limited"}}),
    ]
    for ws in cases:
        val = _make_status_sensor(ws).native_value
        assert val in options, f"{val!r} fehlt in _attr_options {sorted(options)}"
    assert "unknown" in options


def test_api_status_attributes_expose_raw_status_and_note():
    ws = _ws("error", note="HTTP 403 (blocked) - moeglicherweise Rate-Limit")
    attrs = _make_status_sensor(ws).extra_state_attributes
    assert attrs["raw_status"] == "error"
    assert attrs["note"] == "HTTP 403 (blocked) - moeglicherweise Rate-Limit"
    assert attrs["workspace_key"] == "ws1"
    assert attrs["last_update_success"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"OK ({len(tests)} tests)")
