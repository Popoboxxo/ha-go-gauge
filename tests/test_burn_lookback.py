#!/usr/bin/env python3
"""Unit tests for the HA-free burn-lookback/forecast kernel (2.1.0).

Covered contract (plan ``docs/PLAN-burn-lookback-2.1.0.md`` §3.1-§3.4):

* [REQ-015] option normalisation (defaults, ranges, clamping, invalid input),
  ``lookback_seconds``/``retention_seconds`` and the sample pruner,
* [REQ-015] the OLS slope in %/h over the configured lookback,
* [REQ-015] window-reset detection on percent drop OR ``resets_at`` change,
* [REQ-015] the ``lookback_too_short`` guard,
* [REQ-018] real calendar-month window length from ``resetsAt``,
* [REQ-018] forecast method selection (``zero``/``recent``/``pace``/
  ``unavailable``) including the ``t_min`` boundary.

``burn_lookback.py`` imports no ``homeassistant`` code (only the HA-free
``const`` module) and is loaded here with the same ``importlib`` pattern as
the rest of the suite, so these are real kernel assertions - no HA install,
no doubles.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# `burn_lookback` uses `from .const import ...`; load const first so the
# relative import resolves through sys.modules exactly like the coordinator.
const = _load("go_gauge.const", str(BASE / "const.py"))
bl = _load("go_gauge.burn_lookback", str(BASE / "burn_lookback.py"))

NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)
RESET = datetime(2026, 10, 26, 17, 0, 0, tzinfo=timezone.utc)

WINDOWS = {
    # window: (default, min, max, step)
    "5h": (120, 15, 300, 5),
    "week": (720, 60, 10080, 60),
    "month": (2880, 60, 10080, 60),
}


# ---------------------------------------------------------------------------
# [REQ-015] defaults, ranges, clamping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("window", ["5h", "week", "month"])
def test_default_lookback_minutes_matches_the_const_spec(window):
    """[REQ-015] Each window reports the documented default (D6)."""
    expected = WINDOWS[window][0]
    assert bl.default_lookback_minutes(window) == expected


@pytest.mark.parametrize("window", ["5h", "week", "month"])
def test_normalize_accepts_the_default_unchanged(window):
    """[REQ-015] A valid default passes through untouched."""
    default = WINDOWS[window][0]
    assert bl.normalize_lookback_minutes(window, default) == default


@pytest.mark.parametrize("window", ["5h", "week", "month"])
@pytest.mark.parametrize("invalid", [None, "abc", True, False])
def test_normalize_invalid_input_falls_back_to_the_default(window, invalid):
    """[REQ-015] ``None``/garbage/bool are not minute counts -> default.

    ``bool`` is excluded on purpose: ``True`` is an ``int`` subclass in Python
    but never a meaningful lookback, so it must not become 1 minute.
    """
    assert bl.normalize_lookback_minutes(window, invalid) == WINDOWS[window][0]


@pytest.mark.parametrize("window", ["5h", "week", "month"])
def test_normalize_clamps_to_min_and_max(window):
    """[REQ-015] Values below min / above max clamp to the spec bounds."""
    _, minimum, maximum, _ = WINDOWS[window]
    assert bl.normalize_lookback_minutes(window, minimum - 1) == minimum
    assert bl.normalize_lookback_minutes(window, maximum + 1) == maximum
    assert bl.normalize_lookback_minutes(window, 10 ** 9) == maximum


def test_normalize_accepts_numeric_strings_and_floats():
    """[REQ-015] Options may arrive as strings/floats (UI + storage)."""
    assert bl.normalize_lookback_minutes("5h", "300") == 300
    assert bl.normalize_lookback_minutes("week", 720.9) == 720


@pytest.mark.parametrize("window", ["bogus", "", "12h"])
def test_unknown_window_falls_back_to_120(window):
    """[REQ-015] An unknown window always yields the 5h default (120)."""
    assert bl.default_lookback_minutes(window) == 120
    assert bl.normalize_lookback_minutes(window, 999) == 120


def test_lookback_seconds_is_normalised_minutes_times_60():
    """[REQ-015] Effective lookback length in seconds."""
    assert bl.lookback_seconds("5h", 120) == 7200
    assert bl.lookback_seconds("week", 720) == 43200
    assert bl.lookback_seconds("month", 2880) == 172800


def test_lookback_seconds_clamps_like_the_coordinator():
    """[REQ-015] An out-of-range value reports the length actually used."""
    assert bl.lookback_seconds("5h", 5) == 15 * 60
    assert bl.lookback_seconds("5h", 20000) == 300 * 60


def test_retention_seconds_is_the_largest_slider_maximum():
    """[REQ-015] The window-less retention is 7 days (10080 min), not a lookback."""
    assert bl.retention_seconds() == 604800
    assert bl.retention_seconds() == const.BURN_LOOKBACK_RETENTION_SECONDS


@pytest.mark.parametrize(
    ("window", "seconds"),
    [
        ("5h", 18000),      # 300 min slider maximum (D6/D9)
        ("week", 604800),   # 10080 min slider maximum
        ("month", 604800),  # 10080 min slider maximum
    ],
)
def test_retention_seconds_is_per_window(window, seconds):
    """[REQ-015] Retention is the window's OWN slider maximum (D9, plan §9.3)."""
    assert bl.retention_seconds(window) == seconds
    assert bl.retention_seconds(window) == (
        const.BURN_LOOKBACK_WINDOWS[window]["max"] * 60
    )


# ---------------------------------------------------------------------------
# [REQ-015] prune_samples: retention + cap
# ---------------------------------------------------------------------------

def test_prune_drops_samples_older_than_retention():
    """[REQ-015] Older than 7 days -> dropped; the rest stays in order."""
    stale = (NOW - timedelta(seconds=const.BURN_LOOKBACK_RETENTION_SECONDS + 1), 10.0)
    fresh = (NOW - timedelta(hours=1), 20.0)
    assert bl.prune_samples([stale, fresh], NOW) == [fresh]


def test_prune_keeps_a_sample_exactly_at_the_cutoff():
    """[REQ-015] The retention boundary is inclusive (>= cutoff)."""
    edge = (NOW - timedelta(seconds=const.BURN_LOOKBACK_RETENTION_SECONDS), 10.0)
    assert bl.prune_samples([edge], NOW) == [edge]


def test_prune_accepts_an_explicit_retention_override():
    """[REQ-015] A caller-supplied horizon replaces the 7-day default."""
    old = (NOW - timedelta(hours=2), 1.0)
    new = (NOW - timedelta(minutes=30), 2.0)
    assert bl.prune_samples([old, new], NOW, retention=3600) == [new]


def test_prune_cap_keeps_the_newest_samples():
    """[REQ-015] With a cap the newest entries win (oldest dropped)."""
    samples = [(NOW - timedelta(minutes=10 - i), float(i)) for i in range(5)]
    assert bl.prune_samples(samples, NOW, cap=3) == samples[-3:]


def test_prune_cap_zero_keeps_nothing():
    """[REQ-015] A non-positive cap yields an empty history."""
    samples = [(NOW, 1.0)]
    assert bl.prune_samples(samples, NOW, cap=0) == []
    assert bl.prune_samples(samples, NOW, cap=-1) == []


def test_default_cap_keeps_the_newest_10080_samples():
    """[REQ-015] The production cap is 10080 samples per window (D9)."""
    assert const.BURN_SAMPLE_CAP == 10080
    samples = [
        (NOW - timedelta(seconds=10082 - i), float(i))
        for i in range(10082)
    ]
    pruned = bl.prune_samples(samples, NOW)
    assert len(pruned) == 10080
    assert pruned == samples[2:]


def test_prune_tolerates_unsorted_and_two_tuple_input():
    """[REQ-015] 2-tuples and unsorted lists are readable (legacy runtime)."""
    older = (NOW - timedelta(minutes=30), 10.0)
    newer = (NOW, 20.0)
    assert bl.prune_samples([newer, older], NOW) == [newer, older]


def test_prune_drops_samples_without_a_readable_timestamp():
    """[REQ-015] A sample that cannot be age-checked is dropped."""
    assert bl.prune_samples([(None, 10.0), ("not-a-date", 20.0), (NOW, 30.0)], NOW) == [
        (NOW, 30.0)
    ]


def test_prune_empty_or_none_returns_empty():
    """[REQ-015] No history -> no crash."""
    assert bl.prune_samples([], NOW) == []
    assert bl.prune_samples(None, NOW) == []


# ---------------------------------------------------------------------------
# [REQ-015] OLS slope in %/h
# ---------------------------------------------------------------------------

def test_ols_perfect_line_yields_the_exact_slope():
    """[REQ-015] 10 pct-points over one hour -> exactly 10.0 %/h."""
    samples = [(NOW - timedelta(hours=1), 10.0), (NOW, 20.0)]
    assert bl.ols_slope_per_hour(samples, NOW, 120) == 10.0


def test_ols_noisy_line_uses_all_points():
    """[REQ-015] OLS averages jitter instead of trusting one noisy pair.

    Seven 10-minutely samples rising by ~1.16 pct/h; the two-point slope of the
    first/last pair would be 1.00, so this assertion really exercises the
    least-squares fit (and its 2-decimal rounding).
    """
    samples = [
        (NOW - timedelta(minutes=60 - 10 * i), percent)
        for i, percent in enumerate(
            [10.0, 10.4, 10.1, 10.6, 10.9, 11.2, 11.0]
        )
    ]
    assert bl.ols_slope_per_hour(samples, NOW, 120) == pytest.approx(1.16, abs=0.01)


def test_ols_requires_two_samples():
    """[REQ-015] Fewer than two usable samples -> None."""
    assert bl.ols_slope_per_hour([], NOW, 120) is None
    assert bl.ols_slope_per_hour([(NOW, 20.0)], NOW, 120) is None
    assert bl.ols_slope_per_hour(None, NOW, 120) is None


def test_ols_rejects_a_span_below_300_seconds():
    """[REQ-015] A 299 s span amplifies poll jitter -> None."""
    samples = [(NOW - timedelta(seconds=299), 10.0), (NOW, 20.0)]
    assert bl.ols_slope_per_hour(samples, NOW, 120) is None


def test_ols_accepts_a_span_of_exactly_300_seconds():
    """[REQ-015] 300 s is the inclusive minimum span -> concrete value."""
    samples = [(NOW - timedelta(seconds=300), 10.0), (NOW, 11.0)]
    assert bl.ols_slope_per_hour(samples, NOW, 120) == 12.0


def test_ols_negative_slope_yields_none():
    """[REQ-015] A drop is a window reset, never a negative burn rate."""
    samples = [(NOW - timedelta(hours=1), 50.0), (NOW, 20.0)]
    assert bl.ols_slope_per_hour(samples, NOW, 120) is None


def test_ols_flat_line_yields_zero():
    """[REQ-015] An unchanged percent is 0.0 %/h, not None."""
    samples = [(NOW - timedelta(hours=1), 50.0), (NOW, 50.0)]
    assert bl.ols_slope_per_hour(samples, NOW, 120) == 0.0


def test_ols_ignores_samples_outside_the_lookback():
    """[REQ-015] A 3 h-old outlier is outside 2 h and must not skew the fit."""
    samples = [
        (NOW - timedelta(hours=3), 0.0),
        (NOW - timedelta(hours=1), 10.0),
        (NOW, 20.0),
    ]
    assert bl.ols_slope_per_hour(samples, NOW, 120) == 10.0


def test_ols_rounds_to_two_decimals():
    """[REQ-015] 2/3 %/h is reported as 0.67 (plain ``==``, not approx)."""
    samples = [(NOW - timedelta(seconds=5400), 10.0), (NOW, 11.0)]
    assert bl.ols_slope_per_hour(samples, NOW, 120) == 0.67


def test_ols_none_lookback_yields_none():
    """[REQ-015] No configured lookback -> no slope."""
    samples = [(NOW - timedelta(hours=1), 10.0), (NOW, 20.0)]
    assert bl.ols_slope_per_hour(samples, NOW, None) is None


# ---------------------------------------------------------------------------
# [REQ-015] window-reset detection (D10)
# ---------------------------------------------------------------------------

def test_reset_on_percent_drop():
    """[REQ-015] 50 -> 20 is a window rollover."""
    assert bl.is_window_reset(50.0, RESET, 20.0, RESET) is True


def test_no_reset_on_equal_percent():
    """[REQ-015] 50 -> 50 in the same window keeps the history."""
    assert bl.is_window_reset(50.0, RESET, 50.0, RESET) is False


def test_no_reset_on_rising_percent():
    """[REQ-015] A rise is normal consumption, not a reset."""
    assert bl.is_window_reset(20.0, RESET, 50.0, RESET) is False


def test_reset_on_resets_at_change_even_with_equal_percent():
    """[REQ-015] 100 -> 100 in a NEW window is still a rollover (D10)."""
    later = RESET + timedelta(hours=5)
    assert bl.is_window_reset(100.0, RESET, 100.0, later) is True


def test_no_reset_when_both_resets_at_are_none():
    """[REQ-015] Without resets_at only the percent rule applies."""
    assert bl.is_window_reset(50.0, None, 50.0, None) is False


def test_legacy_two_tuple_previous_sample_against_three_tuple_new_one():
    """[REQ-015] A legacy 2-tuple has no resets_at -> the first new sample with
    one counts as a change and clears the (incomplete) history.

    That is the documented D10 consequence of tolerating old 2-tuples: the
    reader sees ``prev_resets_at is None`` vs a real timestamp. One poll of
    history is discarded after an upgrade; every subsequent sample carries the
    marker and compares like-for-like.
    """
    previous = (NOW - timedelta(minutes=10), 50.0)
    assert bl.sample_resets_at(previous) is None
    assert bl.is_window_reset(
        bl.sample_percent(previous), bl.sample_resets_at(previous), 50.0, RESET
    ) is True
    # Two consecutive 3-tuples with the same marker stay stable.
    assert bl.is_window_reset(50.0, RESET, 50.0, RESET) is False


# ---------------------------------------------------------------------------
# [REQ-015] lookback_too_short (D24)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("minutes", "refresh", "expected"),
    [
        (19, 10, True),   # threshold 20
        (20, 10, False),
        (120, 10, False),
        (4, 1, True),     # threshold max(2, 5) = 5
        (5, 1, False),
        (4, 2, True),     # threshold max(4, 5) = 5
        (5, 2, False),
        (15, 10, True),   # a fresh 5h minimum is still too short at 10 min poll
    ],
)
def test_lookback_too_short_boundaries(minutes, refresh, expected):
    """[REQ-015] ``lookback < max(2 * refresh, 5)`` - both boundaries pinned."""
    assert bl.lookback_too_short(minutes, refresh) is expected


def test_lookback_too_short_falls_back_to_10_minute_refresh():
    """[REQ-015] A missing refresh interval uses the 10-min default."""
    assert bl.lookback_too_short(19, None) is True
    assert bl.lookback_too_short(20, None) is False


def test_lookback_too_short_is_true_for_non_numeric_minutes():
    """[REQ-015] A lookback that is not a number cannot hold two polls."""
    assert bl.lookback_too_short(None, 10) is True
    assert bl.lookback_too_short("abc", 10) is True


# ---------------------------------------------------------------------------
# [REQ-018] real calendar-month window length
# ---------------------------------------------------------------------------

def test_calendar_window_seconds_for_fixed_windows():
    """[REQ-018] 5h/week stay their constants (no calendar logic)."""
    assert bl.calendar_window_seconds("5h", RESET) == 18000.0
    assert bl.calendar_window_seconds("week", RESET) == 604800.0


@pytest.mark.parametrize(
    ("resets_at", "days"),
    [
        # Plan §3.3 (M1): ``period_start = resets_at - 1 calendar month`` (day
        # clamped via ``calendar.monthrange``), ``T = resets_at - period_start``.
        # The length therefore follows the month BEFORE the reset instant, not
        # the month ``resets_at`` itself falls into.
        ("2026-11-01T00:00:00Z", 31),  # start 2026-10-01 -> 31 days
        ("2026-11-15T12:00:00Z", 31),  # start 2026-10-15 -> 31 days (mid-month)
        ("2026-09-30T00:00:00Z", 31),  # start 2026-08-30 -> 31 days
        ("2026-03-30T00:00:00Z", 30),  # start 2026-02-28 (day clamp) -> 30 days
        ("2026-12-01T00:00:00Z", 30),  # start 2026-11-01 -> 30 days
        ("2027-03-01T00:00:00Z", 28),  # start 2027-02-01 -> non-leap February
        ("2028-03-01T00:00:00Z", 29),  # start 2028-02-01 -> leap February
    ],
)
def test_calendar_month_uses_the_real_month_length(resets_at, days):
    """[REQ-018] Month window = ``resets_at`` minus one calendar month (§3.3)."""
    assert bl.calendar_window_seconds("month", resets_at) == days * 86400


@pytest.mark.parametrize("resets_at", ["not-a-date", "", None, 123])
def test_calendar_month_falls_back_to_30_days(resets_at):
    """[REQ-018] Unparseable ``resetsAt`` -> nominal 30-day fallback."""
    assert bl.calendar_window_seconds("month", resets_at) == 30 * 86400


def test_calendar_window_seconds_unknown_window_is_none():
    """[REQ-018] Unknown windows have no length."""
    assert bl.calendar_window_seconds("bogus", RESET) is None


# ---------------------------------------------------------------------------
# [REQ-018] compute_forecast: method selection + t_min
# ---------------------------------------------------------------------------

def test_forecast_zero_percent_uses_the_zero_method():
    """[REQ-018] ``percent == 0`` -> ``0.0`` with method ``zero`` (D11)."""
    reset = NOW + timedelta(hours=1)
    result = bl.compute_forecast("5h", 0, reset, NOW, burn_rate=None)

    assert result["forecast"] == 0.0
    assert result["forecast_recent"] == 0.0
    assert result["forecast_method"] == "zero"
    assert result["window_seconds"] == 18000.0


@pytest.mark.parametrize("resets_at", [None, "not-a-date", ""])
def test_forecast_unavailable_without_a_valid_resets_at(resets_at):
    """[REQ-018] Missing/invalid ``resetsAt`` -> None + ``unavailable`` (D23)."""
    result = bl.compute_forecast("5h", 42.0, resets_at, NOW, burn_rate=1.0)

    assert result["forecast"] is None
    assert result["forecast_method"] == "unavailable"
    assert result["window_seconds"] is None


def test_forecast_unavailable_for_a_non_numeric_percent():
    """[REQ-018] No percent -> no number; the nominal window length is reported."""
    result = bl.compute_forecast("5h", None, NOW + timedelta(hours=1), NOW, None)

    assert result["forecast"] is None
    assert result["forecast_method"] == "unavailable"
    assert result["window_seconds"] == 18000.0


def test_forecast_unavailable_before_t_min_without_a_burn_rate():
    """[REQ-018] Before 15 min a pace projection is meaningless; no rate -> None."""
    reset = NOW + timedelta(hours=5) - timedelta(seconds=600)  # 600 s elapsed
    result = bl.compute_forecast("5h", 5.0, reset, NOW, burn_rate=None)

    assert result["forecast"] is None
    assert result["forecast_method"] == "unavailable"


def test_forecast_uses_the_recent_rate_before_15_minutes():
    """[REQ-018] 600 s elapsed -> ``pct + burn_rate * remaining_h`` (D11)."""
    reset = NOW + timedelta(seconds=18000 - 600)
    result = bl.compute_forecast("5h", 5.0, reset, NOW, burn_rate=2.0)

    # 5 + 2 %/h * (17400 s / 3600) = 5 + 9.666... = 14.7
    assert result["forecast"] == 14.7
    assert result["forecast_recent"] == 14.7
    assert result["forecast_method"] == "recent"


def test_forecast_switches_to_pace_at_exactly_900_seconds():
    """[REQ-018] The 15-min ``t_min`` is inclusive: 900 s -> ``pace``."""
    reset = NOW + timedelta(seconds=18000 - 900)
    result = bl.compute_forecast("5h", 5.0, reset, NOW, burn_rate=2.0)

    assert result["forecast"] == 100.0  # 5 * 18000 / 900
    assert result["forecast_method"] == "pace"
    # The recent-rate value is still reported next to it (D12).
    assert result["forecast_recent"] == pytest.approx(14.5, abs=0.1)


def test_forecast_stays_recent_at_899_seconds():
    """[REQ-018] One second below ``t_min`` still uses the recent rate."""
    reset = NOW + timedelta(seconds=18000 - 899)
    result = bl.compute_forecast("5h", 5.0, reset, NOW, burn_rate=2.0)

    assert result["forecast_method"] == "recent"
    assert result["forecast"] == result["forecast_recent"]


def test_forecast_month_t_min_is_one_percent_of_the_real_window():
    """[REQ-018] 31-day month -> ``t_min`` is 1 % = 26784 s, not 900 s."""
    reset = datetime(2026, 11, 1, tzinfo=timezone.utc)
    window_seconds = 31 * 86400
    t_min = window_seconds // 100  # 26784 s

    at_boundary = reset - timedelta(seconds=window_seconds - t_min)
    result = bl.compute_forecast("month", 10.0, reset, at_boundary, burn_rate=1.0)
    assert result["forecast_method"] == "pace"
    assert result["forecast"] == 1000.0  # 10 * T / (T / 100)
    assert result["elapsed_fraction"] == pytest.approx(0.01, abs=1e-9)

    just_before = reset - timedelta(seconds=window_seconds - (t_min - 1))
    result = bl.compute_forecast("month", 10.0, reset, just_before, burn_rate=1.0)
    assert result["forecast_method"] == "recent"


def test_forecast_clamps_a_stale_resets_at():
    """[REQ-018] A stale reset (past) clamps elapsed to the full window."""
    reset = NOW - timedelta(hours=2)
    result = bl.compute_forecast("5h", 42.0, reset, NOW, burn_rate=1.0)

    assert result["elapsed_fraction"] == 1.0
    assert result["remaining_seconds"] == 0.0
    assert result["forecast"] == 42.0
    assert result["forecast_method"] == "pace"


def test_forecast_window_seconds_for_month_is_the_calendar_month():
    """[REQ-018] The reported ``window_seconds`` is the real month length."""
    reset = datetime(2026, 11, 1, tzinfo=timezone.utc)
    now = datetime(2026, 10, 22, tzinfo=timezone.utc)
    result = bl.compute_forecast("month", 82.8, reset, now, burn_rate=None)

    assert result["window_seconds"] == 31 * 86400
    assert result["remaining_seconds"] == 10 * 86400
