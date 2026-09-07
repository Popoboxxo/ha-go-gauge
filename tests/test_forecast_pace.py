#!/usr/bin/env python3
"""Unit tests for the forecast/pace kernel functions in coordinator.py.

Covers window_elapsed_fraction, forecast_percent, pace_status - the pure
math behind the new "Prognose"/"Pace" sensors (Daniel-Feature-Wunsch
2026-09-07): linear pace projection per window (5h/week/month) plus a
green/yellow/red classification against two configurable thresholds.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"

# Base homeassistant.* module fakes are installed by conftest.py before this
# test file is collected. DataUpdateCoordinator still needs a subscriptable
# stand-in (matches test_coordinator_parsing.py's setup).


def _class_getitem(cls, item):
    return cls


_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)}
)
_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})

_aiohttp = sys.modules["aiohttp"]
if not hasattr(_aiohttp, "ClientTimeout"):
    class _ClientTimeout:
        def __init__(self, total=None):
            self.total = total
    _aiohttp.ClientTimeout = _ClientTimeout


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("go_gauge.const", str(BASE / "const.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))

NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


def _ws(percent, win, resets_at, status="ok"):
    return {"status": status, "windows": {win: {"percent": percent, "resets_at": resets_at}}}


# ---------------------------------------------------------------------------
# window_elapsed_fraction
# ---------------------------------------------------------------------------

def test_elapsed_fraction_halfway_through_5h_window():
    resets_at = NOW + timedelta(hours=2, minutes=30)  # half of 5h remaining
    frac = coord.window_elapsed_fraction("5h", resets_at, now=NOW)
    assert frac == 0.5


def test_elapsed_fraction_none_right_after_reset():
    """Reset just happened (full window remaining) -> elapsed <= 0 -> None."""
    resets_at = NOW + timedelta(hours=5)
    assert coord.window_elapsed_fraction("5h", resets_at, now=NOW) is None


def test_elapsed_fraction_none_without_resets_at():
    assert coord.window_elapsed_fraction("5h", None, now=NOW) is None


def test_elapsed_fraction_capped_at_one_for_stale_reset():
    """resets_at already in the past (stale data) -> clamp to 1.0, not >1."""
    resets_at = NOW - timedelta(hours=1)
    assert coord.window_elapsed_fraction("5h", resets_at, now=NOW) == 1.0


# ---------------------------------------------------------------------------
# forecast_percent
# ---------------------------------------------------------------------------

def test_forecast_extrapolates_linearly():
    """40% used at the halfway point of the window -> projected 80% at end."""
    resets_at = NOW + timedelta(hours=2, minutes=30)
    ws = _ws(40, "5h", resets_at)
    assert coord.forecast_percent(ws, "5h", now=NOW) == 80.0


def test_forecast_can_exceed_100_percent():
    resets_at = NOW + timedelta(hours=1)  # 4/5 elapsed
    ws = _ws(90, "5h", resets_at)
    assert coord.forecast_percent(ws, "5h", now=NOW) == 112.5


def test_forecast_none_right_after_reset():
    resets_at = NOW + timedelta(hours=5)
    ws = _ws(1, "5h", resets_at)
    assert coord.forecast_percent(ws, "5h", now=NOW) is None


def test_forecast_none_for_no_subscription():
    resets_at = NOW + timedelta(hours=2)
    ws = _ws(40, "5h", resets_at, status="no_subscription")
    assert coord.forecast_percent(ws, "5h", now=NOW) is None


def test_forecast_none_for_missing_workspace():
    assert coord.forecast_percent(None, "5h", now=NOW) is None


def test_forecast_none_for_non_numeric_percent():
    resets_at = NOW + timedelta(hours=2)
    ws = _ws(None, "5h", resets_at)
    assert coord.forecast_percent(ws, "5h", now=NOW) is None


# ---------------------------------------------------------------------------
# pace_status
# ---------------------------------------------------------------------------

def test_pace_status_green_below_threshold():
    assert coord.pace_status(50.0, green_below=80, red_above=100) == "green"


def test_pace_status_yellow_in_target_band():
    assert coord.pace_status(90.0, green_below=80, red_above=100) == "yellow"
    assert coord.pace_status(100.0, green_below=80, red_above=100) == "yellow"


def test_pace_status_red_above_threshold():
    assert coord.pace_status(101.0, green_below=80, red_above=100) == "red"


def test_pace_status_clamps_inverted_thresholds():
    """Misconfigured red < green: red_above is clamped up to green_below,
    collapsing the yellow band to zero width instead of inverting it."""
    assert coord.pace_status(85.0, green_below=90, red_above=50) == "green"  # < 90
    assert coord.pace_status(95.0, green_below=90, red_above=50) == "red"    # > 90
    assert coord.pace_status(10.0, green_below=90, red_above=50) == "green"
