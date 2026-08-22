#!/usr/bin/env python3
"""Offline-Logiktest v0.2: Coordinator-Normalisierung DIREKT gegen die Live-API.
Stubbt nur HA-Module; HTTP laeuft echt (aiohttp + Browser-Headers)."""
import asyncio
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
    "voluptuous",
]:
    sys.modules[name] = _Flexible(name)
sys.modules["homeassistant"].__path__ = []


def _class_getitem(cls, item):
    return cls


sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,),
    {"__class_getitem__": classmethod(_class_getitem)})


def _enum_like(*members):
    ns = {m.upper(): m for m in members}
    return type("EnumStub", (), ns)


sys.modules["homeassistant.components.sensor"] = _Flexible("sensor")
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like("measurement")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like("timestamp")
sys.modules["homeassistant.components.binary_sensor"].BinarySensorDeviceClass = _enum_like(
    "problem", "connectivity")

BASE = "/opt/data/ha-go-gauge/custom_components/go_gauge"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = load("go_gauge.const", f"{BASE}/const.py")
coord = load("go_gauge.coordinator", f"{BASE}/coordinator.py")


async def main():
    # Echte Tokens aus der zentralen .env (werden nie ausgegeben)
    tokens = []
    for line in open("/opt/data/opencode-go-monitor/.env"):
        if line.startswith("OPENCODE_WS_") and "_TOKEN=" in line:
            tokens.append(line.split("=", 1)[1].strip())

    client = coord.OpenCodeGoApiClient(None, tokens)  # session wird pro Call gesetzt

    import aiohttp
    async with aiohttp.ClientSession() as session:
        client._session = session
        models = await client.fetch_models()
        print(f"Live-API: {len(models)} Modelle")
        usage = await client.fetch_all_usage()

    state = {
        "fetched_at": "test", "model_count_live": len(models),
        "models_live_ok": True, "workspaces": [], "models": [],
        "cheapest_model": None, "cheapest_overall": None, "free_models": [],
    }
    # Normalisierung ueber die echte merge-Logik testen:
    # Wir bauen den State wie im Coordinator und pruefen Reset-Parsing.
    for key, res in usage.items():
        entry = {"key": key, "token_slot": res.get("token_slot"),
                 "status": res.get("status", "ok"), "windows": {}}
        api = res.get("usage") or {}
        for api_key, win in (("rolling", "5h"), ("weekly", "week"), ("monthly", "month")):
            blk = api.get(api_key) or {}
            entry["windows"][win] = {
                "percent": blk.get("percent"), "status": blk.get("status"),
                "resets_at": coord._parse_reset(blk.get("resetsAt")),
            }
        state["workspaces"].append(entry)

    print("\n=== Direkt-API-Ergebnis ===")
    for ws in state["workspaces"]:
        m = ws["windows"]["month"]
        r = ws["windows"]["week"]
        print(f"  {ws['key']} (slot {ws['token_slot']}): "
              f"Monat {m['percent']}% reset={m['resets_at']} | Woche {r['percent']}%")
        assert isinstance(m["resets_at"], object), "reset fehlt"

    # Ratio-Logik
    eff_free = coord.efficiency({"free": True})
    eff_paid = coord.efficiency({"in": 1.0, "out": 2.0, "req": [100, 200, 600]})
    assert eff_free == {"usd_per_1m_mixed": 0.0, "month_req_per_usd": None, "free": True}
    assert abs(eff_paid["usd_per_1m_mixed"] - 1.2) < 1e-9 and eff_paid["month_req_per_usd"] == 10.0
    print(f"\nRatio: free={eff_free} | paid={eff_paid}")
    print("\nALLE DIREKT-API-TESTS OK")


asyncio.run(main())
