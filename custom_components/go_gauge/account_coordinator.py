"""Account coordinator runtime (extracted from ``account.py``, plan §4.3).

Workspace-independent catalog/publish runtime (:class:`AccountCoordinator`,
plan §2.4/§6.2/§6.3), its option-seeding/clamping helpers, and
:func:`_domain_data` - the shared ``hass.data[DOMAIN]`` accessor used by all
three account leaf modules (coordinator, reachability, adoption). This module is
the acyclic common home: it depends on neither ``account_reachability`` nor
``account_adoption``, so placing ``_domain_data`` here avoids a circular import
when those two modules need it.

Pure structural extraction - no behaviour change. Signatures, docstrings and
error handling are unchanged from their previous home in ``account.py``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import OpenCodeGoApiClient
from .catalog import build_models_block
from .const import (
    ACCOUNT_BACKOFF_MAX_MINUTES,
    ACCOUNT_MODELS_REFRESH_MAX,
    ACCOUNT_MODELS_REFRESH_MIN,
    ACCOUNT_PUBLISH_TICK_MINUTES,
    CONF_AUTO_UPDATE_MODELS,
    CONF_MODELS_REFRESH_MINUTES,
    DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS,
    DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
    DOMAIN,
    ENTRY_KIND_WORKSPACE,
    conf_entry_kind,
)

_LOGGER = logging.getLogger(__name__)


def _clamp_minutes(minutes: Any) -> int:
    """Clamp a (possibly foreign) minute value into the account's 1-1440 range."""
    try:
        value = int(minutes)
    except (TypeError, ValueError):
        return DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES
    return max(ACCOUNT_MODELS_REFRESH_MIN, min(value, ACCOUNT_MODELS_REFRESH_MAX))


def _isoformat(value: datetime | None) -> str | None:
    """Render a timestamp as an ISO string; ``None`` stays ``None``."""
    return value.isoformat() if value is not None else None


def _workspace_options(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return the option dicts of all workspace entries (defensive).

    Used only to seed the account options from a pre-2.0.0 installation: the
    model settings used to live per workspace and there is no lossless way to
    merge N of them, so the FIRST workspace that carries a key wins (plan §4.7:
    the semantic changes from "per workspace" to "global").
    """
    try:
        entries = list(hass.config_entries.async_entries(DOMAIN))
    except Exception:  # noqa: BLE001 - missing entry list -> plain defaults
        return []
    return [
        dict(getattr(entry, "options", None) or {})
        for entry in entries
        if conf_entry_kind(getattr(entry, "data", None)) == ENTRY_KIND_WORKSPACE
    ]


def _seed_account_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Seed missing account options from a workspace entry - never raises.

    DELIBERATE DEVIATION from plan §4.7 (explicit user decision: "migration
    without data loss"): before 2.0.0 both values lived in the workspace
    options, so a plain reset to defaults would silently drop the user's
    settings on every upgrade. If the account entry does not carry them yet, the
    first workspace value that exists is adopted instead; otherwise the
    documented defaults (ON / 60 min) apply. Runs BEFORE the coordinator is
    constructed, so the very first runtime already sees the migrated settings.
    The account is global by design - N different workspace values cannot all be
    kept - and the seed runs once: afterwards the account entry is the owner.
    """
    try:
        options = dict(getattr(entry, "options", None) or {})
        missing = [
            key
            for key in (CONF_AUTO_UPDATE_MODELS, CONF_MODELS_REFRESH_MINUTES)
            if key not in options
        ]
        if not missing:
            return

        workspace_options = _workspace_options(hass)
        defaults: dict[str, Any] = {
            CONF_AUTO_UPDATE_MODELS: DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS,
            CONF_MODELS_REFRESH_MINUTES: DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
        }
        for key in missing:
            for candidate in workspace_options:
                if key in candidate:
                    options[key] = candidate[key]
                    break
            else:
                options[key] = defaults[key]
        options[CONF_MODELS_REFRESH_MINUTES] = _clamp_minutes(
            options[CONF_MODELS_REFRESH_MINUTES]
        )
        options[CONF_AUTO_UPDATE_MODELS] = bool(options[CONF_AUTO_UPDATE_MODELS])

        hass.config_entries.async_update_entry(entry, options=options)
        _LOGGER.info(
            "Go Gauge: account options seeded (%s=%s, %s=%s)",
            CONF_AUTO_UPDATE_MODELS,
            options[CONF_AUTO_UPDATE_MODELS],
            CONF_MODELS_REFRESH_MINUTES,
            options[CONF_MODELS_REFRESH_MINUTES],
        )
    except Exception as err:  # noqa: BLE001 - seeding is best effort
        _LOGGER.warning(
            "Go Gauge: seeding the account options failed (%s) - the "
            "coordinator falls back to its documented defaults",
            err,
        )


class AccountCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Workspace-independent coordinator for the account entry (plan §2.4).

    TWO clocks, deliberately separated (plan §6.2):

    * ``update_interval`` = :data:`ACCOUNT_PUBLISH_TICK_MINUTES` (5 min). This
      is a pure *publish* tick: ``_async_update_data`` only re-renders the
      snapshot and NEVER performs HTTP. That is what makes a TTL expiry (or a
      changed setting) reach the UI without polling the API (plan §11.1 R7).
    * a separate ``async_track_time_interval`` timer for the actual catalog
      fetch, armed with ``fetch_interval_minutes`` (base interval, extended by
      the exponential backoff of plan §6.3).

    ``async_fetch_now`` is the ONLY HTTP path (fetch timer, refresh button,
    one-time first fetch during the account setup). The coordinator is created
    WITHOUT HA's
    ``config_entry=`` kwarg and never calls ``async_config_entry_first_refresh``
    on purpose: an account fetch failure must not be able to fail the config
    entry setup, and a 401 is not a reauth case here (the account entry carries
    no token, plan §6.3).
    """

    # Explicit annotation: mypy skips the HA base class (not installed in CI),
    # so without this the attribute type would stay ``Any``.
    data: dict[str, Any]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Read the account options and arm nothing yet (setup starts the timer).

        ``entry`` is kept as a plain reference (options persistence only, plan
        §6.4) - no ``config_entry=`` kwarg, no ``self.config_entry`` attribute,
        so nothing here can trigger HA's legacy entry-bound coordinator
        behaviour.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_account",
            update_interval=timedelta(minutes=ACCOUNT_PUBLISH_TICK_MINUTES),
        )
        self.entry = entry
        options = getattr(entry, "options", None) or {}
        self.auto_fetch: bool = bool(
            options.get(
                CONF_AUTO_UPDATE_MODELS, DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS
            )
        )
        self.base_interval_minutes: int = _clamp_minutes(
            options.get(
                CONF_MODELS_REFRESH_MINUTES,
                DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
            )
        )
        # Evidence timestamps/outcome of the catalog fetch (plan §5). They are
        # read by the reachability sensor through the snapshot, never mutated
        # from outside.
        self.last_models_ok: datetime | None = None
        self.last_models_attempt: datetime | None = None
        self.last_models_outcome: str | None = None
        self.last_error: str | None = None
        self._failures: int = 0
        # The catalog block survives a failed fetch on purpose: a transient 403
        # must show the last known catalog plus a "fail" outcome, not an empty
        # catalog.
        self._models_block: dict[str, Any] | None = None
        self._unsub_fetch: Any = None
        # Set by ``async_stop_fetch_timer`` and cleared by
        # ``async_start_fetch_timer``: a fetch that is still in flight when the
        # entry unloads must not re-arm the timer afterwards (M4 - a leaked
        # timer would keep polling after the entry is gone).
        self._stopped: bool = False
        # Serialises the HTTP round trip: the refresh button and the fetch timer
        # can fire at the same time, and two overlapping fetches would
        # double-increment the backoff and publish racing snapshots.
        self._fetch_lock = asyncio.Lock()
        self._client = OpenCodeGoApiClient(async_get_clientsession(hass))

    @property
    def models_block(self) -> dict[str, Any] | None:
        """Last successfully built catalog block (``None`` until the first fetch).

        This property is THE access form for consumers (the sensors,
        diagnostics, one-time first fetch check) - the same object is also
        mirrored into the snapshot under the ``models_block`` key, so entity
        code never has to reach into a private attribute.
        """
        return self._models_block

    @property
    def fetch_interval_minutes(self) -> int | None:
        """Effective fetch interval in minutes; ``None`` when auto-fetch is off."""
        if not self.auto_fetch:
            return None
        return self._effective_fetch_minutes()

    def _effective_fetch_minutes(self) -> int | None:
        """Map base interval + failure count onto the effective fetch interval.

        Exactly plan §6.3: a base interval ABOVE the cap is never shortened
        (``b > 60 -> b``, e.g. 120 stays 120), otherwise the interval doubles
        per consecutive failure and is capped at :data:`ACCOUNT_BACKOFF_MAX_MINUTES`.
        ``None`` when auto-fetch is disabled (no timer at all).
        """
        if not self.auto_fetch:
            return None
        base = self.base_interval_minutes
        if base > ACCOUNT_BACKOFF_MAX_MINUTES:
            return base
        return min(base * (2 ** self._failures), ACCOUNT_BACKOFF_MAX_MINUTES)

    async def _async_update_data(self) -> dict[str, Any]:
        """Publish tick: rebuild the snapshot only - never touch the API.

        Kept ``async`` because ``DataUpdateCoordinator`` requires it; there is
        no ``await`` inside, and adding one (an HTTP call) would break the
        two-clock contract of plan §6.2.
        """
        return self._snapshot(datetime.now(timezone.utc))

    def _snapshot(self, now: datetime) -> dict[str, Any]:
        """Return the published state dict (the coordinator's ``data``).

        A snapshot instead of live attribute reads because HA entities and the
        diagnostics export consume ``coordinator.data``: re-rendering it is what
        makes a TTL expiry visible (publish tick, plan §5/§11.1 R7) and what
        keeps entity attributes consistent within one update. It is written

        * by every publish tick (``_async_update_data``),
        * at the end of every ``async_fetch_now`` (``finally`` - success and
          failure alike, so ``last_models_attempt``/``last_error`` always land),
        * by the two setters (``set_fetch_interval`` / ``set_auto_fetch``) for
          immediate effect without a config entry reload (plan §6.4).

        Timestamps are ISO strings (or ``None``) so the dict stays JSON-safe for
        the diagnostics export; ``backoff_active`` collapses "failures > 0 and a
        timer actually runs" into one boolean for the UI.
        """
        interval = self.fetch_interval_minutes
        return {
            "fetched_at": now.isoformat(),
            "models_block": self._models_block,
            "last_models_attempt": _isoformat(self.last_models_attempt),
            "last_models_ok": _isoformat(self.last_models_ok),
            "last_models_outcome": self.last_models_outcome,
            "last_error": self.last_error,
            "auto_fetch": self.auto_fetch,
            "base_interval_minutes": self.base_interval_minutes,
            "fetch_interval_minutes": interval,
            "backoff_active": self._failures > 0 and interval is not None,
            "backoff_failures": self._failures,
        }

    async def async_fetch_now(self) -> None:
        """Fetch the live model catalog - the ONLY HTTP path - and never raise.

        Concurrency guard (review fix): the refresh button and the fetch timer
        share this method. A second call while a fetch is already running is
        SKIPPED (not queued) - two overlapping fetches would double-count the
        backoff increments and let two snapshots race. The check before the
        ``async with`` is safe because there is no ``await`` between check and
        acquisition in a single-threaded event loop.

        Failure handling (plan §6.3): the outcome is recorded as ``"fail"``, the
        failure counter (backoff) is incremented, the error message is kept for
        the UI/diagnostics and the PREVIOUS catalog block is preserved. No
        exception escapes, on purpose:

        * the fetch timer and the refresh button must not produce unhandled
          task exceptions,
        * ``GoGaugeAuthError``/``ConfigEntryAuthFailed`` must not be translated
          into a reauth flow - the account entry has no token, so a 401 here is
          a plain fetch failure (plan §6.3: "401 ist auf der Konto-Entry nicht
          möglich").

        The ``finally`` block of :meth:`_fetch_once` always records the attempt
        timestamp, publishes the new snapshot and re-arms the fetch timer,
        because a success resets and a failure extends the effective interval.
        """
        if self._fetch_lock.locked():
            _LOGGER.debug(
                "Go Gauge: account catalog fetch already running - skipping "
                "this call"
            )
            return
        async with self._fetch_lock:
            await self._fetch_once()

    async def _fetch_once(self) -> None:
        """The actual (single-flight) fetch, publish and timer re-arm."""
        now = datetime.now(timezone.utc)
        try:
            live_ids = await self._client.fetch_models()
        except Exception as err:  # noqa: BLE001 - must never propagate
            self.last_models_outcome = "fail"
            self._failures += 1
            self.last_error = str(err) or type(err).__name__
            _LOGGER.warning(
                "Go Gauge: account model catalog fetch failed (%s) - keeping "
                "the last known catalog, next attempt in %s min",
                self.last_error,
                self._effective_fetch_minutes(),
            )
        else:
            self._models_block = build_models_block(live_ids)
            self.last_models_ok = now
            self.last_models_outcome = "ok"
            self.last_error = None
            self._failures = 0
        finally:
            self.last_models_attempt = now
            try:
                self.async_set_updated_data(self._snapshot(now))
            except Exception as err:  # noqa: BLE001 - publish must not raise
                _LOGGER.warning(
                    "Go Gauge: publishing the account snapshot failed (%s)", err
                )
            self._reschedule_fetch()

    def set_fetch_interval(self, minutes: int) -> None:
        """Set the base fetch interval (clamped), re-arm the timer, publish.

        Live effect without a reload (plan §6.4): the caller persists the value
        into ``entry.options`` separately; this method only owns the runtime.
        """
        self.base_interval_minutes = _clamp_minutes(minutes)
        self._reschedule_fetch()
        self.async_set_updated_data(self._snapshot(datetime.now(timezone.utc)))

    def set_auto_fetch(self, enabled: bool) -> None:
        """Switch the automatic catalog fetch on/off live (plan §6.2/§6.4).

        Off cancels the timer and makes ``fetch_interval_minutes`` ``None``; the
        publish tick keeps running so TTL expiries still reach the UI.
        """
        self.auto_fetch = bool(enabled)
        self._reschedule_fetch()
        self.async_set_updated_data(self._snapshot(datetime.now(timezone.utc)))

    def _reschedule_fetch(self) -> None:
        """Cancel the running fetch timer (if any) and arm it with the current interval.

        Does nothing after :meth:`async_stop_fetch_timer`: the ``finally`` block
        of an in-flight fetch also calls this method, and re-arming there would
        leak a timer into an entry that is already unloaded (M4 review fix).

        Defensive on the unsubscribe call: it runs from ``finally`` blocks and
        setters, where an exception would surface as an unhandled task error for
        no benefit.
        """
        previous = self._unsub_fetch
        self._unsub_fetch = None
        if previous is not None:
            try:
                previous()
            except Exception as err:  # noqa: BLE001 - timer cleanup is best effort
                _LOGGER.debug(
                    "Go Gauge: cancelling the account fetch timer failed (%s)", err
                )
        if self._stopped or not self.auto_fetch:
            return
        minutes = self._effective_fetch_minutes()
        if minutes is None:  # defensive: auto_fetch is on here, never falsy
            return
        try:
            self._unsub_fetch = async_track_time_interval(
                self.hass, self._async_fetch_tick, timedelta(minutes=minutes)
            )
        except Exception as err:  # noqa: BLE001 - a missing timer beats a crash
            _LOGGER.warning(
                "Go Gauge: arming the account fetch timer failed (%s) - "
                "automatic catalog fetches stay off until the next reload", err,
            )

    async def _async_fetch_tick(self, now: datetime | None = None) -> None:
        """Timer callback: delegate to ``async_fetch_now`` (which never raises)."""
        try:
            await self.async_fetch_now()
        except Exception as err:  # noqa: BLE001 - last-resort guard for the timer
            _LOGGER.warning(
                "Go Gauge: account fetch tick failed unexpectedly (%s)", err
            )

    async def async_start_fetch_timer(self) -> None:
        """Arm the fetch timer (called from the account entry setup).

        Clears the stop flag first: a coordinator that was stopped by an unload
        and then re-armed by a new setup must schedule again.
        """
        self._stopped = False
        self._reschedule_fetch()

    async def async_stop_fetch_timer(self) -> None:
        """Cancel the fetch timer - idempotent, called from unload/remove.

        Also latches ``_stopped``: a fetch that is still in flight must not
        re-arm the timer when it finishes (M4 - no timer leak after unload).
        Only the next ``async_start_fetch_timer`` unlatches it.
        """
        self._stopped = True
        previous = self._unsub_fetch
        self._unsub_fetch = None
        if previous is None:
            return
        try:
            previous()
        except Exception as err:  # noqa: BLE001 - unload must not fail on cleanup
            _LOGGER.debug(
                "Go Gauge: cancelling the account fetch timer failed (%s)", err
            )


def _domain_data(
    hass: HomeAssistant, *, create: bool = False
) -> dict[str, Any] | None:
    """Return ``hass.data[DOMAIN]`` as a dict, or ``None`` when unavailable.

    Defensive on purpose (same contract as :func:`workspace_coordinators`): the
    legacy tests pass a ``MagicMock`` and diagnostics/services may run while
    ``hass.data`` is not a plain dict yet. ``create=True`` mirrors the
    ``setdefault(DOMAIN, {})`` pattern of the entry setup; callers that only
    read/clean up pass ``create=False`` so they never materialise the key.
    """
    hass_data = getattr(hass, "data", None)
    if not isinstance(hass_data, dict):
        return None
    data = hass_data.setdefault(DOMAIN, {}) if create else hass_data.get(DOMAIN)
    if not isinstance(data, dict):
        return None
    return data
