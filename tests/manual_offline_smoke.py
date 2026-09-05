#!/usr/bin/env python3
"""MANUAL LIVE-API SMOKE TEST (not a pytest suite)

This file is NOT automatically collected by pytest. It runs only when invoked
directly as `python tests/manual_offline_smoke.py` and tests the integration
against the REAL opencode.ai API with actual authentication tokens.

Requires:
  - Live internet connection to opencode.ai
  - Authentication tokens in /opt/data/opencode-go-monitor/.env
    (format: OPENCODE_WS_<n>_TOKEN=<actual_token_string>)

Test sequence:
  1. Fetch model catalog from the public API (no auth needed)
  2. Fetch usage for each configured token (requires valid token + active subscription)
  3. Verify sensor logic against mock Coordinator with fetched data
  4. Confirm constants (DEFAULT_USAGE_REFRESH_MINUTES, DEFAULT_MODELS_REFRESH_MINUTES)

The sensor logic phase (step 3) is pure Python—see tests/test_sensor_logic.py for
pytest-based variants that mock the API layer. This file tests end-to-end behavior
with real data to catch API schema drift and integration issues that unit tests
cannot discover.

v0.3.1: Two-cycle Coordinator + Catalog-Sensor + no_subscription-Handling
(403 EntitlementError = Workspace ohne Abo)."""
import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path


class _Flexible(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


for name in [
    "homeassistant", "homeassistant.core", "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.entity_platform", "homeassistant.components",
    "homeassistant.components.sensor", "homeassistant.components.binary_sensor",
    "homeassistant.components.button", "homeassistant.components.diagnostics",
    "homeassistant.config_entries", "homeassistant.data_entry_flow",
    "voluptuous",
]:
    sys.modules[name] = _Flexible(name)
sys.modules["homeassistant"].__path__ = []


def _class_getitem(cls, item):
    return cls


uc = sys.modules["homeassistant.helpers.update_coordinator"]
uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)})


def _enum_like(*members):
    return type("EnumStub", (), {m.upper(): m for m in members})


sys.modules["homeassistant.components.sensor"] = _Flexible("sensor")
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like("measurement")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like("timestamp")

BASE = str(Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge")
ENV_FILE = Path("/opt/data/opencode-go-monitor/.env")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = load("go_gauge.const", f"{BASE}/const.py")
coord = load("go_gauge.coordinator", f"{BASE}/coordinator.py")


async def main():
    tokens = []
    for line in ENV_FILE.open():
        if line.startswith("OPENCODE_WS_") and "_TOKEN=" in line:
            tokens.append(line.split("=", 1)[1].strip())

    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = coord.OpenCodeGoApiClient(session)

        # 1) Modell-Katalog (public, immer 200)
        live_ids = await client.fetch_models()
        block = coord.build_models_block(live_ids)
        print(f"Katalog: {len(block['models'])} Modelle | billigst={block['cheapest_model']} "
              f"| free={block['free_models']}")

        # 2) Usage je Token - inkl. no_subscription-Erkennung
        usage_ws = []
        for i, tok in enumerate(tokens, start=1):
            res = await client.fetch_usage(tok)
            status = res.get("status", "ok")
            api = res.get("usage") or {}
            month = api.get("monthly") or {}
            reset = coord._parse_reset(month.get("resetsAt"))
            usage_ws.append({"key": f"ws{i}", "token_slot": i, "status": status,
                             "windows": {"month": {"percent": month.get("percent"),
                                                   "resets_at": reset}}})
            note = res.get("note", "")
            print(f"  ws{i}: status={status} | Monat {month.get('percent')}% {note}")

    # Erwartung: mind. ein 'ok' (Honcho/App), die ohne Abo -> no_subscription
    statuses = [w["status"] for w in usage_ws]
    assert "ok" in statuses or all(s == "no_subscription" for s in statuses), \
        f"Unerwartete Status: {statuses}"
    print(f"\nStatus-Verteilung: {statuses}")

    state = {"fetched_at": "t", "workspaces": usage_ws, "models_block": block}

    # 3) Sensor-Logik: Katalog + Usage + Reset
    class FakeCoord:
        data = state
        last_update_success = True

    class _EntityBase:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    uc.CoordinatorEntity = _EntityBase
    load("go_gauge.entity", f"{BASE}/entity.py")
    sensor = load("go_gauge.sensor", f"{BASE}/sensor.py")
    fc = FakeCoord()

    cat = sensor.ModelCatalogSensor(fc, types.SimpleNamespace(entry_id="test"))
    cat.coordinator = fc
    attrs = cat.extra_state_attributes
    catalog = json.loads(attrs["catalog_json"])
    print(f"ModelCatalogSensor: value={cat.native_value} | "
          f"ranking[0]={attrs['ranking_by_cost'][0]['id']}")
    assert cat.native_value == len(catalog)

    pct = sensor.UsagePercentSensor(fc, types.SimpleNamespace(entry_id="test"),
                                    key="ws3", win="month", label="Monat", ws_name="3")
    pct.coordinator = fc
    print(f"UsagePercent ws3/month -> {pct.native_value}% "
          f"| reset_iso={pct.extra_state_attributes.get('resets_at_iso')}")

    rst = sensor.ResetTimestampSensor(fc, types.SimpleNamespace(entry_id="test"),
                                      key="ws2", win="month", label="Monat", ws_name="2")
    rst.coordinator = fc
    print(f"ResetTimestamp ws2/month -> {rst.native_value}")

    cheapest = sensor.CheapestModelSensor(fc, types.SimpleNamespace(entry_id="test"))
    cheapest.coordinator = fc
    print(f"CheapestModel -> {cheapest.native_value}")

    free = sensor.FreeModelsSensor(fc, types.SimpleNamespace(entry_id="test"))
    free.coordinator = fc
    print(f"FreeModels -> {free.native_value}")

    assert const.DEFAULT_USAGE_REFRESH_MINUTES == 10
    assert const.DEFAULT_MODELS_REFRESH_MINUTES == 60

    print("\nDefaults OK: usage=10min, models=60min")
    print("ALLE v0.3.1 TESTS BESTANDEN")


if __name__ == "__main__":
    # Live-Smoke-Test gegen die echte opencode.ai API - braucht echte Tokens
    # in ENV_FILE, daher kein pytest-Test (pytest sammelt diese Datei ein,
    # fuehrt aber ohne test_*-Funktionen nichts davon aus).
    if not ENV_FILE.exists():
        raise SystemExit(f"Live-Smoke-Test uebersprungen: {ENV_FILE} fehlt")
    asyncio.run(main())
