#!/usr/bin/env python3
"""Coordinator integration for the configurable burn lookback (2.1.0).

[REQ-015] The coordinator is the single owner of the sample history and the
live lookback: it parses the three option keys, keeps them clamped, clears the
history on a window rollover (percent drop OR ``resets_at`` change), prunes to
the 7-day retention/cap and exposes ``burn_rate`` / ``burn_rate_diagnostics`` /
``forecast_details`` to the entities.

Loading mirrors ``tests/test_derived_usage.py``: real modules via ``importlib``
on top of the shared homeassistant fakes from ``tests/conftest.py``.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"

conftest.install_ha_stubs()


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

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
RESETS_AT = datetime(2026, 10, 26, 17, 0, 0, tzinfo=timezone.utc)
DEFAULT_LOOKBACKS = {"5h": 120, "week": 720, "month": 2880}


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _FakeHass:
    """Anything the coordinator constructor passes to the session lookup."""


def _make_coordinator(options: dict | None = None) -> "coord.GoGaugeCoordinator":
    """Build a real coordinator without any HTTP/session dependency."""
    coord.async_get_clientsession = lambda _hass: object()
    return coord.GoGaugeCoordinator(_FakeHass(), ["token"], options or {})


def _bare_coordinator(**attrs) -> "coord.GoGaugeCoordinator":
    """A coordinator without ``__init__`` (no HA plumbing needed)."""
    c = coord.GoGaugeCoordinator.__new__(coord.GoGaugeCoordinator)
    c._usage_samples = {}
    c.burn_lookback_minutes = dict(DEFAULT_LOOKBACKS)
    c.usage_minutes = 10
    c.data = {}
    for key, value in attrs.items():
        setattr(c, key, value)
    return c


# ---------------------------------------------------------------------------
# [REQ-015] option parsing + live setter
# ---------------------------------------------------------------------------

def test_options_default_the_three_lookbacks():
    """[REQ-015] A ConfigEntry without options uses 120/720/2880 (D6)."""
    c = _make_coordinator({})

    assert c.burn_lookback_minutes == DEFAULT_LOOKBACKS


def test_options_are_clamped_on_parse():
    """[REQ-015] Out-of-range/garbage option values are normalised at startup."""
    c = _make_coordinator({
        const.CONF_BURN_LOOKBACK_5H_MINUTES: 5,
        const.CONF_BURN_LOOKBACK_WEEK_MINUTES: 20000,
        const.CONF_BURN_LOOKBACK_MONTH_MINUTES: "abc",
    })

    assert c.burn_lookback_minutes == {"5h": 15, "week": 10080, "month": 2880}


def test_options_keep_valid_values():
    """[REQ-015] Valid options are used as-is."""
    c = _make_coordinator({
        const.CONF_BURN_LOOKBACK_5H_MINUTES: 240,
        const.CONF_BURN_LOOKBACK_WEEK_MINUTES: 1440,
        const.CONF_BURN_LOOKBACK_MONTH_MINUTES: 5760,
    })

    assert c.burn_lookback_minutes == {"5h": 240, "week": 1440, "month": 5760}


def test_set_burn_lookback_updates_live_and_clamps():
    """[REQ-015] The Number setter path mutates the live value, clamped."""
    c = _bare_coordinator()

    c.set_burn_lookback("5h", 240)
    assert c.burn_lookback_minutes["5h"] == 240

    c.set_burn_lookback("5h", 20000)
    assert c.burn_lookback_minutes["5h"] == 300

    c.set_burn_lookback("5h", 1)
    assert c.burn_lookback_minutes["5h"] == 15

    c.set_burn_lookback("week", 20000)
    assert c.burn_lookback_minutes["week"] == 10080


# ---------------------------------------------------------------------------
# [REQ-015] sample recording + rollover detection (D10)
# ---------------------------------------------------------------------------

def test_record_usage_sample_stores_three_tuple_with_resets_at():
    """[REQ-015] New samples carry the parsed ``resets_at`` as third element."""
    c = _bare_coordinator()

    c._record_usage_sample("ws1", "5h", 42.5, NOW, RESETS_AT)

    assert c._usage_samples["ws1:5h"] == [(NOW, 42.5, RESETS_AT)]


def test_record_usage_sample_clears_on_percent_drop():
    """[REQ-015] A lower percent means the window rolled over."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 80.0, NOW - timedelta(minutes=10), RESETS_AT)
    c._record_usage_sample("ws1", "5h", 5.0, NOW, RESETS_AT)

    assert c._usage_samples["ws1:5h"] == [(NOW, 5.0, RESETS_AT)]


def test_record_usage_sample_clears_on_resets_at_change():
    """[REQ-015] 100 -> 100 in a new window still clears (D10)."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 100.0, NOW - timedelta(minutes=10), RESETS_AT)
    new_reset = RESETS_AT + timedelta(hours=5)
    c._record_usage_sample("ws1", "5h", 100.0, NOW, new_reset)

    assert c._usage_samples["ws1:5h"] == [(NOW, 100.0, new_reset)]


def test_record_usage_sample_keeps_history_on_equal_percent_and_resets_at():
    """[REQ-015] Same percent + same marker is a normal repeat poll."""
    earlier = NOW - timedelta(minutes=10)
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 40.0, earlier, RESETS_AT)
    c._record_usage_sample("ws1", "5h", 40.0, NOW, RESETS_AT)

    assert c._usage_samples["ws1:5h"] == [
        (earlier, 40.0, RESETS_AT),
        (NOW, 40.0, RESETS_AT),
    ]


def test_raising_the_lookback_uses_older_samples_immediately():
    """[REQ-015] Retention covers the whole slider range, so a raise works at once.

    Two samples 4 h and 3 h old are outside the 120 min default lookback but
    inside the 300 min maximum. They must still be in the store, and raising
    the lookback must make ``burn_rate`` use them without new polls (D9).
    """
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 10.0, NOW - timedelta(hours=4), RESETS_AT)
    c._record_usage_sample("ws1", "5h", 20.0, NOW - timedelta(hours=3), RESETS_AT)
    assert len(c._usage_samples["ws1:5h"]) == 2

    assert c.burn_rate("ws1", "5h", now=NOW) is None  # outside 120 min

    c.set_burn_lookback("5h", 300)
    assert c.burn_rate("ws1", "5h", now=NOW) == 10.0


def test_record_usage_sample_uses_the_window_specific_retention():
    """[REQ-015] 5h prunes at 18000 s; week/month keep the 7-day maximum (D9).

    Plan §9 item 3: retention is per window, not one global horizon.
    """
    c = _bare_coordinator()
    old = NOW - timedelta(days=2)  # inside 7 d, outside the 5 h maximum

    for window in ("5h", "week", "month"):
        c._record_usage_sample("ws1", window, 10.0, old, RESETS_AT)
        c._record_usage_sample("ws1", window, 20.0, NOW, RESETS_AT)

    assert c._usage_samples["ws1:5h"] == [(NOW, 20.0, RESETS_AT)]
    assert c._usage_samples["ws1:week"] == [
        (old, 10.0, RESETS_AT),
        (NOW, 20.0, RESETS_AT),
    ]
    assert c._usage_samples["ws1:month"] == [
        (old, 10.0, RESETS_AT),
        (NOW, 20.0, RESETS_AT),
    ]


def test_record_usage_sample_still_caps_each_window_at_10080(monkeypatch):
    """[REQ-015] The per-window retention keeps the 10080-sample cap (D9)."""
    assert const.BURN_SAMPLE_CAP == 10080
    monkeypatch.setattr(coord, "BURN_SAMPLE_CAP", 4)
    c = _bare_coordinator()
    base = NOW - timedelta(seconds=10)
    for i in range(6):
        c._record_usage_sample("ws1", "5h", float(i), base + timedelta(seconds=i))

    samples = c._usage_samples["ws1:5h"]
    assert len(samples) == 4
    assert samples[0] == (base + timedelta(seconds=2), 2.0, None)
    assert samples[-1] == (base + timedelta(seconds=5), 5.0, None)


# ---------------------------------------------------------------------------
# [REQ-015] burn_rate
# ---------------------------------------------------------------------------

def test_burn_rate_uses_the_configured_lookback():
    """[REQ-015] OLS over the samples inside the window's lookback."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 10.0, NOW - timedelta(hours=1), RESETS_AT)
    c._record_usage_sample("ws1", "5h", 20.0, NOW, RESETS_AT)

    assert c.burn_rate("ws1", "5h", now=NOW) == 10.0


def test_burn_rate_none_for_too_few_samples():
    """[REQ-015] A single sample cannot yield a slope."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 10.0, NOW, RESETS_AT)

    assert c.burn_rate("ws1", "5h", now=NOW) is None


def test_burn_rate_none_for_a_short_span():
    """[REQ-015] Less than 300 s between samples -> None."""
    c = _bare_coordinator()
    c._record_usage_sample("ws1", "5h", 10.0, NOW - timedelta(seconds=60), RESETS_AT)
    c._record_usage_sample("ws1", "5h", 20.0, NOW, RESETS_AT)

    assert c.burn_rate("ws1", "5h", now=NOW) is None


def test_burn_rate_none_for_a_negative_slope():
    """[REQ-015] A drop (unpruned history) is not a rate."""
    c = _bare_coordinator()
    c._usage_samples["ws1:5h"] = [
        (NOW - timedelta(hours=1), 50.0, RESETS_AT),
        (NOW, 20.0, RESETS_AT),
    ]

    assert c.burn_rate("ws1", "5h", now=NOW) is None


def test_burn_rate_tolerates_legacy_two_tuple_samples():
    """[REQ-015] Pre-2.1.0 2-tuples remain readable."""
    c = _bare_coordinator()
    c._usage_samples["ws1:5h"] = [
        (NOW - timedelta(hours=1), 10.0),
        (NOW, 20.0),
    ]

    assert c.burn_rate("ws1", "5h", now=NOW) == 10.0


def test_burn_rate_is_none_for_an_unknown_workspace():
    """[REQ-015] No samples for the key -> None."""
    c = _bare_coordinator()

    assert c.burn_rate("nope", "5h", now=NOW) is None


# ---------------------------------------------------------------------------
# [REQ-015] burn_rate_diagnostics (D14/D21/D24)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("minutes", "expected_too_short"),
    [(15, True), (19, True), (20, False), (120, False)],
)
def test_burn_rate_diagnostics_reports_the_guard(minutes, expected_too_short):
    """[REQ-015] With a 10-min refresh the guard flips at 20 min (D24)."""
    c = _bare_coordinator(
        burn_lookback_minutes={"5h": minutes, "week": 720, "month": 2880},
        usage_minutes=10,
    )
    c._usage_samples["ws1:5h"] = [
        (NOW - timedelta(minutes=10), 10.0, RESETS_AT),
        (NOW - timedelta(minutes=5), 12.0, RESETS_AT),
    ]

    result = c.burn_rate_diagnostics("ws1", "5h", now=NOW)

    assert result["lookback_minutes"] == minutes
    assert result["lookback_seconds"] == minutes * 60
    assert result["lookback_too_short"] is expected_too_short


def test_burn_rate_diagnostics_counts_only_samples_inside_the_lookback():
    """[REQ-015] ``samples`` explains a ``None`` rate (configured lookback)."""
    c = _bare_coordinator(
        burn_lookback_minutes={"5h": 15, "week": 720, "month": 2880},
    )
    c._usage_samples["ws1:5h"] = [
        (NOW - timedelta(minutes=60), 1.0, RESETS_AT),  # outside 15 min
        (NOW - timedelta(minutes=10), 2.0, RESETS_AT),
        (NOW - timedelta(minutes=5), 3.0, RESETS_AT),
    ]

    result = c.burn_rate_diagnostics("ws1", "5h", now=NOW)

    assert result["samples"] == 2
    assert result["lookback_minutes"] == 15


# ---------------------------------------------------------------------------
# [REQ-018] forecast_details reads the published snapshot
# ---------------------------------------------------------------------------

def _snapshot_workspace(percent, resets_at, key="ws1", window="5h"):
    return {
        "workspaces": [{
            "key": key,
            "status": "ok",
            "windows": {window: {"percent": percent, "resets_at": resets_at}},
        }],
    }


def test_forecast_details_reads_percent_and_resets_at_from_the_snapshot():
    """[REQ-018] 50 % after one of five hours -> 250 % pace projection."""
    c = _bare_coordinator(data=_snapshot_workspace(50.0, NOW + timedelta(hours=4)))
    c._usage_samples["ws1:5h"] = [
        (NOW - timedelta(hours=1), 40.0, RESETS_AT),
        (NOW, 50.0, RESETS_AT),
    ]

    result = c.forecast_details("ws1", "5h", now=NOW)

    assert result["forecast"] == 250.0
    assert result["forecast_method"] == "pace"
    assert result["window_seconds"] == 18000.0
    assert result["forecast_recent"] == pytest.approx(50.0 + 10.0 * 4, abs=0.1)


def test_forecast_details_tolerates_a_missing_workspace():
    """[REQ-018] Unknown workspace -> unavailable, never an exception (D23)."""
    c = _bare_coordinator(data={"workspaces": []})

    result = c.forecast_details("missing", "5h", now=NOW)

    assert result["forecast"] is None
    assert result["forecast_method"] == "unavailable"


def test_forecast_details_tolerates_missing_data_attribute():
    """[REQ-018] A bare double without ``data`` -> unavailable."""
    c = _bare_coordinator()
    del c.data

    result = c.forecast_details("ws1", "5h", now=NOW)

    assert result["forecast"] is None
    assert result["forecast_method"] == "unavailable"


@pytest.mark.parametrize("status", ["no_subscription", "error"])
def test_forecast_details_is_unavailable_for_a_degraded_workspace(status):
    """[REQ-018] ``status`` no_subscription/error short-circuits (plan §9 m2).

    The workspace snapshot deliberately carries a usable percent, a future
    ``resets_at`` AND burn samples: without the status guard this would compute
    a ``pace`` forecast. The guard must win over all of that (D23: no fake
    number for a workspace without usable usage data).
    """
    snapshot = _snapshot_workspace(50.0, NOW + timedelta(hours=4))
    snapshot["workspaces"][0]["status"] = status
    c = _bare_coordinator(data=snapshot)
    c._usage_samples["ws1:5h"] = [
        (NOW - timedelta(hours=1), 40.0, RESETS_AT),
        (NOW, 50.0, RESETS_AT),
    ]

    result = c.forecast_details("ws1", "5h", now=NOW)

    assert result == {
        "forecast": None,
        "forecast_recent": None,
        "forecast_method": "unavailable",
        "elapsed_fraction": None,
        "window_seconds": 18000.0,
        "remaining_seconds": None,
    }


def test_forecast_details_is_unavailable_without_resets_at():
    """[REQ-018] A missing ``resets_at`` is unavailable end-to-end (D23)."""
    c = _bare_coordinator(data=_snapshot_workspace(42.0, None))

    result = c.forecast_details("ws1", "5h", now=NOW)

    assert result["forecast"] is None
    assert result["forecast_recent"] is None
    assert result["forecast_method"] == "unavailable"
    assert result["elapsed_fraction"] is None
    assert result["window_seconds"] is None


def test_forecast_details_zero_percent_is_zero_not_unavailable():
    """[REQ-018] 0 % -> 0.0 / method ``zero`` end-to-end (D11)."""
    c = _bare_coordinator(data=_snapshot_workspace(0, NOW + timedelta(hours=4)))

    result = c.forecast_details("ws1", "5h", now=NOW)

    assert result["forecast"] == 0.0
    assert result["forecast_recent"] == 0.0
    assert result["forecast_method"] == "zero"
    assert result["window_seconds"] == 18000.0
