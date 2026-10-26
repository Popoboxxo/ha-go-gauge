"""Go Gauge HA - burn-rate lookback and forecast kernel (HA-free).

Pure logic behind the configurable burn-rate lookback (2.1.0): option
normalisation/defaults, sample retention, the OLS slope over the configured
lookback, the ``lookback_too_short`` guard, the real calendar-month window
length and the two forecast methods (Ø-pace vs. recent-rate fallback).

Deliberately imports no ``homeassistant`` code (only the HA-free ``const``
module), so every function is directly unit-testable without a Home Assistant
installation. See ``docs/PLAN-burn-lookback-2.1.0.md`` (§2 D1-D24, §3.1-§3.4).
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from typing import Any

from .const import (
    BURN_LOOKBACK_RETENTION_SECONDS,
    BURN_LOOKBACK_WINDOWS,
    BURN_RATE_MIN_SAMPLES,
    BURN_RATE_MIN_SPAN_SECONDS,
    BURN_SAMPLE_CAP,
    LOOKBACK_TOO_SHORT_MIN_MINUTES,
    LOOKBACK_TOO_SHORT_REFRESH_FACTOR,
    MIN_FORECAST_ELAPSED_FRACTION,
    MIN_FORECAST_ELAPSED_SECONDS,
    WINDOW_SECONDS,
)

# Fallbacks used by the resilient input handling below. The default lookback is
# derived from the single source of truth (the 5h spec), never a second literal.
_DEFAULT_LOOKBACK_MINUTES = BURN_LOOKBACK_WINDOWS["5h"]["default"]
_FALLBACK_USAGE_REFRESH_MINUTES = 10
_MONTH_FALLBACK_SECONDS = float(WINDOW_SECONDS["month"])
_DAY_SECONDS = 24 * 3600

# A sample is a ``(timestamp, percent)`` or
# ``(timestamp, percent, resets_at)`` tuple; readers tolerate both.
Sample = tuple[Any, ...]


def _is_number(value: object) -> bool:
    """Return True for a real int/float - ``bool`` deliberately excluded."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_utc(value: datetime) -> datetime:
    """Normalise a datetime for comparisons; naive values are treated as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object) -> datetime | None:
    """Parse a ``datetime`` or an ISO-8601 string (``Z`` tolerated) to datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _coerce_now(now: datetime | None) -> datetime:
    """Return ``now`` as a UTC datetime, defaulting to the current instant."""
    if now is None:
        return datetime.now(timezone.utc)
    return _as_utc(now)


def _normalized_reset(value: object) -> datetime | None:
    """Parse and normalise a resets_at value for equality comparisons."""
    parsed = _parse_datetime(value)
    return _as_utc(parsed) if parsed is not None else None


def default_lookback_minutes(window: str) -> int:
    """Return the configured default lookback (minutes) for one window.

    Unknown windows fall back to the 5h default (120 min) so callers always
    get a usable, non-None value.
    """
    spec = BURN_LOOKBACK_WINDOWS.get(window)
    if spec is None:
        return _DEFAULT_LOOKBACK_MINUTES
    return int(spec["default"])


def normalize_lookback_minutes(window: str, value: object) -> int:
    """Coerce an option/entity value into a valid lookback for ``window``.

    ``int(value)`` is used when possible (numeric strings and floats included);
    an unknown window yields 120 minutes, an unconvertible value the window
    default. The result is always clamped to the spec min..max range. ``bool``
    is treated as invalid: it is never a meaningful minute count (same rule as
    the numeric check in ``compute_forecast``).
    """
    spec = BURN_LOOKBACK_WINDOWS.get(window)
    if spec is None:
        return _DEFAULT_LOOKBACK_MINUTES

    default = int(spec["default"])
    candidate: int | None = None
    if not isinstance(value, bool):
        try:
            candidate = int(value)  # type: ignore[call-overload]
        except (TypeError, ValueError, OverflowError):
            candidate = None
    if candidate is None:
        candidate = default

    minimum = int(spec["min"])
    maximum = int(spec["max"])
    return max(minimum, min(maximum, candidate))


def lookback_seconds(window: str, minutes: int) -> int:
    """Return the effective lookback length in seconds.

    Normalises first, so an out-of-range value still yields the lookback the
    coordinator would actually use.
    """
    return normalize_lookback_minutes(window, minutes) * 60


def retention_seconds(window: str | None = None) -> int:
    """Return the sample retention horizon in seconds (D9).

    Retention is per window and always the window's slider MAXIMUM (not the
    current lookback), so raising a lookback takes effect immediately:
    ``5h`` -> 18000 s, ``week``/``month`` -> 604800 s. An unknown or omitted
    ``window`` returns the global maximum (the largest slider maximum,
    :data:`BURN_LOOKBACK_RETENTION_SECONDS`).
    """
    spec = BURN_LOOKBACK_WINDOWS.get(window) if window is not None else None
    if spec is not None:
        return spec["max"] * 60
    return BURN_LOOKBACK_RETENTION_SECONDS


def sample_timestamp(sample: object) -> datetime | None:
    """Return a sample's timestamp (first element), tolerating 2-/3-tuples."""
    try:
        value = sample[0]  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None
    return _parse_datetime(value)


def sample_percent(sample: object) -> float | None:
    """Return a sample's numeric percent (second element), else ``None``."""
    try:
        value = sample[1]  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None
    if not _is_number(value):
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def sample_resets_at(sample: object) -> datetime | None:
    """Return a sample's resets_at (third element); ``None`` for 2-tuples."""
    try:
        if len(sample) < 3:  # type: ignore[arg-type]
            return None
        value = sample[2]  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return None
    return _parse_datetime(value)


def prune_samples(
    samples: list[Sample] | None,
    now: datetime,
    retention: int | None = None,
    cap: int | None = None,
) -> list[Sample]:
    """Drop too-old samples, then keep only the newest ``cap`` entries.

    ``retention`` defaults to :data:`BURN_LOOKBACK_RETENTION_SECONDS` (the
    largest slider maximum, so raising a lookback works immediately) and
    ``cap`` to :data:`BURN_SAMPLE_CAP`. Samples without a readable timestamp
    cannot be age-checked and are dropped. Order is preserved; "newest" means
    the tail of the (chronologically appended) history.
    """
    if not samples:
        return []

    now_utc = _coerce_now(now)
    horizon = (
        BURN_LOOKBACK_RETENTION_SECONDS if retention is None else int(retention)
    )
    limit = BURN_SAMPLE_CAP if cap is None else int(cap)

    cutoff = now_utc - timedelta(seconds=max(horizon, 0))
    kept: list[Sample] = []
    for sample in samples:
        timestamp = sample_timestamp(sample)
        if timestamp is None:
            continue
        if _as_utc(timestamp) >= cutoff:
            kept.append(sample)

    if limit <= 0:
        return []
    return kept[-limit:]


def is_window_reset(
    prev_percent: object,
    prev_resets_at: object,
    percent: object,
    resets_at: object,
) -> bool:
    """Return True when the window rolled over between two samples (D10).

    A reset is either a percent DROP for the same window or a change of the
    ``resets_at`` value (a rollover can keep the same percent, e.g. 100 -> 100
    in a fresh window). Without a previous percent there is no history to
    clear, so ``False``.
    """
    if not _is_number(prev_percent):
        return False
    if _is_number(percent) and percent < prev_percent:  # type: ignore[operator]
        return True
    return _normalized_reset(prev_resets_at) != _normalized_reset(resets_at)


def ols_slope_per_hour(
    samples: list[Sample] | None,
    now: datetime,
    lookback_minutes: int,
    min_span_seconds: int = BURN_RATE_MIN_SPAN_SECONDS,
    min_samples: int = BURN_RATE_MIN_SAMPLES,
) -> float | None:
    """Return the OLS burn-rate slope in %/h over the configured lookback.

    Ordinary least squares WITH intercept over x = seconds relative to the
    first in-window sample, y = percent. Requires at least ``min_samples``
    usable points (always at least 2) spanning at least ``min_span_seconds``;
    a zero denominator or a negative slope yields ``None`` (a drop is a window
    reset, not a rate). The result is rounded to two decimals.
    """
    if lookback_minutes is None:
        return None
    try:
        horizon = float(lookback_minutes) * 60.0
    except (TypeError, ValueError):
        return None
    if horizon < 0:
        return None

    now_utc = _coerce_now(now)
    cutoff = now_utc - timedelta(seconds=horizon)

    points: list[tuple[datetime, float]] = []
    for sample in samples or []:
        timestamp = sample_timestamp(sample)
        percent = sample_percent(sample)
        if timestamp is None or percent is None:
            continue
        timestamp_utc = _as_utc(timestamp)
        if timestamp_utc >= cutoff:
            points.append((timestamp_utc, percent))

    required = max(int(min_samples), 2)
    if len(points) < required:
        return None

    points.sort(key=lambda point: point[0])
    first_timestamp = points[0][0]
    last_timestamp = points[-1][0]
    span = (last_timestamp - first_timestamp).total_seconds()
    if span < min_span_seconds:
        return None

    xs = [(timestamp - first_timestamp).total_seconds()
          for timestamp, _ in points]
    ys = [percent for _, percent in points]
    count = len(points)

    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_xx = sum(x * x for x in xs)

    denominator = count * sum_xx - sum_x * sum_x
    # Float-safe zero check: an exact ``== 0`` comparison misses near-zero
    # denominators that would explode the slope (NIT 4).
    if abs(denominator) < 1e-9:
        return None

    slope_per_second = (count * sum_xy - sum_x * sum_y) / denominator
    slope_per_hour = slope_per_second * 3600.0
    if slope_per_hour < 0:
        return None
    return round(slope_per_hour, 2)


def lookback_too_short(minutes: int, usage_refresh_minutes: int) -> bool:
    """Return True when the lookback cannot hold two useful polls (D24).

    ``lookback < max(2 * usage_refresh_minutes, 5 min)``. A missing/invalid
    refresh interval falls back to 10 minutes; a non-numeric lookback counts as
    too short (no usable history).
    """
    if _is_number(usage_refresh_minutes):
        refresh = float(usage_refresh_minutes)  # type: ignore[arg-type]
    else:
        refresh = float(_FALLBACK_USAGE_REFRESH_MINUTES)

    threshold = max(
        LOOKBACK_TOO_SHORT_REFRESH_FACTOR * refresh,
        LOOKBACK_TOO_SHORT_MIN_MINUTES,
    )
    if not _is_number(minutes):
        return True
    return float(minutes) < threshold  # type: ignore[arg-type]


def calendar_window_seconds(window: str, resets_at: object) -> float | None:
    """Return the real window length in seconds, ``None`` for unknown windows.

    ``5h``/``week`` are fixed (nominal ``WINDOW_SECONDS``). For ``month`` the
    length is ``period_end - period_start`` where ``period_start`` is
    ``resets_at`` minus ONE calendar month, its day clamped to the target
    month's length via ``calendar.monthrange`` (plan §3.3). That yields the
    real 28/29/30/31-day length without a first-of-month special case:
    ``2026-11-01`` - 1 month = ``2026-10-01`` = 31 days.

    Unparseable values and implausible lengths (outside 20..40 days) fall back
    to the nominal ``WINDOW_SECONDS["month"]`` (30 days).
    """
    if window == "5h":
        return float(WINDOW_SECONDS["5h"])
    if window == "week":
        return float(WINDOW_SECONDS["week"])
    if window != "month":
        return None

    parsed = _parse_datetime(resets_at)
    if parsed is None:
        return _MONTH_FALLBACK_SECONDS

    if parsed.month == 1:
        year, month = parsed.year - 1, 12
    else:
        year, month = parsed.year, parsed.month - 1
    day = min(parsed.day, calendar.monthrange(year, month)[1])
    period_start = parsed.replace(year=year, month=month, day=day)

    seconds = float((parsed - period_start).total_seconds())
    if not 20 * _DAY_SECONDS <= seconds <= 40 * _DAY_SECONDS:
        return _MONTH_FALLBACK_SECONDS
    return seconds


def compute_forecast(
    window: str,
    percent: object,
    resets_at: object,
    now: datetime,
    burn_rate: float | None,
) -> dict[str, Any]:
    """Return the forecast block of one workspace window.

    Keys are exactly ``forecast``, ``forecast_recent``, ``forecast_method``,
    ``elapsed_fraction``, ``window_seconds`` and ``remaining_seconds``.

    Method selection (rules applied in order, plan D11/D23):

    1. non-numeric percent -> ``unavailable`` (nominal window length reported);
    2. missing/unparseable ``resets_at`` -> ``unavailable`` (never a fake value);
    3. ``percent == 0`` -> ``zero`` (both forecasts ``0.0``);
    4. before ``t_min = max(15 min, 1 % of T)`` -> ``recent`` using the measured
       burn rate; without a burn rate -> ``unavailable``;
    5. from ``t_min`` on -> ``pace`` (``percent * T / elapsed``).

    The method depends only on the window length ``T`` (``t_min`` is
    ``max(15 min, 1 % of T)``), never on the poll interval (D11).
    """
    nominal = WINDOW_SECONDS.get(window)
    nominal_seconds = float(nominal) if nominal is not None else None

    if not _is_number(percent):
        return {
            "forecast": None,
            "forecast_recent": None,
            "forecast_method": "unavailable",
            "elapsed_fraction": None,
            "window_seconds": nominal_seconds,
            "remaining_seconds": None,
        }

    parsed_reset = _parse_datetime(resets_at)
    if parsed_reset is None:
        return {
            "forecast": None,
            "forecast_recent": None,
            "forecast_method": "unavailable",
            "elapsed_fraction": None,
            "window_seconds": None,
            "remaining_seconds": None,
        }

    now_utc = _coerce_now(now)
    reset_utc = _as_utc(parsed_reset)
    window_seconds = calendar_window_seconds(window, parsed_reset)
    if window_seconds is None or window_seconds <= 0:
        return {
            "forecast": None,
            "forecast_recent": None,
            "forecast_method": "unavailable",
            "elapsed_fraction": None,
            "window_seconds": None,
            "remaining_seconds": None,
        }

    remaining_seconds = max((reset_utc - now_utc).total_seconds(), 0.0)
    elapsed_seconds = min(
        max(window_seconds - remaining_seconds, 0.0), window_seconds
    )
    elapsed_fraction = elapsed_seconds / window_seconds

    percent_value = float(percent)  # type: ignore[arg-type]
    numeric_burn = burn_rate if _is_number(burn_rate) else None
    forecast_recent = (
        round(percent_value + float(numeric_burn) * remaining_seconds / 3600.0, 1)
        if numeric_burn is not None
        else None
    )

    if percent_value == 0.0:
        return {
            "forecast": 0.0,
            "forecast_recent": 0.0,
            "forecast_method": "zero",
            "elapsed_fraction": elapsed_fraction,
            "window_seconds": window_seconds,
            "remaining_seconds": remaining_seconds,
        }

    minimum_elapsed = max(
        float(MIN_FORECAST_ELAPSED_SECONDS),
        MIN_FORECAST_ELAPSED_FRACTION * window_seconds,
    )
    if elapsed_seconds < minimum_elapsed:
        if forecast_recent is None:
            return {
                "forecast": None,
                "forecast_recent": None,
                "forecast_method": "unavailable",
                "elapsed_fraction": elapsed_fraction,
                "window_seconds": window_seconds,
                "remaining_seconds": remaining_seconds,
            }
        return {
            "forecast": forecast_recent,
            "forecast_recent": forecast_recent,
            "forecast_method": "recent",
            "elapsed_fraction": elapsed_fraction,
            "window_seconds": window_seconds,
            "remaining_seconds": remaining_seconds,
        }

    return {
        "forecast": round(percent_value * window_seconds / elapsed_seconds, 1),
        "forecast_recent": forecast_recent,
        "forecast_method": "pace",
        "elapsed_fraction": elapsed_fraction,
        "window_seconds": window_seconds,
        "remaining_seconds": remaining_seconds,
    }
