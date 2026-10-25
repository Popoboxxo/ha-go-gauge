#!/usr/bin/env python3
"""Binary-sensor availability: workspace rate-limit flags + account reachability.

Two concerns:

1. ``RateLimitedBinarySensor.available`` must respect the coordinator's update
   success (``super().available``), not just the no_subscription case - the
   audited bug kept reporting stale data as available.
2. ``ApiReachableBinarySensor`` (account entry, 2.0.0) resolves the API state
   from TIMESTAMPED EVIDENCE and TTLs (plan §5), not from
   ``last_update_success`` + ``fetched_at``: fresh success -> ON, only fresh
   failures -> OFF, fresh "answered but no success" -> unknown, no fresh
   evidence -> unknown.
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


# NOTE: "voluptuous" is deliberately NOT faked here. It is a real pip package
# with no homeassistant dependency and must never be faked (see
# tests/conftest.py docstring); a fake here poisoned sys.modules for later
# test modules and broke vol.Schema/vol.Required at runtime.
for _name in [
    "homeassistant", "homeassistant.core", "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.entity_platform", "homeassistant.helpers.event",
    "homeassistant.helpers.issue_registry", "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_registry", "homeassistant.components",
    "homeassistant.components.sensor", "homeassistant.components.binary_sensor",
    "homeassistant.config_entries", "homeassistant.data_entry_flow",
    "aiohttp",
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
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
account_logic = _load("go_gauge.account_logic", str(BASE / "account_logic.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
account = _load("go_gauge.account", str(BASE / "account.py"))
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
        coordinator, _FakeEntry(), key="ws1", win="month")


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


# ---------------------------------------------------------------------------
# ApiReachableBinarySensor: evidence + TTL instead of last_update_success
# ---------------------------------------------------------------------------

def _live(module_name):
    """Resolve a module through ``sys.modules`` at CALL time.

    Collection order is alphabetical and several test files reload
    ``go_gauge.account``/``go_gauge.coordinator``; the integration resolves its
    lazy imports through ``sys.modules`` too, so the doubles must be built from
    the module currently registered - otherwise ``isinstance`` checks inside
    ``collect_evidence`` would reject them.
    """
    return sys.modules[module_name]


def _account_coordinator(**attrs):
    """Bare ``AccountCoordinator`` (no HA init) carrying only evidence attrs."""
    module = _live("go_gauge.account")
    coordinator = module.AccountCoordinator.__new__(module.AccountCoordinator)
    coordinator.auto_fetch = attrs.pop("auto_fetch", True)
    coordinator.base_interval_minutes = attrs.pop("base_interval_minutes", 60)
    coordinator.last_models_attempt = attrs.pop("last_models_attempt", None)
    coordinator.last_models_outcome = attrs.pop("last_models_outcome", None)
    for key, value in attrs.items():
        setattr(coordinator, key, value)
    return coordinator


def _workspace_coordinator(*, attempt=None, outcome=None, usage_minutes=10,
                           name="Work"):
    """Bare ``GoGaugeCoordinator`` (no HA init) carrying only evidence attrs."""
    # Build from the class account.py actually bound at ITS import time: that
    # is the class ``workspace_coordinators`` isinstance-checks against.
    module = _live("go_gauge.account")
    coordinator = module.GoGaugeCoordinator.__new__(module.GoGaugeCoordinator)
    coordinator.ws_name = name
    coordinator.usage_minutes = usage_minutes
    coordinator.last_usage_attempt = attempt
    coordinator.last_usage_outcome = outcome
    return coordinator


class _EvidenceHass:
    """``hass`` double whose ``data`` holds the loaded coordinators."""

    def __init__(self, account_coordinator, workspaces=None):
        self.data = {
            const.DOMAIN: {
                "acc": account_coordinator,
                const.DATA_ACCOUNT_KEY: account_coordinator,
                **{f"ws-{i}": ws for i, ws in enumerate(workspaces or [])},
            }
        }


def _reachable_sensor(*, account_coordinator=None, workspaces=None):
    """Build the sensor with ``hass`` wired to the given coordinators."""
    account_coordinator = account_coordinator or _account_coordinator()
    hass = _EvidenceHass(account_coordinator, workspaces)
    sensor = binary_sensor.ApiReachableBinarySensor(account_coordinator, _FakeEntry())
    sensor.hass = hass
    return sensor


def test_api_reachable_unknown_without_evidence():
    """[PLAN2-E5] No evidence at all -> None/unknown, not OFF."""
    sensor = _reachable_sensor()
    assert sensor.is_on is None
    assert sensor.extra_state_attributes["reachability_reason"] == "no_evidence"
    assert sensor.available is True


def test_api_reachable_on_from_fresh_models_only():
    """[PLAN2-E5] A fresh successful catalog fetch alone proves ON."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    account = _account_coordinator(
        last_models_attempt=now - timedelta(minutes=1), last_models_outcome="ok"
    )
    sensor = _reachable_sensor(account_coordinator=account)
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["reachability_reason"] == "fresh_ok"


def test_api_reachable_off_when_all_fresh_failures():
    """[PLAN2-E5] Only fresh failures -> OFF (all_fresh_failed)."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    account = _account_coordinator(
        last_models_attempt=now - timedelta(minutes=1), last_models_outcome="fail"
    )
    sensor = _reachable_sensor(account_coordinator=account)
    assert sensor.is_on is False
    assert sensor.extra_state_attributes["reachability_reason"] == "all_fresh_failed"


def test_api_reachable_neutral_no_subscription_is_unknown():
    """[PLAN2-E5] no_subscription/401 = API answered -> unknown, never ON."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    workspace = _workspace_coordinator(
        attempt=now - timedelta(minutes=1), outcome="neutral"
    )
    sensor = _reachable_sensor(workspaces=[workspace])
    assert sensor.is_on is None
    assert (
        sensor.extra_state_attributes["reachability_reason"]
        == "answered_no_success"
    )


def test_api_reachable_stale_evidence_is_unknown():
    """[PLAN2-E5] Evidence older than its TTL is ignored, not counted as fail."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    # usage TTL = 3 x 10 min = 30 min; 31 min old -> stale
    workspace = _workspace_coordinator(
        attempt=now - timedelta(minutes=31), outcome="fail", usage_minutes=10
    )
    sensor = _reachable_sensor(workspaces=[workspace])
    assert sensor.is_on is None
    assert sensor.extra_state_attributes["reachability_reason"] == "no_evidence"
    assert sensor.extra_state_attributes["evidence"][0]["fresh"] is False


def test_api_reachable_ttls_and_evidence_attributes():
    """[PLAN2-E5] TTL/evidence attributes mirror the matrix contract."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    account = _account_coordinator(
        last_models_attempt=now, last_models_outcome="ok", base_interval_minutes=20
    )
    workspace = _workspace_coordinator(attempt=now, outcome="ok", usage_minutes=5)
    sensor = _reachable_sensor(
        account_coordinator=account, workspaces=[workspace]
    )
    attrs = sensor.extra_state_attributes
    # models TTL = 2 x base interval, usage TTL = 3 x workspace interval
    assert attrs["models_ttl_s"] == 40 * 60
    assert attrs["usage_ttl_s"] == 15 * 60
    assert attrs["checked_at"]
    sources = {item["source"] for item in attrs["evidence"]}
    assert sources == {"models", "usage"}
    usage_item = next(i for i in attrs["evidence"] if i["source"] == "usage")
    assert usage_item["workspace"] == "Work"
    assert usage_item["fresh"] is True
    assert usage_item["ttl_s"] == 15 * 60


def test_api_reachable_models_ttl_uses_fallback_when_auto_fetch_off():
    """[PLAN2-E5] auto_fetch off -> MODELS_TTL_FALLBACK (120 min), not 0."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    account = _account_coordinator(
        auto_fetch=False, base_interval_minutes=10,
        last_models_attempt=now, last_models_outcome="ok",
    )
    sensor = _reachable_sensor(account_coordinator=account)
    assert sensor.extra_state_attributes["models_ttl_s"] == (
        const.MODELS_TTL_FALLBACK_MINUTES * 60
    )


def test_api_reachable_state_and_attributes_share_one_snapshot(monkeypatch):
    """[REVIEW-2.0.0-MINOR] ``is_on`` and the attributes use the SAME snapshot.

    Regression: both properties used to compute the reachability with two
    different ``now`` values, so state and attributes could contradict each
    other at a TTL edge. The state write now caches the snapshot for the
    attribute read of the same update.
    """
    module = _live("go_gauge.account")
    real_snapshot = module.account_reachability_snapshot
    snapshots = []

    def _counting(hass):
        snapshot = real_snapshot(hass)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(module, "account_reachability_snapshot", _counting)
    sensor = _reachable_sensor()

    state = sensor.is_on
    attributes = sensor.extra_state_attributes

    assert state is None
    assert len(snapshots) == 1, "one computation per state write"
    assert attributes["checked_at"] == snapshots[0].now.isoformat()


if __name__ == "__main__":
    test_available_when_update_succeeds_and_subscription_active()
    test_unavailable_when_no_subscription_even_if_update_succeeded()
    test_unavailable_when_coordinator_update_failed()
    test_unavailable_when_update_failed_and_no_subscription()
    test_api_reachable_unknown_without_evidence()
    test_api_reachable_on_from_fresh_models_only()
    test_api_reachable_off_when_all_fresh_failures()
    test_api_reachable_neutral_no_subscription_is_unknown()
    test_api_reachable_stale_evidence_is_unknown()
    test_api_reachable_ttls_and_evidence_attributes()
    test_api_reachable_models_ttl_uses_fallback_when_auto_fetch_off()
    print("OK")
