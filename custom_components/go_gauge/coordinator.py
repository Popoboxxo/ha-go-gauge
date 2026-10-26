"""Go Gauge HA - Data coordinator talking DIRECTLY to opencode.ai.

Since 2.0.0 this coordinator owns exactly ONE refresh cycle: the per-workspace
usage windows (default every 10 min, switchable in the options). The model
catalog moved to the workspace-independent ``AccountCoordinator``
(``account.py``) - see ``docs/PLAN-account-entry-2.0.0.md`` §2.7/§2.9.

Every cycle that performs a fetch also records its own evidence timestamps
(``last_usage_attempt`` / ``last_usage_ok`` / ``last_usage_outcome``) so the
account-level reachability sensor can resolve the API state without guessing from
``last_update_success`` (plan §5). A publish tick without a due fetch leaves the
attempt timestamp untouched, so stale evidence ages out via its TTL.

Cloudflare note: plain urllib/requests get blocked (HTTP 403 Error 1010);
aiohttp WITH full browser headers succeeds (verified live 2026-08-22).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import burn_lookback as bl
from .api import OpenCodeGoApiClient
from .const import (
    BURN_LOOKBACK_WINDOWS,
    BURN_RATE_MIN_SPAN_SECONDS,
    BURN_SAMPLE_CAP,
    CONF_AUTO_UPDATE_USAGE,
    CONF_USAGE_REFRESH_MINUTES,
    DEFAULT_USAGE_REFRESH_MINUTES,
    DOMAIN,
    WINDOW_SECONDS,
    GoGaugeAuthError,
)

_LOGGER = logging.getLogger(__name__)

# Burn-rate lookback window: only the last 2h of usage samples feed the
# %/h slope. A full hour-plus of history keeps the slope stable across the
# short 5h rolling window while still ignoring long-past consumption.
BURN_RATE_LOOKBACK_SECONDS = 2 * 3600


def _unavailable_forecast(window: str) -> dict[str, Any]:
    """Return the "no fake number" forecast block of one window (D23).

    ``window_seconds`` stays the window's nominal length so consumers can still
    render the timeframe; every derived value is ``None``.
    """
    nominal = WINDOW_SECONDS.get(window)
    return {
        "forecast": None,
        "forecast_recent": None,
        "forecast_method": "unavailable",
        "elapsed_fraction": None,
        "window_seconds": float(nominal) if nominal is not None else None,
        "remaining_seconds": None,
    }


def window_elapsed_fraction(win: str, resets_at: datetime | None,
                             now: datetime | None = None) -> float | None:
    """Fraction of the window elapsed so far, 0 < f <= 1; None if unknown.

    None right after a reset (elapsed <= 0) - dividing by a near-zero
    fraction would blow the forecast up into meaningless numbers.

    .. deprecated::
        Kept for backwards compatibility; no production caller since 2.1.0.
        New code uses ``burn_lookback.compute_forecast``.
    """
    if resets_at is None:
        return None
    total = WINDOW_SECONDS.get(win)
    if not total:
        return None
    now = now or datetime.now(timezone.utc)
    remaining = (resets_at - now).total_seconds()
    elapsed = total - remaining
    if elapsed <= 0:
        return None
    return min(elapsed / total, 1.0)


def forecast_percent(ws: dict[str, Any] | None, win: str,
                      now: datetime | None = None) -> float | None:
    """Linear pace projection: current percent extrapolated to window end.

    .. deprecated::
        Kept for backwards compatibility; no production caller since 2.1.0.
        New code uses ``burn_lookback.compute_forecast`` (calendar-month aware
        and able to fall back to the recent burn rate).
    """
    if not ws or ws.get("status") in ("no_subscription", "error"):
        return None
    blk = (ws.get("windows") or {}).get(win) or {}
    pct = blk.get("percent")
    if not isinstance(pct, (int, float)):
        return None
    frac = window_elapsed_fraction(win, blk.get("resets_at"), now)
    if not frac:
        return None
    return round(pct / frac, 1)


def pace_status(forecast_pct: float, green_below: float, red_above: float) -> str:
    """green/yellow/red vs. the two configurable thresholds.

    Clamp red_above >= green_below defensively - a misconfigured Number
    entity (red below green) just collapses the yellow band instead of
    inverting green/red.
    """
    red_above = max(red_above, green_below)
    if forecast_pct < green_below:
        return "green"
    if forecast_pct > red_above:
        return "red"
    return "yellow"


def remaining_percent(ws: dict[str, Any] | None, win: str) -> float | None:
    """Restbudget of one workspace window: 100 - used percent.

    None for a missing workspace, a no_subscription/error workspace and a
    non-numeric percent - same "None instead of a fake number" pattern as
    UsagePercentSensor (a MEASUREMENT/%-sensor must never carry a string).
    """
    if not ws or ws.get("status") in ("no_subscription", "error"):
        return None
    blk = (ws.get("windows") or {}).get(win) or {}
    pct = blk.get("percent")
    if not isinstance(pct, (int, float)):
        return None
    return 100.0 - float(pct)


def seconds_until_reset(ws: dict[str, Any] | None, win: str,
                        now: datetime | None = None) -> float | None:
    """Seconds until the window resets, clamped at 0; None if unknown.

    A stale resets_at in the past yields 0.0, never a negative duration.
    """
    if not ws:
        return None
    blk = (ws.get("windows") or {}).get(win)
    if not blk:
        return None
    resets_at = blk.get("resets_at")
    if not isinstance(resets_at, datetime):
        return None
    now = now or datetime.now(timezone.utc)
    return max((resets_at - now).total_seconds(), 0.0)


def burn_rate_per_hour(samples: list[tuple[Any, ...]] | None,
                       now: datetime | None = None) -> float | None:
    """Consumption slope in %/h over the recent sample history.

    - Only samples within BURN_RATE_LOOKBACK_SECONDS of `now` are used.
    - Needs at least two samples spanning BURN_RATE_MIN_SPAN_SECONDS.
    - A negative slope means a window reset happened between samples and is
      not a "rate" - returns None (the history recorder clears on reset, so
      this is only a defensive fallback).
    - Tolerates both 2-tuple and 3-tuple samples (``ts, pct = sample[0],
      sample[1]``); the third element (``resets_at``) is ignored here.

    .. deprecated::
        Kept for backwards compatibility; no production caller since 2.1.0.
        New code uses ``GoGaugeCoordinator.burn_rate`` (OLS with intercept over
        the configurable lookback).
    """
    if not samples or len(samples) < 2:
        return None
    now = now or datetime.now(timezone.utc)
    recent: list[tuple[datetime, float]] = []
    for sample in samples:
        ts, pct = sample[0], sample[1]
        if (now - ts).total_seconds() <= BURN_RATE_LOOKBACK_SECONDS:
            recent.append((ts, pct))
    if len(recent) < 2:
        return None
    first_ts, first_pct = recent[0]
    last_ts, last_pct = recent[-1]
    span = (last_ts - first_ts).total_seconds()
    if span < BURN_RATE_MIN_SPAN_SECONDS:
        return None
    rate = (last_pct - first_pct) / (span / 3600.0)
    if rate < 0:
        return None
    return round(rate, 2)


def _parse_reset(resets_at: str | None) -> datetime | None:
    if not resets_at:
        return None
    try:
        dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


class GoGaugeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Usage-only coordinator for one workspace.

    ``auto_update_usage`` off => ``update_interval`` is ``None``: no scheduled
    polling at all, only the refresh button (or a manual service call) triggers
    an update (plan §11.1 R9 - deliberately no 24h fallback anymore).
    """

    # Set by the config-entry setup after construction (workspace display name).
    # Declared here so platform entities that read it type-check against
    # GoGaugeCoordinator instead of the HA base type.
    ws_name: str
    # Explicit type: the auth path assigns ``self.data`` from a snapshot that is
    # derived from a previous ``self.data`` read (circular inference otherwise:
    # "Cannot determine type of data"). The HA base class is skipped by mypy
    # (homeassistant not installed), so without this declaration the attribute
    # type would be inferred from that single self-referential assignment.
    data: dict[str, Any]

    def __init__(self, hass: HomeAssistant, tokens: list[str],
                 options: dict[str, Any] | None = None) -> None:
        self.hass = hass
        self._tokens = tokens
        self._client = OpenCodeGoApiClient(async_get_clientsession(hass))
        options = options or {}

        self.auto_usage = options.get(CONF_AUTO_UPDATE_USAGE, True)
        self.usage_minutes = int(
            options.get(CONF_USAGE_REFRESH_MINUTES, DEFAULT_USAGE_REFRESH_MINUTES))
        # Per-window burn-rate lookback (2.1.0): parsed/normalised once here,
        # then kept live by ``set_burn_lookback`` (Number entities + options
        # flow). Keys are the window names "5h"/"week"/"month".
        self.burn_lookback_minutes: dict[str, int] = {
            win: bl.normalize_lookback_minutes(win, options.get(spec["option"]))
            for win, spec in BURN_LOOKBACK_WINDOWS.items()
        }
        # Warnschwelle + Ampel-Rot-Grenze als Runtime-Attribute (Number-Entities
        # schreiben sie live)
        from .const import (  # local import: no cycle at import time
            CONF_PACE_RED_PERCENT,
            DEFAULT_PACE_RED_PERCENT,
            DEFAULT_WARN_PERCENT,
        )
        self.warn_percent = int(options.get("warn_percent", DEFAULT_WARN_PERCENT))
        self.pace_red_percent = int(
            options.get(CONF_PACE_RED_PERCENT, DEFAULT_PACE_RED_PERCENT))
        # Flag: Runtime-Entities persistieren Optionen ohne Entry-Reload
        self._skip_reload = False

        # Usage-only interval: the account entry owns the catalog cycle and its
        # own publish tick. Auto-update off -> no scheduled polling at all
        # (``None``), never a fake 24h interval (plan §11.1 R9).
        interval = timedelta(minutes=self.usage_minutes) if self.auto_usage else None

        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=interval)
        # Zeitstempel der letzten echten Abrufe je Zyklus
        self.last_usage_fetch: datetime | None = None
        # Evidence of the last usage cycle (plan §5): ``attempt`` is written on
        # every run that actually attempts a fetch (not on a bare publish tick),
        # ``ok`` only on a successful fetch, ``outcome`` is one of
        # "ok"/"neutral"/"fail" (see ``_async_update_data``).
        self.last_usage_attempt: datetime | None = None
        self.last_usage_ok: datetime | None = None
        self.last_usage_outcome: str | None = None
        # Usage-Verlauf je Workspace-Fenster ("ws1:5h") ->
        # [(ts, percent, resets_at|None)]. Das dritte Element ist das
        # ``resets_at`` des Samples (Reset-Erkennung D10); ältere 2-Tupel
        # bleiben lesbar, weil alle Leser beide Formen tolerieren.
        self._usage_samples: dict[
            str, list[tuple[datetime, float, datetime | None]]
        ] = {}

    def recalculate_interval(self) -> None:
        """Intervall nach Auto-Update-Schalter/Minuten NEU setzen (live).

        Der Setter von ``update_interval`` speichert nur den neuen Wert; ein
        bereits laufender Refresh-Timer laeuft sonst mit dem ALTEN Intervall
        weiter. ``_schedule_refresh()`` meldet den scharfen Timer ab und plant
        den naechsten Abruf mit dem neuen Intervall.

        Usage-only: the catalog cycle lives on the account entry, so this
        coordinator never schedules a models fetch (2.0.0, plan §2.7).
        """
        interval = timedelta(minutes=self.usage_minutes) if self.auto_usage else None
        self.update_interval = interval
        self._schedule_refresh()
        _LOGGER.info("Go Gauge: Update-Intervall -> %s (usage=%s/%s min)",
                     interval, self.auto_usage, self.usage_minutes)

    def set_burn_lookback(self, window: str, minutes: int) -> None:
        """Set the live burn-rate lookback of one window (normalised).

        The Number entities persist the option separately; this method only
        updates the coordinator state that ``burn_rate``/diagnostics read.
        """
        self.burn_lookback_minutes[window] = bl.normalize_lookback_minutes(
            window, minutes
        )

    def burn_rate(self, key: str, window: str,
                  now: datetime | None = None) -> float | None:
        """OLS burn rate (%/h) of one workspace window over the lookback.

        Defensive attribute access: lightweight test doubles may bind this
        method without running ``GoGaugeCoordinator.__init__``.
        """
        now = now or datetime.now(timezone.utc)
        store = getattr(self, "_usage_samples", None) or {}
        samples = store.get(f"{key}:{window}", [])
        lookbacks = getattr(self, "burn_lookback_minutes", None) or {}
        lookback = lookbacks.get(window, bl.default_lookback_minutes(window))
        return bl.ols_slope_per_hour(samples, now, lookback)

    def forecast_details(self, key: str, window: str,
                         now: datetime | None = None) -> dict[str, Any]:
        """Return the forecast block of one workspace window (D11/D23).

        Reads ``percent``/``resets_at`` from the published snapshot, feeds the
        coordinator's current OLS burn rate into ``burn_lookback.compute_forecast``
        and tolerates a missing workspace/window (then ``unavailable``).

        A workspace whose ``status`` is ``"no_subscription"`` or ``"error"``
        short-circuits to the unavailable block: no derived number may be
        published for a workspace that has no usable usage data (the same
        "no fake number" semantics the legacy ``forecast_percent`` had).
        """
        now = now or datetime.now(timezone.utc)
        workspaces = (getattr(self, "data", None) or {}).get("workspaces") or []
        workspace = next(
            (ws for ws in workspaces if ws.get("key") == key), None
        )
        if workspace is not None and workspace.get("status") in (
            "no_subscription", "error"
        ):
            return _unavailable_forecast(window)
        block = ((workspace or {}).get("windows") or {}).get(window) or {}
        return bl.compute_forecast(
            window,
            block.get("percent"),
            block.get("resets_at"),
            now,
            self.burn_rate(key, window, now),
        )

    def burn_rate_diagnostics(self, key: str, window: str,
                              now: datetime | None = None) -> dict[str, Any]:
        """Return the debug block behind the burn-rate sensor (D14/D21).

        ``samples`` counts only the samples inside the CONFIGURED lookback (not
        the full retention horizon), so it explains a ``None`` burn rate.
        """
        now = now or datetime.now(timezone.utc)
        lookbacks = getattr(self, "burn_lookback_minutes", None) or {}
        lookback = lookbacks.get(window, bl.default_lookback_minutes(window))
        usage_minutes = getattr(
            self, "usage_minutes", DEFAULT_USAGE_REFRESH_MINUTES
        )
        store = getattr(self, "_usage_samples", None) or {}
        samples = store.get(f"{key}:{window}", [])
        count = len(bl.prune_samples(
            samples,
            now,
            retention=bl.lookback_seconds(window, lookback),
            cap=BURN_SAMPLE_CAP,
        ))
        return {
            "lookback_minutes": lookback,
            "samples": count,
            "lookback_seconds": bl.lookback_seconds(window, lookback),
            "lookback_too_short": bl.lookback_too_short(
                lookback, usage_minutes
            ),
        }

    def _record_usage_sample(self, key: str, win: str, percent: float,
                             now: datetime,
                             resets_at: datetime | None = None) -> None:
        """Append a (timestamp, percent, resets_at) sample for the burn rate.

        Window-reset detection (D10): a percent DROP or a CHANGED ``resets_at``
        means the window rolled over, so pre-reset history is cleared first
        instead of leaving a bogus negative slope behind. The history is always
        pruned to the WINDOW's maximum retention horizon (not the current
        lookback) so raising a lookback works immediately (D9), then capped
        (newest kept). Retention is per window: 5h keeps 18000 s, week/month
        604800 s (plan §9 item 3).

        Existing 2-tuple samples from an older runtime stay readable; new
        samples are 3-tuples. Defensive `getattr`: lightweight test doubles
        bind `_async_update_data` without running ``GoGaugeCoordinator.__init__``,
        so `_usage_samples` may not exist yet.
        """
        store = getattr(self, "_usage_samples", None)
        if store is None:
            store = {}
            self._usage_samples = store
        samples = store.setdefault(f"{key}:{win}", [])

        reset_value = bl.sample_resets_at((now, float(percent), resets_at))
        previous = samples[-1] if samples else None
        if previous is not None and bl.is_window_reset(
            bl.sample_percent(previous),
            bl.sample_resets_at(previous),
            float(percent),
            reset_value,
        ):
            samples.clear()

        samples.append((now, float(percent), reset_value))
        samples[:] = bl.prune_samples(
            samples,
            now,
            retention=bl.retention_seconds(win),
            cap=BURN_SAMPLE_CAP,
        )

    def _snapshot(
        self,
        now: datetime,
        usage: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the coordinator snapshot returned by ``_async_update_data``.

        Shared by the normal return path and the auth-failure path so the
        published shape (keys, timestamps, toggles) stays identical. The auth
        path uses it to publish a snapshot that marks the affected workspace
        with ``status="auth_error"`` BEFORE raising ``ConfigEntryAuthFailed``.

        The key set is exact (plan §2.7/M3): the model catalog lives on the
        account coordinator, so no catalog key appears here anymore.
        """
        return {
            "fetched_at": now.isoformat(),
            "last_usage_fetch": (
                self.last_usage_fetch.isoformat() if self.last_usage_fetch else None
            ),
            "last_usage_ok": (
                self.last_usage_ok.isoformat() if self.last_usage_ok else None
            ),
            "last_usage_attempt": (
                self.last_usage_attempt.isoformat()
                if self.last_usage_attempt
                else None
            ),
            "last_usage_outcome": self.last_usage_outcome,
            "auto_usage": self.auto_usage,
            "usage_refresh_minutes": self.usage_minutes,
            "workspaces": usage,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)

        # Evidence bookkeeping (plan §5): ``last_usage_attempt`` names a REAL
        # fetch attempt, not a bare publish tick. It is therefore written inside
        # the ``usage_due`` block below: a tick without a due fetch must let the
        # timestamp AGE so the reachability TTL expires correctly (review fix
        # M3 - an old outcome must not look fresh). The outcome/ok timestamps are
        # only touched when a fetch actually happened (see the cycle end below).

        # --- Usage-Zyklus -----------------------------------------------------
        # WICHTIG: Bei Fehlern NICHT die alten Daten verwerfen (kein Unbekannt!),
        # sondern letzten Stand behalten und Fehler nur als Attribut melden.
        prev_ws = {w["key"]: w for w in ((self.data or {}).get("workspaces") or [])}
        usage = list((self.data or {}).get("workspaces") or [])
        usage_due = (
            self._tokens
            and (self.auto_usage
                 or not usage  # erster Start: immer laden
                 or self.last_usage_fetch is None)
            and (self.last_usage_fetch is None
                 or now - self.last_usage_fetch >= timedelta(minutes=self.usage_minutes))
        )
        ok_any = False
        if usage_due:
            # A fetch is actually attempted -> this is the evidence timestamp.
            self.last_usage_attempt = now
            fresh = []
            # Sammelt fehlende erwartete Felder ueber alle Workspaces dieses
            # Update-Zyklus, damit nur EINE aggregierte Warnung geloggt wird
            # (kein Log-Spam pro Workspace/Fenster).
            missing_fields: set[str] = set()
            for i, token in enumerate(self._tokens, start=1):
                key = f"ws{i}"
                old = prev_ws.get(key, {})
                entry: dict[str, Any] = {"key": key, "token_slot": i,
                                         "status": "ok", "windows": {}}
                try:
                    res = await self._client.fetch_usage(token)
                    # Audit vom 2026-09-04: Ein Feldnamen-Mismatch zwischen der
                    # opencode.ai-API-Response und diesem Parser wird als
                    # moeglicher Root-Cause-Kandidat fuer leere/falsche
                    # Sensordaten vermutet - NICHT gegen die Live-API
                    # verifiziert. Diese Pruefung macht ein fehlendes
                    # erwartetes Feld ueber eine Log-Warnung SICHTBAR statt es
                    # wie bisher lautlos per dict.get() als None durchzureichen;
                    # das bestehende Fallback-Verhalten (None/alter Wert)
                    # bleibt dabei unveraendert. Unsere eigenen synthetischen
                    # 403-Antworten (status in no_subscription/error, siehe
                    # fetch_usage) haben absichtlich ein leeres "usage": {} und
                    # zaehlen daher nicht als Schema-Drift.
                    #
                    # KEIN "status" als Pflichtfeld: die reale Erfolgsantwort
                    # ist {"usage": {...}} ohne Top-Level-status; der Parser
                    # defaultet bereits auf "ok" (siehe oben), und status wird
                    # hier nur optional uebernommen. Eine Pflichtpruefung wuerde
                    # bei JEDEM erfolgreichen Abruf einen Falsch-Positiv-Alarm
                    # erzeugen (Audit 2026-09-13, RC-4).
                    is_synthetic_error = res.get("status") in ("no_subscription", "error")
                    if not is_synthetic_error and "usage" not in res:
                        missing_fields.add("usage")
                    api = res.get("usage") or {}
                    for api_key, win in (
                        ("rolling", "5h"),
                        ("weekly", "week"),
                        ("monthly", "month"),
                    ):
                        if not is_synthetic_error and api_key not in api:
                            missing_fields.add(f"usage.{api_key}")
                        blk = api.get(api_key) or {}
                        if not is_synthetic_error and api_key in api and "resetsAt" not in blk:
                            missing_fields.add(f"usage.{api_key}.resetsAt")
                        entry["windows"][win] = {
                            "percent": blk.get("percent"),
                            "status": blk.get("status"),
                            "resets_at": _parse_reset(blk.get("resetsAt")),
                        }
                    if res.get("status"):
                        entry["status"] = res["status"]
                    if res.get("note"):
                        entry["note"] = res["note"]
                    if entry["status"] == "ok":
                        ok_any = True
                        # Nur ein ERFOLGREICHER Abruf erzeugt einen Sample -
                        # im Except-Pfad (alter Stand beibehalten) NICHT.
                        for win, blk in entry["windows"].items():
                            pct = blk.get("percent")
                            if isinstance(pct, (int, float)):
                                self._record_usage_sample(
                                    key, win, float(pct), now,
                                    blk.get("resets_at"),
                                )
                except GoGaugeAuthError as err:
                    # Token ungueltig/abgelaufen (HTTP 401): NICHT als
                    # transienten Fehler behandeln. Workspace strukturiert als
                    # "auth_error" markieren, den Snapshot VOR dem Raise
                    # publizieren (HA setzt self.data nur bei normalem Return -
                    # ohne diesen Schritt saehe der ApiStatusSensor stale/
                    # unknown) und dann ConfigEntryAuthFailed werfen, damit HA
                    # die Reauth-UI startet. Gezielt VOR dem generischen
                    # except Exception, damit der 401 nicht dort landet.
                    entry["status"] = "auth_error"
                    # The GoGaugeAuthError message already carries the readable,
                    # non-secret cause ("HTTP 401 - token invalid/expired").
                    # Do NOT prefix it again - that produced the duplicated
                    # "HTTP 401 - token invalid/expired: HTTP 401 - token
                    # invalid/expired" note. Fall back to that same phrase only
                    # if a future raise ever carries an empty message.
                    entry["note"] = str(err) or "HTTP 401 - token invalid/expired"
                    fresh.append(entry)
                    # 401 proves the API ANSWERED but not that it delivered
                    # usage data: "neutral" (plan §5), never "fail". The attempt
                    # timestamp was already written when the fetch became due
                    # (review fix M3 - no second write here); the snapshot is
                    # published before the raise so the status sensors can name
                    # the cause (unchanged behaviour).
                    self.last_usage_outcome = "neutral"
                    self.data = self._snapshot(now, fresh)
                    self.last_update_success = False
                    raise ConfigEntryAuthFailed(
                        "opencode.ai rejected the API token (HTTP 401)") from err
                except Exception as err:  # noqa: BLE001
                    # TRANSIENTER Fehler (Netz/Cloudflare): LETZTEN STAND BEHALTEN
                    entry = dict(old) if old else entry
                    entry["status"] = old.get("status", "error") if old else "error"
                    # Hinweis: KEIN Zeitstempel hier - "fetched_at" wird erst
                    # unten (nach diesem Try/Except-Block) pro Workspace
                    # gesetzt, ist an dieser Stelle also fuer "old" nicht
                    # zuverlaessig verfuegbar (Audit 2026-09-04).
                    entry["note"] = (
                        f"Abruf fehlgeschlagen, letzter bekannter Stand "
                        f"beibehalten: {err}"
                    )
                    _LOGGER.warning("Go Gauge %s: Abruf fehlgeschlagen (%s) - behalte alten Stand",
                                    key, err)
                fresh.append(entry)
            if missing_fields:
                _LOGGER.warning(
                    "Go Gauge: erwartete Felder fehlen in der Usage-API-Response "
                    "(%s) - moeglicher Feldnamen-Mismatch, siehe Audit 2026-09-04; "
                    "betroffene Sensoren fallen auf None/alten Wert zurueck",
                    ", ".join(sorted(missing_fields)),
                )
            usage = fresh
            self.last_usage_fetch = now
            for w in usage:
                w["fetched_at"] = now.isoformat()
            if not ok_any and all(
                w.get("status") in ("no_subscription", "error") for w in usage
            ):
                _LOGGER.warning(
                    "Go Gauge: kein Workspace mit aktiven Nutzungsdaten "
                    "(alle no_subscription/error)")

            # Cycle end evidence (plan §5/§8.3): a fresh success is the only
            # thing that refreshes ``last_usage_ok``; an answer without any
            # success (no subscription, 401) is "neutral"; everything else is a
            # failure. No fetch this run -> keep the previous outcome (a plain
            # publish tick must not invent a failure).
            if ok_any:
                self.last_usage_ok = now
                self.last_usage_outcome = "ok"
            elif all(
                w.get("status") in ("no_subscription", "auth_error") for w in usage
            ):
                self.last_usage_outcome = "neutral"
            else:
                self.last_usage_outcome = "fail"

        return self._snapshot(now, usage)
