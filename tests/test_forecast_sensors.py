#!/usr/bin/env python3
"""Headline forecast/pace/burn-rate sensors (2.1.0, plan §3.2/§9 M2-M3).

[REQ-018] ``UsageForecastSensor`` publishes ``forecast_details()["forecast"]``
as its state and the four forecast attributes (``forecast_recent``,
``forecast_method``, ``elapsed_fraction``, ``window_seconds``).
``UsagePaceSensor`` derives its traffic light from the PRIMARY forecast
(``pace_status(forecast, warn, red)`` - D12, präzisiert in plan §9.2) and adds
``forecast_percent``/``green_below``/``red_above`` next to the forecast
attributes. [REQ-015] ``BurnRateSensor`` exposes ``lookback_minutes``,
``samples`` and ``lookback_too_short`` from ``burn_rate_diagnostics()``.

Loading follows ``tests/test_derived_usage.py``: the real sensor module via
``importlib`` on top of the shared homeassistant fakes from
``tests/conftest.py``. The coordinator is a fake that returns a fixed
``forecast_details`` block, so these tests pin the SENSOR contract (state +
exact attribute dicts); the forecast kernel itself is covered by
``tests/test_burn_lookback.py`` and ``tests/test_forecast_calendar.py``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"

conftest.install_ha_stubs()

# Base homeassistant.* fakes are installed by conftest.py before this test file
# is collected. Use conftest's real coordinator double (it has a working
# ``__init__`` and ``__class_getitem__``) instead of a bare throwaway class, so
# modules imported AFTER this one can still construct a GoGaugeCoordinator.
conftest.install_coordinator_stub()

_uc = sys.modules["homeassistant.helpers.update_coordinator"]


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


_uc.CoordinatorEntity = _CoordinatorEntity


def _enum_like(*members):
    return type("EnumStub", (), {m.upper(): m for m in members})


# "duration"/"timestamp"/"enum" are read at class-body time by the other
# sensors in sensor.py (TimeUntilResetSensor, ResetTimestampSensor,
# ApiStatusSensor) - this file only instantiates forecast/pace/burn-rate, but
# the module import defines all of them.
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like("measurement")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like(
    "timestamp", "duration", "enum"
)


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
_load("go_gauge.account_logic", str(BASE / "account_logic.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
sensor = _load("go_gauge.sensor", str(BASE / "sensor.py"))

# Realistic 5h fixture: 250 % projected pace, the recent-rate block next to it.
WARN_PERCENT = 80.0
RED_PERCENT = 100.0
WINDOW_SECONDS = 18000.0

# Exactly the shape ``burn_lookback.compute_forecast`` returns.
FORECAST_BLOCKS = {
    "pace": {
        "forecast": 250.0,
        "forecast_recent": 90.0,
        "forecast_method": "pace",
        "elapsed_fraction": 0.2,
        "window_seconds": WINDOW_SECONDS,
        "remaining_seconds": 14400.0,
    },
    "recent": {
        "forecast": 14.7,
        "forecast_recent": 14.7,
        "forecast_method": "recent",
        "elapsed_fraction": 0.03333333333333333,
        "window_seconds": WINDOW_SECONDS,
        "remaining_seconds": 17400.0,
    },
    "zero": {
        "forecast": 0.0,
        "forecast_recent": 0.0,
        "forecast_method": "zero",
        "elapsed_fraction": 0.2,
        "window_seconds": WINDOW_SECONDS,
        "remaining_seconds": 14400.0,
    },
    "unavailable": {
        "forecast": None,
        "forecast_recent": None,
        "forecast_method": "unavailable",
        "elapsed_fraction": None,
        "window_seconds": WINDOW_SECONDS,
        "remaining_seconds": None,
    },
}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _FakeEntry:
    """ConfigEntry double: only ``entry_id`` is read by the entity base."""

    entry_id = "ws_entry"
    data: dict = {}


class _FakeCoordinator:
    """Coordinator double: fixed forecast block + burn-rate diagnostics."""

    ws_name = "Work"
    warn_percent = WARN_PERCENT
    pace_red_percent = RED_PERCENT

    def __init__(self, details, *, burn_rate=None, diagnostics=None):
        self._details = details
        self._burn_rate = burn_rate
        self._diagnostics = diagnostics if diagnostics is not None else {
            "lookback_minutes": 120,
            "samples": 0,
            "lookback_seconds": 7200,
            "lookback_too_short": False,
        }
        self.data = {"workspaces": []}
        self.forecast_calls = []

    def forecast_details(self, key, win):
        self.forecast_calls.append((key, win))
        return self._details

    def burn_rate(self, key, win):
        return self._burn_rate

    def burn_rate_diagnostics(self, key, win):
        return self._diagnostics


def _forecast_sensor(coordinator, win="5h"):
    return sensor.UsageForecastSensor(
        coordinator, _FakeEntry(), key="ws1", win=win, label="5h rolling"
    )


def _pace_sensor(coordinator, win="5h"):
    return sensor.UsagePaceSensor(
        coordinator, _FakeEntry(), key="ws1", win=win, label="5h rolling"
    )


def _burn_rate_sensor(coordinator, win="5h"):
    return sensor.BurnRateSensor(
        coordinator, _FakeEntry(), key="ws1", win=win, label="5h rolling"
    )


# ---------------------------------------------------------------------------
# [REQ-018] UsageForecastSensor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "expected_value"),
    [
        ("pace", 250.0),
        ("recent", 14.7),
        ("zero", 0.0),
        ("unavailable", None),
    ],
)
def test_forecast_sensor_native_value_per_method(method, expected_value):
    """[REQ-018] The state is ``forecast_details()["forecast"]`` for all methods."""
    entity = _forecast_sensor(_FakeCoordinator(FORECAST_BLOCKS[method]))

    assert entity.native_value == expected_value


@pytest.mark.parametrize("method", ["pace", "recent", "zero", "unavailable"])
def test_forecast_sensor_attributes_are_the_forecast_block(method):
    """[REQ-018] Exactly the four forecast attributes, no more, no less (D22)."""
    block = FORECAST_BLOCKS[method]
    entity = _forecast_sensor(_FakeCoordinator(block))

    assert entity.extra_state_attributes == {
        "forecast_recent": block["forecast_recent"],
        "forecast_method": method,
        "elapsed_fraction": block["elapsed_fraction"],
        "window_seconds": block["window_seconds"],
    }


def test_forecast_sensor_reads_its_own_workspace_window():
    """[REQ-018] The sensor queries its own key/window pair."""
    coordinator = _FakeCoordinator(FORECAST_BLOCKS["pace"])
    entity = _forecast_sensor(coordinator, win="week")

    _ = entity.native_value

    assert coordinator.forecast_calls == [("ws1", "week")]


# ---------------------------------------------------------------------------
# [REQ-018] UsagePaceSensor: traffic light from the PRIMARY forecast (D12/§9.2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("method", "forecast", "forecast_recent", "expected_state"),
    [
        ("pace", 250.0, 90.0, "red"),
        # Early window: the primary forecast IS the recent fallback (D12).
        ("recent", 14.7, 14.7, "green"),
        # Deliberately divergent fixture: the light must follow ``forecast``,
        # never ``forecast_recent`` (the D12/§9.2 regression pin).
        ("recent", 110.0, 20.0, "red"),
        ("pace", 85.0, 85.0, "yellow"),
        ("zero", 0.0, 0.0, "green"),
        ("unavailable", None, None, None),
    ],
)
def test_pace_sensor_traffic_light_uses_the_primary_forecast(
    method, forecast, forecast_recent, expected_state
):
    """[REQ-018] ``pace_status(forecast, warn, red)`` - not the recent block."""
    block = {
        "forecast": forecast,
        "forecast_recent": forecast_recent,
        "forecast_method": method,
        "elapsed_fraction": 0.2,
        "window_seconds": WINDOW_SECONDS,
        "remaining_seconds": 14400.0,
    }
    entity = _pace_sensor(_FakeCoordinator(block))

    assert entity.native_value == expected_state


@pytest.mark.parametrize(
    ("method", "forecast", "forecast_recent", "expected_state"),
    [
        ("pace", 250.0, 90.0, "red"),
        ("recent", 14.7, 14.7, "green"),
        ("unavailable", None, None, None),
    ],
)
def test_pace_sensor_attributes_are_exact(
    method, forecast, forecast_recent, expected_state
):
    """[REQ-018] Pace attributes: forecast block + thresholds (D22)."""
    block = {
        "forecast": forecast,
        "forecast_recent": forecast_recent,
        "forecast_method": method,
        "elapsed_fraction": 0.2,
        "window_seconds": WINDOW_SECONDS,
        "remaining_seconds": 14400.0,
    }
    entity = _pace_sensor(_FakeCoordinator(block))

    assert entity.extra_state_attributes == {
        "forecast_percent": forecast,
        "green_below": WARN_PERCENT,
        "red_above": RED_PERCENT,
        "forecast_recent": forecast_recent,
        "forecast_method": method,
        "elapsed_fraction": 0.2,
        "window_seconds": WINDOW_SECONDS,
    }


def test_pace_sensor_icon_follows_the_traffic_light():
    """[REQ-018] The icon mirrors the light (red -> close-circle)."""
    entity = _pace_sensor(_FakeCoordinator(FORECAST_BLOCKS["pace"]))

    assert entity.native_value == "red"
    assert entity.icon == "mdi:close-circle-outline"


# ---------------------------------------------------------------------------
# [REQ-015] BurnRateSensor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("minutes", "samples", "too_short"),
    [
        (120, 3, False),
        (15, 1, True),
    ],
)
def test_burn_rate_sensor_exposes_the_diagnostics_attributes(minutes, samples, too_short):
    """[REQ-015] ``lookback_minutes``/``samples``/``lookback_too_short`` (D21/D22)."""
    coordinator = _FakeCoordinator(
        FORECAST_BLOCKS["pace"],
        burn_rate=12.5,
        diagnostics={
            "lookback_minutes": minutes,
            "samples": samples,
            # Not part of the entity contract - must be filtered out.
            "lookback_seconds": minutes * 60,
            "lookback_too_short": too_short,
        },
    )
    entity = _burn_rate_sensor(coordinator)

    assert entity.native_value == 12.5
    assert entity.extra_state_attributes == {
        "lookback_minutes": minutes,
        "samples": samples,
        "lookback_too_short": too_short,
    }


def test_burn_rate_sensor_native_value_is_none_without_a_rate():
    """[REQ-015] Too few samples -> ``None`` (never a fake 0 %/h)."""
    entity = _burn_rate_sensor(_FakeCoordinator(FORECAST_BLOCKS["pace"], burn_rate=None))

    assert entity.native_value is None


# ---------------------------------------------------------------------------
# [REQ-018] conftest package shim (prep for the import-fallback removal, §9)
# ---------------------------------------------------------------------------

def test_conftest_registers_the_go_gauge_package_shim():
    """[REQ-018] conftest exposes a real ``go_gauge`` package with ``__path__``."""
    package = sys.modules["go_gauge"]

    assert hasattr(package, "__path__")
    assert package.__path__ == [str(BASE)]
