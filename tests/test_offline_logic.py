#!/usr/bin/env python3
"""Offline-Logiktest: Coordinator-Normalisierung + Sensor-Entities gegen echte /state-Daten.
HA-Module und aiohttp werden gestubbt - es geht nur um unsere eigene Logik."""
import importlib.util
import json
import sys
import types


class _Flexible(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


for name in [
    "homeassistant", "homeassistant.core", "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.entity_platform", "homeassistant.components",
    "homeassistant.components.binary_sensor",
    "homeassistant.components.button", "homeassistant.components.diagnostics",
    "homeassistant.config_entries", "homeassistant.data_entry_flow",
    "aiohttp", "voluptuous",
]:
    sys.modules[name] = _Flexible(name)
sys.modules["homeassistant"].__path__ = []


def _enum_like(*members):
    """Enum-Stub mit Klassenattributen (SensorStateClass.MEASUREMENT etc.)."""
    ns = {m.upper(): m for m in members}
    return type("EnumStub", (), ns)


sys.modules["homeassistant.components.sensor"] = _Flexible("sensor")
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like(
    "measurement", "total_increasing")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like("timestamp")
sys.modules["homeassistant.components.binary_sensor"].BinarySensorDeviceClass = _enum_like(
    "problem", "connectivity")

# DataUpdateCoordinator muss generisch subscriptable sein (Generic-Stub)
def _class_getitem(cls, item):
    return cls

sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)})

BASE = "/opt/data/ha-go-gauge/custom_components/go_gauge"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = load("go_gauge.const", f"{BASE}/const.py")
coord = load("go_gauge.coordinator", f"{BASE}/coordinator.py")

state = json.load(open("/tmp/state3.json"))
dummy = coord.GoGaugeCoordinator.__new__(coord.GoGaugeCoordinator)
norm = dummy._normalize(state)

print("=== Coordinator-Normalisierung ===")
print("Workspaces:", [(w["key"], w["name"]) for w in norm["workspaces"]])
ws3 = next(w for w in norm["workspaces"] if w["key"] == "ws3")
m = ws3["windows"]["month"]
print(f"ws3 month: {m['percent']}% | usd={m['usd']} | reset={m['resets_at']}")
assert m["resets_at"] is not None and m["resets_at"].tzinfo is not None, "Reset-Timestamp fehlt/naiv"
print(f"Modelle: {len(norm['models'])} | billigstes: {norm['cheapest_model']} | free: {norm['free_models']}")
assert norm["cheapest_model"], "Kein billigstes Modell ermittelt"

# Sensor-Entities mit gemocktem Coordinator
class FakeCoord:
    data = norm
    last_update_success = True


class _EntityBase:
    """Ersetzt CoordinatorEntity.__init__ im Test."""

    def __init__(self, coordinator):
        self.coordinator = coordinator


sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = _EntityBase

fc = FakeCoord()

sensor = load("go_gauge.sensor", f"{BASE}/sensor.py")

pct = sensor.UsagePercentSensor(fc, types.SimpleNamespace(entry_id="test"), "ws3", "Honcho", "month", "Monat")
pct.coordinator = fc
attrs = pct.extra_state_attributes
print(f"\nUsagePercent ws3/month -> {pct.native_value}% | usd={attrs.get('usd')} | reset_iso={attrs.get('resets_at_iso')}")

rst = sensor.ResetTimestampSensor(fc, types.SimpleNamespace(entry_id="test"), "ws3", "Honcho", "month", "Monat")
rst.coordinator = fc
print(f"ResetTimestamp ws3/month -> {rst.native_value}")

cheapest = sensor.CheapestModelSensor(fc, types.SimpleNamespace(entry_id="test"))
cheapest.coordinator = fc
print(f"CheapestModel -> {cheapest.native_value} | attrs={cheapest.extra_state_attributes}")

free = sensor.FreeModelsSensor(fc, types.SimpleNamespace(entry_id="test"))
free.coordinator = fc
print(f"FreeModels -> {free.native_value}")

ratio = sensor.ModelRatioSensor(fc, types.SimpleNamespace(entry_id="test"), "deepseek-v4-flash")
ratio.coordinator = fc
print(f"ModelRatio deepseek-v4-flash -> {ratio.native_value} USD/1M | req/$={ratio.extra_state_attributes.get('month_req_per_usd')}")

print("\nALLE LOGIK-TESTS OK")
