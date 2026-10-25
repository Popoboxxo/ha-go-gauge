#!/usr/bin/env python3
"""MANUAL SMOKE TEST (not a pytest suite)

This file is NOT automatically collected by pytest. It runs only when invoked
directly as `python3 tests/manual_offline_smoke.py` and exercises the loaded
integration modules plus their HA-fake stubs end to end.

Two modes:

* **offline (default)** - no tokens needed: the catalog comes from the bundled
  ``PRICING`` table, the usage windows are synthetic, and the account snapshot
  plus the reachability matrix are rendered from real integration logic. This
  is what CI/offline runs get, so the script always exits 0 on a healthy tree.
* **live** - when ``/opt/data/opencode-go-monitor/.env`` carries
  ``OPENCODE_WS_<n>_TOKEN=<token>`` lines, the real opencode.ai API is probed
  (model catalog unauthenticated, usage per token) and the same account /
  reachability / sensor assertions run against live data.

Test sequence:
  1. Model catalog (live API or the bundled PRICING table)
  2. Usage per token (live) or a synthetic window (offline)
  3. Account snapshot + reachability matrix (``account_logic``/``account``)
  4. Sensor logic against fake workspace + account coordinators
  5. Confirm constants (usage interval, account model interval)

Since 2.0.0 the integration splits into:

* a workspace-independent **account** coordinator that owns the model catalog
  (``models_block``, auto-fetch/interval settings, catalog evidence), and
* N **workspace** coordinators whose snapshot is usage-only.

The sensor phase therefore uses two fakes: ``FakeWorkspaceCoord`` (usage) and
``FakeAccountCoord`` (catalog). The old single fake carried ``models_block`` on
the workspace side, which no longer matches the runtime architecture.
"""
import asyncio
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path


class _Flexible(types.ModuleType):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


# Real exception classes instead of _Flexible throwaways: coordinator.py and
# __init__.py import them and production code uses them in ``except`` clauses
# (GoGaugeAuthError -> ConfigEntryAuthFailed). A throwaway class cannot be
# raised/caught the way HA's hierarchy requires, so the stub must expose a
# genuine exception subclass hierarchy.
class HomeAssistantError(Exception):
    """Stub of homeassistant.exceptions.HomeAssistantError."""


class ConfigEntryAuthFailed(HomeAssistantError):
    """Stub of homeassistant.exceptions.ConfigEntryAuthFailed."""


class ConfigEntryNotReady(HomeAssistantError):
    """Stub of homeassistant.exceptions.ConfigEntryNotReady."""


# NOTE: "voluptuous" is deliberately NOT faked here. It is a real pip package
# with no homeassistant dependency and must never be faked (see
# tests/conftest.py docstring).
HOMEASSISTANT_HELPERS = [
    "aiohttp_client", "device_registry", "entity_registry", "event",
    "issue_registry", "update_coordinator", "entity_platform",
]
for name in [
    "homeassistant", "homeassistant.core", "homeassistant.exceptions",
    "homeassistant.helpers",
    *[f"homeassistant.helpers.{sub}" for sub in HOMEASSISTANT_HELPERS],
    "homeassistant.components",
    "homeassistant.components.sensor", "homeassistant.components.binary_sensor",
    "homeassistant.components.button", "homeassistant.components.diagnostics",
    "homeassistant.config_entries", "homeassistant.data_entry_flow",
]:
    sys.modules[name] = _Flexible(name)
sys.modules["homeassistant"].__path__ = []
sys.modules["homeassistant.helpers"].__path__ = []

# Bind the helper submodules explicitly so `from homeassistant.helpers import
# device_registry` (used by account.py) resolves to the registered module
# instead of a fresh _Flexible throwaway class.
_helpers = sys.modules["homeassistant.helpers"]
for _sub in HOMEASSISTANT_HELPERS:
    setattr(_helpers, _sub, sys.modules[f"homeassistant.helpers.{_sub}"])

_exceptions = sys.modules["homeassistant.exceptions"]
_exceptions.HomeAssistantError = HomeAssistantError
_exceptions.ConfigEntryAuthFailed = ConfigEntryAuthFailed
_exceptions.ConfigEntryNotReady = ConfigEntryNotReady


def _class_getitem(cls, item):
    return cls


uc = sys.modules["homeassistant.helpers.update_coordinator"]
uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)})


def _enum_like(*members):
    return type("EnumStub", (), {m.upper(): m for m in members})


sys.modules["homeassistant.components.sensor"] = _Flexible("sensor")
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like("measurement")
# ENUM is used by ApiStatusSensor's class body, so it must exist at import time.
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like(
    "timestamp", "duration", "enum")

BASE = str(Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge")
ENV_FILE = Path("/opt/data/opencode-go-monitor/.env")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = load("go_gauge.const", f"{BASE}/const.py")
api = load("go_gauge.api", f"{BASE}/api.py")
catalog = load("go_gauge.catalog", f"{BASE}/catalog.py")
account_logic = load("go_gauge.account_logic", f"{BASE}/account_logic.py")
coord = load("go_gauge.coordinator", f"{BASE}/coordinator.py")
account = load("go_gauge.account", f"{BASE}/account.py")


def _read_tokens():
    """Return the token list from ENV_FILE; empty when the file is absent."""
    if not ENV_FILE.exists():
        return []
    tokens = []
    for line in ENV_FILE.open():
        if line.startswith("OPENCODE_WS_") and "_TOKEN=" in line:
            tokens.append(line.split("=", 1)[1].strip())
    return tokens


async def _probe_live(tokens):
    """Probe the real API: (model block, usage snapshot rows)."""
    import aiohttp

    async with aiohttp.ClientSession() as session:
        client = api.OpenCodeGoApiClient(session)

        # 1) Modell-Katalog (public, immer 200)
        live_ids = await client.fetch_models()
        block = catalog.build_models_block(live_ids)
        print(f"Katalog (live): {len(block['models'])} Modelle | "
              f"billigst={block['cheapest_model']} | free={block['free_models']}")

        # 2) Usage je Token - inkl. no_subscription-Erkennung
        usage_ws = []
        for i, tok in enumerate(tokens, start=1):
            res = await client.fetch_usage(tok)
            status = res.get("status", "ok")
            usage_payload = res.get("usage") or {}
            month = usage_payload.get("monthly") or {}
            reset = coord._parse_reset(month.get("resetsAt"))
            usage_ws.append({"key": f"ws{i}", "token_slot": i, "status": status,
                             "windows": {"month": {"percent": month.get("percent"),
                                                   "resets_at": reset}}})
            note = res.get("note", "")
            print(f"  ws{i}: status={status} | Monat {month.get('percent')}% {note}")
    return block, usage_ws


def _probe_offline():
    """Deterministic offline data: bundled pricing + one synthetic window."""
    block = catalog.build_models_block(None)
    reset = datetime.now(timezone.utc) + timedelta(days=4, hours=6)
    usage_ws = [{"key": "ws1", "token_slot": 1, "status": "ok",
                 "windows": {"month": {"percent": 42.0, "resets_at": reset}}}]
    print(f"Katalog (offline, PRICING): {len(block['models'])} Modelle | "
          f"billigst={block['cheapest_model']} | free={block['free_models']}")
    print("  ws1: status=ok | Monat 42.0% (synthetisch)")
    return block, usage_ws


def _reachability(usage_ws):
    """Resolve the account reachability matrix from the observed outcomes."""
    now = datetime.now(timezone.utc)
    evidence = [account_logic.Evidence(account_logic.SOURCE_MODELS, None,
                                       account_logic.OUTCOME_OK, now)]
    outcome_by_status = {
        "ok": account_logic.OUTCOME_OK,
        "no_subscription": account_logic.OUTCOME_NEUTRAL,
    }
    for ws in usage_ws:
        evidence.append(account_logic.Evidence(
            account_logic.SOURCE_USAGE, ws["key"],
            outcome_by_status.get(ws["status"], account_logic.OUTCOME_FAIL), now))

    snapshot = account.ReachabilitySnapshot(
        reachability=account_logic.resolve_reachability(
            evidence, now,
            account_logic.models_ttl(const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES),
            account_logic.usage_ttl(const.DEFAULT_USAGE_REFRESH_MINUTES),
        ),
        evidence=evidence,
        now=now,
        models_ttl=account_logic.models_ttl(const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES),
        usage_ttl=account_logic.usage_ttl(const.DEFAULT_USAGE_REFRESH_MINUTES),
    )
    return snapshot


async def main():
    tokens = _read_tokens()
    if tokens:
        block, usage_ws = await _probe_live(tokens)
        statuses = [w["status"] for w in usage_ws]
        # Erwartung: mind. ein 'ok' (Honcho/App), die ohne Abo -> no_subscription
        assert "ok" in statuses or all(s == "no_subscription" for s in statuses), \
            f"Unerwartete Status: {statuses}"
        print(f"\nStatus-Verteilung: {statuses}")
    else:
        print(f"Live-API uebersprungen: {ENV_FILE} fehlt/ohne Token - Offline-Modus.")
        block, usage_ws = _probe_offline()

    # 3) Konto-Snapshot (Spiegel von AccountCoordinator._snapshot, account.py)
    now = datetime.now(timezone.utc)
    account_snapshot = {
        "fetched_at": now.isoformat(),
        "models_block": block,
        "last_models_attempt": now.isoformat(),
        "last_models_ok": now.isoformat(),
        "last_models_outcome": "ok",
        "auto_fetch": True,
        "base_interval_minutes": const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
        "fetch_interval_minutes": const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
        "backoff_active": False,
    }
    print(f"\nKonto-Snapshot: outcome={account_snapshot['last_models_outcome']} | "
          f"auto_fetch={account_snapshot['auto_fetch']} | "
          f"intervall={account_snapshot['fetch_interval_minutes']}min | "
          f"katalog_modelle={len(account_snapshot['models_block']['models'])}")

    # 4) Reachability-Matrix (account_logic + account.ReachabilitySnapshot)
    reach_snapshot = _reachability(usage_ws)
    reach = reach_snapshot.reachability
    print(f"Reachability: state={reach.state} | reason={reach.reason} | "
          f"counts={reach.counts}")
    for row in reach_snapshot.evidence_attributes():
        print(f"  evidence: source={row['source']} workspace={row['workspace']} "
              f"outcome={row['outcome']} age={row['age_s']}s "
              f"ttl={row['ttl_s']}s fresh={row['fresh']}")

    # 5) Sensor-Logik: Konto-Entities (Katalog) + Workspace-Entities (Usage)
    class FakeWorkspaceCoord:
        data = {"fetched_at": "t", "workspaces": usage_ws}
        last_update_success = True

    class FakeAccountCoord:
        models_block = block
        auto_fetch = True
        base_interval_minutes = const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES
        last_models_outcome = "ok"

        @property
        def fetch_interval_minutes(self):
            return self.base_interval_minutes if self.auto_fetch else None

    class _EntityBase:
        def __init__(self, coordinator):
            self.coordinator = coordinator

    uc.CoordinatorEntity = _EntityBase
    load("go_gauge.entity", f"{BASE}/entity.py")
    sensor = load("go_gauge.sensor", f"{BASE}/sensor.py")

    wc = FakeWorkspaceCoord()
    ac = FakeAccountCoord()
    entry = types.SimpleNamespace(entry_id="test")

    cat = sensor.ModelCatalogSensor(ac, entry)
    cat.coordinator = ac
    attrs = cat.extra_state_attributes
    catalog_json = json.loads(attrs["catalog_json"])
    print(f"\nModelCatalogSensor: value={cat.native_value} | "
          f"ranking[0]={attrs['ranking_by_cost'][0]['id']}")
    assert cat.native_value == len(catalog_json)
    assert attrs["account_auto_update_models"] is True

    key = usage_ws[0]["key"]
    pct = sensor.UsagePercentSensor(wc, entry, key=key, win="month", label="Monat")
    pct.coordinator = wc
    print(f"UsagePercent {key}/month -> {pct.native_value}% "
          f"| reset_iso={pct.extra_state_attributes.get('resets_at_iso')}")

    rst = sensor.ResetTimestampSensor(wc, entry, key=key, win="month", label="Monat")
    rst.coordinator = wc
    print(f"ResetTimestamp {key}/month -> {rst.native_value}")

    cheapest = sensor.CheapestModelSensor(ac, entry)
    cheapest.coordinator = ac
    print(f"CheapestModel -> {cheapest.native_value}")

    free = sensor.FreeModelsSensor(ac, entry)
    free.coordinator = ac
    print(f"FreeModels -> {free.native_value}")

    assert const.DEFAULT_USAGE_REFRESH_MINUTES == 10
    assert const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES == 60
    assert len(account_logic.ACCOUNT_ENTITY_KINDS) == 5

    print("\nDefaults OK: usage=10min, account_models=60min")
    print("ALLE SMOKE-CHECKS BESTANDEN")


if __name__ == "__main__":
    # Manual smoke test - not a pytest test (see docstring). Offline mode runs
    # without tokens; live mode needs ENV_FILE entries.
    asyncio.run(main())
