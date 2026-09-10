#!/usr/bin/env python3
"""TDD Red tests for the derived-usage kernel functions and sensors.

[REQ-013] remaining_percent(ws, win): Restbudget = 100 - used percent.
[REQ-014] seconds_until_reset(ws, win, now): Restzeit until the reset.
[REQ-015] burn_rate_per_hour(samples, now): consumption slope in %/h.
[REQ-016] Wiring: RemainingBudgetSensor / TimeUntilResetSensor /
         BurnRateSensor are created per workspace window by the REAL
         async_setup_entry and each reads its own window.

These are the TDD Red phase of the `feat/derived-usage-sensors` feature:
custom_components/go_gauge/{coordinator,sensor}.py do NOT implement the
functions/classes above yet, so every test in this file is expected to
FAIL until the implementation lands.

File style mirrors tests/test_forecast_pace.py (pure kernel functions loaded
dynamically via importlib) and tests/test_sensor_wiring.py (drives the real
async_setup_entry). Base homeassistant.* fakes come from tests/conftest.py.

NOTE: the project currently has no docs/REQUIREMENTS.md; REQ-013..REQ-016
continue the existing numbering of tests/test_coordinator_parsing.py
(REQ-006..REQ-012) for the derived-usage key contract.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"

# Base homeassistant.* module fakes are installed by conftest.py before this
# test file is collected. DataUpdateCoordinator needs its subscriptable
# stand-in and sensor.py subclasses CoordinatorEntity via entity.py.
_uc = sys.modules["homeassistant.helpers.update_coordinator"]


def _class_getitem(cls, item):
    return cls


_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)}
)


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


_uc.CoordinatorEntity = _CoordinatorEntity


def _enum_like(*members):
    return type("EnumStub", (), {m.upper(): m for m in members})


# "duration" is required because TimeUntilResetSensor declares
# SensorDeviceClass.DURATION at class-body time.
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like("measurement")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like(
    "timestamp", "duration"
)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
sensor = _load("go_gauge.sensor", str(BASE / "sensor.py"))

# Deterministic reference "now" for every kernel test below (timezone-aware).
NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


def _ws(percent, win, resets_at=None, status="ok"):
    return {
        "status": status,
        "windows": {win: {"percent": percent, "resets_at": resets_at}},
    }


# ---------------------------------------------------------------------------
# [REQ-013] remaining_percent
# ---------------------------------------------------------------------------

def test_remaining_percent_ok_status():
    """[REQ-013] 42.5% used -> 57.5% remaining."""
    assert coord.remaining_percent(_ws(42.5, "5h"), "5h") == 57.5


def test_remaining_percent_integer_value():
    """[REQ-013] integer percent is coerced to float: 33 -> 67.0."""
    assert coord.remaining_percent(_ws(33, "week"), "week") == 67.0


def test_remaining_percent_none_for_no_subscription():
    """[REQ-013] no_subscription status -> None (not a fake number)."""
    ws = _ws(0, "month", status="no_subscription")
    assert coord.remaining_percent(ws, "month") is None


def test_remaining_percent_none_for_error_status():
    """[REQ-013] error status -> None."""
    ws = _ws(10, "month", status="error")
    assert coord.remaining_percent(ws, "month") is None


def test_remaining_percent_none_for_non_numeric_percent():
    """[REQ-013] percent=None -> None."""
    assert coord.remaining_percent(_ws(None, "5h"), "5h") is None


def test_remaining_percent_none_for_missing_window():
    """[REQ-013] window key absent -> None."""
    ws = _ws(42.5, "5h")
    assert coord.remaining_percent(ws, "week") is None


def test_remaining_percent_none_for_missing_workspace():
    """[REQ-013] ws is None -> None."""
    assert coord.remaining_percent(None, "5h") is None


# ---------------------------------------------------------------------------
# [REQ-014] seconds_until_reset
# ---------------------------------------------------------------------------

def test_seconds_until_reset_future():
    """[REQ-014] 1h ahead of now -> 3600.0 seconds."""
    resets_at = NOW + timedelta(hours=1)
    assert coord.seconds_until_reset(_ws(10, "5h", resets_at), "5h", now=NOW) == 3600.0


def test_seconds_until_reset_past_is_clamped_to_zero():
    """[REQ-014] stale reset in the past is clamped to 0.0, never negative."""
    resets_at = NOW - timedelta(minutes=30)
    assert coord.seconds_until_reset(_ws(99, "5h", resets_at), "5h", now=NOW) == 0.0


def test_seconds_until_reset_none_without_resets_at():
    """[REQ-014] window without resets_at -> None."""
    assert coord.seconds_until_reset(_ws(10, "5h"), "5h", now=NOW) is None


def test_seconds_until_reset_none_for_missing_workspace():
    """[REQ-014] ws is None -> None."""
    assert coord.seconds_until_reset(None, "5h", now=NOW) is None


def test_seconds_until_reset_none_for_missing_window():
    """[REQ-014] window key absent -> None."""
    ws = _ws(10, "5h", NOW + timedelta(hours=1))
    assert coord.seconds_until_reset(ws, "month", now=NOW) is None


def test_seconds_until_reset_none_for_non_datetime_resets_at():
    """[REQ-014] a string resets_at is not a datetime -> None."""
    ws = {"status": "ok", "windows": {"5h": {"percent": 10, "resets_at": "2026-09-07T13:00:00Z"}}}
    assert coord.seconds_until_reset(ws, "5h", now=NOW) is None


# ---------------------------------------------------------------------------
# [REQ-015] burn_rate_per_hour
# ---------------------------------------------------------------------------

def test_burn_rate_basic_two_sample_slope():
    """[REQ-015] 10 pct-points over 1h -> 10.0 %/h."""
    samples = [(NOW - timedelta(hours=1), 10.0), (NOW, 20.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) == pytest.approx(10.0)


def test_burn_rate_none_for_empty_samples():
    """[REQ-015] empty sample list -> None."""
    assert coord.burn_rate_per_hour([], now=NOW) is None


def test_burn_rate_none_for_single_sample():
    """[REQ-015] fewer than two samples -> None."""
    assert coord.burn_rate_per_hour([(NOW, 20.0)], now=NOW) is None


def test_burn_rate_none_for_span_below_minimum():
    """[REQ-015] a 60s span is below the 5min minimum -> None."""
    samples = [(NOW - timedelta(seconds=60), 10.0), (NOW, 20.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) is None


def test_burn_rate_none_on_negative_slope_reset():
    """[REQ-015] a drop (window reset) is not a rate -> None."""
    samples = [(NOW - timedelta(hours=1), 50.0), (NOW, 20.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) is None


def test_burn_rate_constant_percent_is_zero():
    """[REQ-015] unchanged percent -> 0.0, not None."""
    samples = [(NOW - timedelta(hours=1), 50.0), (NOW, 50.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) == 0.0


def test_burn_rate_excludes_samples_outside_lookback():
    """[REQ-015] a 3h-old sample is dropped; the 1h slope (10.0) is used."""
    samples = [
        (NOW - timedelta(hours=3), 0.0),
        (NOW - timedelta(hours=1), 10.0),
        (NOW, 20.0),
    ]
    assert coord.burn_rate_per_hour(samples, now=NOW) == pytest.approx(10.0)


def test_burn_rate_none_when_lookback_leaves_single_sample():
    """[REQ-015] only one sample inside the lookback window -> None."""
    samples = [(NOW - timedelta(hours=3), 0.0), (NOW, 20.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) is None


def test_burn_rate_lookback_boundary_is_inclusive():
    """[REQ-015] a sample exactly at the 2h lookback edge still counts."""
    samples = [
        (NOW - timedelta(seconds=coord.BURN_RATE_LOOKBACK_SECONDS), 0.0),
        (NOW, 20.0),
    ]
    assert coord.burn_rate_per_hour(samples, now=NOW) == pytest.approx(10.0)


def test_burn_rate_rounds_to_two_decimals():
    """[REQ-015] a non-terminating slope (2/3 %/h) is rounded to 0.67.

    Samples 10.0 -> 11.0 over 5400s yield exactly 2/3 %/h. The plain `==`
    against 0.67 fails the moment the `round(rate, 2)` in the kernel is
    removed or its precision changes (a `pytest.approx` would hide that).
    """
    samples = [(NOW - timedelta(seconds=5400), 10.0), (NOW, 11.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) == 0.67


def test_burn_rate_min_span_boundary_299_seconds_is_none():
    """[REQ-015] a 299s span is just below the 300s minimum -> None."""
    samples = [(NOW - timedelta(seconds=299), 10.0), (NOW, 20.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) is None


def test_burn_rate_min_span_boundary_300_seconds_is_concrete():
    """[REQ-015] a 300s span is exactly at the minimum -> computed, not None."""
    samples = [(NOW - timedelta(seconds=300), 10.0), (NOW, 11.0)]
    assert coord.burn_rate_per_hour(samples, now=NOW) == 12.0


# ---------------------------------------------------------------------------
# [REQ-015] GoGaugeCoordinator._record_usage_sample history recorder
# ---------------------------------------------------------------------------

def _bare_coordinator() -> "coord.GoGaugeCoordinator":
    """A GoGaugeCoordinator without __init__ (no HA session/request needed)."""
    c = coord.GoGaugeCoordinator.__new__(coord.GoGaugeCoordinator)
    c._usage_samples = {}
    return c


def test_record_usage_sample_appends_numeric_percent():
    """[REQ-015] a numeric percent is stored as one (ts, percent) sample."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 42.5, NOW)
    assert c._usage_samples == {"ws1:5h": [(NOW, 42.5)]}


def test_record_usage_sample_keeps_key_per_workspace_window():
    """[REQ-015] samples are namespaced by "<key>:<win>" and never mixed."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 10.0, NOW)
    c._record_usage_sample("ws1", "week", 20.0, NOW)
    c._record_usage_sample("ws2", "5h", 30.0, NOW)
    assert set(c._usage_samples) == {"ws1:5h", "ws1:week", "ws2:5h"}
    assert c._usage_samples["ws1:5h"] == [(NOW, 10.0)]
    assert c._usage_samples["ws1:week"] == [(NOW, 20.0)]
    assert c._usage_samples["ws2:5h"] == [(NOW, 30.0)]


def test_record_usage_sample_clears_history_on_percent_drop():
    """[REQ-015] a percent drop (window reset) clears prior history first."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 50.0, NOW - timedelta(minutes=10))
    c._record_usage_sample("ws1", "5h", 20.0, NOW)
    assert c._usage_samples["ws1:5h"] == [(NOW, 20.0)]


def test_record_usage_sample_keeps_history_on_equal_percent():
    """[REQ-015] an unchanged percent is not a drop - both samples stay."""
    c = _bare_coordinator()
    earlier = NOW - timedelta(minutes=10)
    c._record_usage_sample("ws1", "5h", 50.0, earlier)
    c._record_usage_sample("ws1", "5h", 50.0, NOW)
    assert c._usage_samples["ws1:5h"] == [(earlier, 50.0), (NOW, 50.0)]


def test_record_usage_sample_prunes_beyond_lookback():
    """[REQ-015] a sample older than the lookback is dropped; the rest stays."""
    c = _bare_coordinator()
    stale = NOW - timedelta(seconds=coord.BURN_RATE_LOOKBACK_SECONDS + 1)
    c._record_usage_sample("ws1", "5h", 10.0, stale)
    c._record_usage_sample("ws1", "5h", 20.0, NOW)
    assert c._usage_samples["ws1:5h"] == [(NOW, 20.0)]


def test_record_usage_sample_keeps_sample_exactly_at_lookback_edge():
    """[REQ-015] the lookback prune boundary is inclusive (>= cutoff)."""
    c = _bare_coordinator()
    edge = NOW - timedelta(seconds=coord.BURN_RATE_LOOKBACK_SECONDS)
    c._record_usage_sample("ws1", "5h", 10.0, edge)
    c._record_usage_sample("ws1", "5h", 20.0, NOW)
    assert c._usage_samples["ws1:5h"] == [(edge, 10.0), (NOW, 20.0)]


def test_record_usage_sample_caps_history_at_64_entries():
    """[REQ-015] only the newest 64 samples are retained."""
    c = _bare_coordinator()
    base = NOW - timedelta(seconds=200)
    for i in range(70):
        c._record_usage_sample("ws1", "5h", float(i), base + timedelta(seconds=i))
    samples = c._usage_samples["ws1:5h"]
    assert len(samples) == 64
    assert samples[0] == (base + timedelta(seconds=6), 6.0)
    assert samples[-1] == (base + timedelta(seconds=69), 69.0)


# ---------------------------------------------------------------------------
# [REQ-015] only a successful ("ok") usage fetch records samples
# ---------------------------------------------------------------------------

class _MockUsageClient:
    """Mock API client: fixed response or a raised transport error."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def fetch_usage(self, token):
        if self._error is not None:
            raise self._error
        return self._response

    async def fetch_models(self):
        return []


class _MockUsageCoordinator:
    """Minimal coordinator state to drive the real _async_update_data."""

    def __init__(self, response=None, error=None):
        self.hass = None
        self._tokens = ["token"]
        self._client = _MockUsageClient(response, error)
        self.auto_usage = True
        self.usage_minutes = const.DEFAULT_USAGE_REFRESH_MINUTES
        self.auto_models = False
        self.models_minutes = const.DEFAULT_MODELS_REFRESH_MINUTES
        self.warn_percent = const.DEFAULT_WARN_PERCENT
        self._skip_reload = False
        self.last_models_fetch = None
        self.last_usage_fetch = None
        self.data = None
        self.is_catalog_owner = False
        self._usage_samples: dict = {}


def _run_update(response=None, error=None):
    """Drive the real _async_update_data with a mocked usage API client."""
    c = _MockUsageCoordinator(response=response, error=error)
    c.__class__ = type(
        "GoGaugeCoordinator", (_MockUsageCoordinator, coord.GoGaugeCoordinator), {}
    )
    c._async_update_data = coord.GoGaugeCoordinator._async_update_data.__get__(c)
    result = asyncio.run(c._async_update_data())
    return c, result


def _api_usage(rolling=10.0, weekly=20.0, monthly=30.0):
    def blk(pct):
        return {"percent": pct, "status": "ok", "resetsAt": "2026-09-07T13:00:00Z"}

    return {
        "usage": {
            "rolling": blk(rolling),
            "weekly": blk(weekly),
            "monthly": blk(monthly),
        },
        "status": "ok",
    }


def test_update_data_records_samples_only_on_ok_status():
    """[REQ-015] an ok fetch records exactly one sample per window."""
    c, result = _run_update(response=_api_usage())
    assert result["workspaces"][0]["status"] == "ok"
    assert set(c._usage_samples) == {"ws1:5h", "ws1:week", "ws1:month"}
    assert [pct for _, pct in c._usage_samples["ws1:5h"]] == [10.0]
    assert [pct for _, pct in c._usage_samples["ws1:week"]] == [20.0]
    assert [pct for _, pct in c._usage_samples["ws1:month"]] == [30.0]


def test_update_data_records_no_sample_on_error_status():
    """[REQ-015] an error status records nothing even with numeric percents.

    The response deliberately carries populated windows so removing the
    `status == "ok"` gate around the recording loop would leak samples and
    fail here - a bare `usage: {}` error response would not catch that.
    """
    c, result = _run_update(response={**_api_usage(), "status": "error"})
    assert result["workspaces"][0]["status"] == "error"
    assert c._usage_samples == {}


def test_update_data_records_no_sample_on_transport_exception():
    """[REQ-015] a raised transport error keeps old state and records nothing."""
    c, result = _run_update(error=RuntimeError("network down"))
    assert result["workspaces"][0]["status"] == "error"
    assert c._usage_samples == {}


def test_update_data_records_no_sample_for_non_numeric_percent():
    """[REQ-015] an ok fetch with percent=None records no sample for that window."""
    c, result = _run_update(response=_api_usage(rolling=None))
    assert result["workspaces"][0]["status"] == "ok"
    assert "ws1:5h" not in c._usage_samples
    assert len(c._usage_samples["ws1:week"]) == 1
    assert len(c._usage_samples["ws1:month"]) == 1


# ---------------------------------------------------------------------------
# [REQ-016] sensor wiring via the real async_setup_entry
# ---------------------------------------------------------------------------

class _FakeCoordinator:
    """Minimal coordinator mirroring tests/test_sensor_wiring.py.

    `_usage_samples` must exist (here as an empty class attribute) because
    BurnRateSensor reads it; the wiring test overrides it with per-window
    samples. A dict is intentional - sensor.py calls `.get(...)` on it.
    """

    is_catalog_owner = False
    ws_name = "Work"
    _usage_samples: dict = {}

    def __init__(self, data):
        self.data = data


class _FakeEntry:
    entry_id = "test_entry"


class _FakeHass:
    def __init__(self, coordinator):
        self.data = {const.DOMAIN: {_FakeEntry.entry_id: coordinator}}


def test_derived_usage_sensors_wired_per_window():
    """[REQ-016] all three new sensor classes exist for 5h/week/month and
    each reads its own window's data."""
    real_now = datetime.now(timezone.utc)
    data = {
        "workspaces": [{
            "key": "ws1",
            "status": "ok",
            "windows": {
                "5h": {
                    "percent": 12.5,
                    "status": "ok",
                    "resets_at": real_now + timedelta(hours=1),
                },
                "week": {
                    "percent": 40.0,
                    "status": "ok",
                    "resets_at": real_now + timedelta(hours=2),
                },
                "month": {
                    "percent": 77.0,
                    "status": "ok",
                    "resets_at": real_now + timedelta(hours=3),
                },
            },
        }],
        "models_block": None,
    }
    coordinator = _FakeCoordinator(data)
    # Distinct burn-rate slopes per window so a window/label swap is visible.
    coordinator._usage_samples = {
        "ws1:5h": [(real_now - timedelta(hours=1), 0.0), (real_now, 10.0)],
        "ws1:week": [(real_now - timedelta(hours=1), 0.0), (real_now, 20.0)],
        "ws1:month": [(real_now - timedelta(hours=1), 0.0), (real_now, 30.0)],
    }
    hass = _FakeHass(coordinator)
    entry = _FakeEntry()

    added: list = []
    asyncio.run(sensor.async_setup_entry(hass, entry, added.extend))

    remaining = {e._win: e for e in added if isinstance(e, sensor.RemainingBudgetSensor)}
    assert set(remaining) == {"5h", "week", "month"}
    assert remaining["5h"].native_value == 87.5
    assert remaining["week"].native_value == 60.0
    assert remaining["month"].native_value == 23.0

    time_to_reset = {e._win: e for e in added if isinstance(e, sensor.TimeUntilResetSensor)}
    assert set(time_to_reset) == {"5h", "week", "month"}
    assert time_to_reset["5h"].native_value == pytest.approx(1.0, abs=0.01)
    assert time_to_reset["week"].native_value == pytest.approx(2.0, abs=0.01)
    assert time_to_reset["month"].native_value == pytest.approx(3.0, abs=0.01)

    burn_rate = {e._win: e for e in added if isinstance(e, sensor.BurnRateSensor)}
    assert set(burn_rate) == {"5h", "week", "month"}
    assert burn_rate["5h"].native_value < burn_rate["week"].native_value
    assert burn_rate["week"].native_value < burn_rate["month"].native_value
    assert burn_rate["month"].native_value == pytest.approx(30.0, rel=0.01)


def test_burn_rate_sensor_defaults_to_empty_samples():
    """[REQ-016] BurnRateSensor with no recorded samples -> None (not a crash)."""
    coordinator = _FakeCoordinator({
        "workspaces": [{
            "key": "ws1",
            "status": "ok",
            "windows": {"5h": {"percent": 10.0, "status": "ok", "resets_at": None}},
        }],
        "models_block": None,
    })
    entry = _FakeEntry()
    entity = sensor.BurnRateSensor(
        coordinator, entry, key="ws1", win="5h", label="5-Hour", ws_name="Work"
    )
    entity.coordinator = coordinator
    assert entity.native_value is None


def test_derived_usage_sensor_unique_ids_per_window():
    """[REQ-016] unique_id suffixes per window are pinned (iron rule).

    unique_id stability is a hard repo rule - changing a suffix silently
    recreates entities and breaks user automations/dashboards.
    """
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

    expected = {
        sensor.RemainingBudgetSensor: "_remaining",
        sensor.TimeUntilResetSensor: "_time_to_reset",
        sensor.BurnRateSensor: "_burn_rate",
    }
    for cls, suffix in expected.items():
        by_win = {e._win: e for e in added if isinstance(e, cls)}
        assert set(by_win) == {"5h", "week", "month"}
        for win, entity in by_win.items():
            assert entity._attr_unique_id == f"test_entry_ws1_{win}{suffix}"
            assert entity._attr_unique_id.endswith(suffix)


def test_time_until_reset_sensor_is_duration_in_hours():
    """[REQ-016] TimeUntilResetSensor is a DURATION entity reported in hours."""
    assert sensor.TimeUntilResetSensor._attr_device_class == sensor.SensorDeviceClass.DURATION
    assert sensor.TimeUntilResetSensor._attr_native_unit_of_measurement == "h"
