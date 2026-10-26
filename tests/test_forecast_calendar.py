#!/usr/bin/env python3
"""Acceptance test for the calendar-month forecast (2.1.0, plan §3.3).

[REQ-018] The "month" window must project onto the REAL length of the calendar
month taken from ``resetsAt``, not the nominal 30 days. A 31-day month that is
31/31 complete at the same pace therefore reaches a higher forecast than the
old 30-day approximation - and lower when the month is shorter than 30 days.

Motivation (plan §3.3): a 31-day month yielded a forecast near 256.8 % where
the nominal 30-day window yielded 248.4 %; dropping the real length hid the
fact that the remaining 10 days contain 3.2 percentage points more budget.
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


const = _load("go_gauge.const", str(BASE / "const.py"))
bl = _load("go_gauge.burn_lookback", str(BASE / "burn_lookback.py"))


# 31-day October: the window ends at 2026-11-01T00:00:00Z.
RESETS_AT = datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc)
OCTOBER_SECONDS = 31 * 86400
# 10 of the 31 days elapsed -> 21 days (and 82.8 % usage) left behind us.
NOW_10_OF_31 = datetime(2026, 10, 11, 0, 0, 0, tzinfo=timezone.utc)
PERCENT = 82.8


def test_month_forecast_uses_the_31_day_calendar_window():
    """[REQ-018] 82.8 % after 10/31 days -> ~256.7 %, not the 30-day 248.4 %.

    Both formulas share the same ``percent`` and ``t``; only ``T`` differs
    (31 vs. 30 days), which is exactly what this acceptance case isolates:
    ``82.8 * 31 / 10 = 256.68`` vs. ``82.8 * 30 / 10 = 248.4``.
    """
    result = bl.compute_forecast(
        "month", PERCENT, RESETS_AT, NOW_10_OF_31, burn_rate=None
    )

    assert result["window_seconds"] == OCTOBER_SECONDS
    assert result["forecast"] == pytest.approx(256.7, abs=1.0)
    assert abs(result["forecast"] - 256.8) <= 1.0
    # Discriminating assertion: the nominal 30-day result must NOT appear.
    assert result["forecast"] != pytest.approx(248.4, abs=0.5)
    assert result["forecast_method"] == "pace"


def test_month_elapsed_fraction_is_ten_of_thirty_one_days():
    """[REQ-018] ``elapsed_fraction`` uses the real 31-day denominator."""
    result = bl.compute_forecast(
        "month", PERCENT, RESETS_AT, NOW_10_OF_31, burn_rate=None
    )

    assert result["elapsed_fraction"] == pytest.approx(10 / 31, abs=1e-9)
    assert result["remaining_seconds"] == 21 * 86400


def test_week_window_still_uses_604800_seconds():
    """[REQ-018] Only "month" is calendar-based; the week stays 7 days."""
    now = RESETS_AT - timedelta(days=3)
    result = bl.compute_forecast("week", 25.0, RESETS_AT, now, burn_rate=None)

    assert result["window_seconds"] == 604800.0
    assert result["elapsed_fraction"] == pytest.approx(4 / 7, abs=1e-9)
    assert result["forecast"] == pytest.approx(43.8, abs=0.1)  # 25 * 7/4


def test_month_forecast_with_the_same_instant_as_the_plan_date_pair():
    """[REQ-018] Regression pin for the plan's literal date pair.

    The plan text pairs ``resets_at = 2026-11-01`` with ``now = 2026-10-22``.
    Those instants are 10 days APART, i.e. the window has 21/31 days elapsed
    and 10 days remaining - the opposite of "10 of 31 days elapsed". With the
    approved formula (plan §3.2: ``t = T - (resets_at - now)``) the correct
    result is ~122.2 %, not 256.7 %. This test documents that production
    math and the "10 of 31 elapsed" acceptance values belong to
    ``now = 2026-10-11`` (pinned above), and that the date pair must not be
    misread as an elapsed-time calculation.
    """
    now = datetime(2026, 10, 22, 0, 0, 0, tzinfo=timezone.utc)
    result = bl.compute_forecast("month", PERCENT, RESETS_AT, now, burn_rate=None)

    assert result["elapsed_fraction"] == pytest.approx(21 / 31, abs=1e-9)
    assert result["remaining_seconds"] == 10 * 86400
    assert result["forecast"] == pytest.approx(122.2, abs=0.1)


def test_forecast_recent_is_reported_alongside_a_pace_forecast():
    """[REQ-018] ``forecast_recent`` is an attribute even when method=pace (D12)."""
    result = bl.compute_forecast(
        "month", PERCENT, RESETS_AT, NOW_10_OF_31, burn_rate=1.5
    )

    assert result["forecast_method"] == "pace"
    assert result["forecast_recent"] == pytest.approx(
        PERCENT + 1.5 * (21 * 24), abs=0.1
    )


def test_month_forecast_is_zero_when_nothing_was_used():
    """[REQ-018] ``percent == 0`` -> 0.0 with method ``zero``."""
    result = bl.compute_forecast("month", 0, RESETS_AT, NOW_10_OF_31, burn_rate=None)

    assert result["forecast"] == 0.0
    assert result["forecast_method"] == "zero"
    assert result["window_seconds"] == OCTOBER_SECONDS


def test_month_forecast_is_unavailable_without_resets_at():
    """[REQ-018] No ``resetsAt`` -> no fake calendar guess (D23)."""
    result = bl.compute_forecast("month", PERCENT, None, NOW_10_OF_31, burn_rate=None)

    assert result["forecast"] is None
    assert result["forecast_method"] == "unavailable"


def test_month_forecast_stays_below_the_nominal_value_for_a_short_month():
    """[REQ-018] February (28 days) projects lower than the 30-day nominal.

    82.8 % after 10/28 days -> 231.8 % vs. the nominal ``82.8 * 30 / 10``.
    """
    feb_resets_at = datetime(2027, 3, 1, tzinfo=timezone.utc)  # non-leap Feb
    now = datetime(2027, 2, 11, tzinfo=timezone.utc)  # 10 of 28 days elapsed
    result = bl.compute_forecast(
        "month", PERCENT, feb_resets_at, now, burn_rate=None
    )

    assert result["window_seconds"] == 28 * 86400
    assert result["forecast"] == pytest.approx(231.8, abs=1.0)
    assert result["forecast"] < 30 * 86400 * PERCENT / (10 * 86400)
