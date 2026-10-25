"""Account ConfigEntry runtime and lifecycle (2.0.0 account entry).

Since 2.0.0 the integration owns exactly ONE workspace-independent "account"
ConfigEntry next to N workspace entries (plan ``docs/PLAN-account-entry-2.0.0.md``
§2.4, §7). This module holds:

* ``AccountCoordinator`` - the workspace-independent catalog/publish runtime
  (publish tick, fetch timer, backoff, plan §2.4/§6.2/§6.3),
* ``workspace_coordinators`` - the read-only view of the loaded workspace
  coordinators in ``hass.data[DOMAIN]`` (evidence source for the reachability
  matrix, plan §5),
* ``async_ensure_account_entry`` - the singleton guard that creates the account
  entry when a workspace justifies it (full implementation, plan §7.2),
* ``async_setup_account_entry`` / ``async_unload_account_entry`` /
  ``async_remove_account_entry`` - the real account entry lifecycle: register
  the coordinator under BOTH ``hass.data[DOMAIN]`` keys, run the tolerant first
  publish (no HTTP) and the one-time first fetch, arm the fetch timer, and tear
  all of it down again on unload/remove (plan §2.4, §3.4, §7.3),
* ``async_adopt_account_entities`` + ``AdoptionReport`` - the entity adoption
  that moves the five catalog entities onto the account entry (plan §3.3,
  §4.1-§4.6, §8.3/§8.4): registry sweep per kind, collision/loser path,
  duplicate-device merge, the device hand-over to the account entry and the
  removal of the legacy workspace model controls (``report.removed_settings``,
  plan §4.7),
* ``collect_evidence`` - the timestamped fetch outcomes of the loaded
  coordinators, consumed by the account reachability sensor (plan §5).

The account setup forwards ``ACCOUNT_PLATFORMS`` AFTER the adoption (plan §3.3
step 3e): HA re-binds the existing registry entries through their now canonical
``unique_id`` and keeps ``entity_id``, history, area and labels. Exactly one
generation of the five catalog entities exists - the workspace entries create
no catalog entity at all since 2.0.0.

The legacy workspace option keys ``auto_update_models`` /
``models_refresh_minutes`` are NOT deleted by a migration write (the
``auto_update_models`` values of several workspaces cannot be merged
losslessly). They are unused by the workspace runtime from 2.0.0 on, but they
are the SOURCE of a one-time seed: :func:`_seed_account_options` copies the
first existing workspace value onto the new account entry so an upgrading
installation keeps its settings. That is a deliberate deviation from plan §4.7
(explicit user decision: "migration without data loss" wins over "no
derivation"), and it is one-shot - the account entry owns the values from then
on. The next options-flow save of a workspace drops the stale keys because the
flow writes the full option set.

Import graph (kept acyclic on purpose): this module imports ``.api``,
``.catalog``, ``.const`` and ``.coordinator``; ``coordinator.py`` does NOT
import this module and nothing here imports the integration ``__init__``, which
imports this module.

KNOWN FOLLOW-UP (2.1.0, deliberately NOT in 2.0.0): this file still carries the
coordinator, the lifecycle, the adoption and the reachability helpers at once,
so some functions are long. Splitting it into per-concern modules is scheduled
for 2.1.0 - right after the release the cost/risk ratio is wrong: every test
loads these modules manually (see the ``_load`` helpers in the suite), so a
move would touch the whole test-loader chain while the release is under
validation. Purely structural, no behaviour change.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigEntryState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import OpenCodeGoApiClient
from .catalog import build_models_block
from .const import (
    ACCOUNT_BACKOFF_MAX_MINUTES,
    ACCOUNT_DEVICE_IDENTIFIER,
    ACCOUNT_ENTRY_UNIQUE_ID,
    ACCOUNT_MODELS_REFRESH_MAX,
    ACCOUNT_MODELS_REFRESH_MIN,
    ACCOUNT_PLATFORMS,
    ACCOUNT_PUBLISH_TICK_MINUTES,
    CONF_AUTO_UPDATE_MODELS,
    CONF_MODELS_REFRESH_MINUTES,
    DATA_ACCOUNT_KEY,
    DATA_ADOPTION_LOCK,
    DATA_ADOPTION_REPORT,
    DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS,
    DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
    DEFAULT_USAGE_REFRESH_MINUTES,
    DOMAIN,
    ENTRY_KIND_WORKSPACE,
    conf_entry_kind,
)
from .coordinator import GoGaugeCoordinator

if TYPE_CHECKING:  # no runtime import: account_logic is loaded lazily on purpose
    from .account_logic import Evidence, Reachability

_LOGGER = logging.getLogger(__name__)

# Repair-issue id suffixes that are not per-entity-kind (plan §4.4/§4.7). The
# per-kind collision ids are derived from ``account_logic.ACCOUNT_ENTITY_KINDS``
# in ``_adoption_issue_ids`` below.
_GLOBAL_ADOPTION_ISSUE_IDS: tuple[str, ...] = (
    "account_device_merged",
    "account_device_not_empty",
    "adoption_unsupported",
    "adoption_failed",
)

# Legacy workspace model controls removed on adoption (plan §4.7). The two
# canonical account ids are the new home of these settings and must survive.
_LEGACY_MODEL_SETTING_SUFFIXES: tuple[str, ...] = (
    "_auto_update_models",
    "_models_refresh_minutes",
)
_CANONICAL_MODEL_SETTING_IDS: frozenset[str] = frozenset({
    "account_auto_update_models",
    "account_models_refresh_minutes",
})


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


def workspace_coordinators(hass: HomeAssistant) -> dict[str, GoGaugeCoordinator]:
    """Return ``{entry_id: GoGaugeCoordinator}`` for the loaded workspace entries.

    ``hass.data[DOMAIN]`` holds one runtime object per entry plus foreign keys
    (e.g. ``_account``, the adoption lock), so the mapping
    is filtered by type instead of trusting every value. Defensive on
    ``hass.data``/``hass.data[DOMAIN]``: the legacy tests pass a ``MagicMock``
    and diagnostics may run while ``hass.data`` is not a plain dict yet - both
    must yield an empty mapping instead of raising.
    """
    hass_data = getattr(hass, "data", None)
    if not isinstance(hass_data, dict):
        return {}
    domain_data = hass_data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return {}
    return {
        entry_id: coordinator
        for entry_id, coordinator in domain_data.items()
        if isinstance(coordinator, GoGaugeCoordinator)
    }


def collect_evidence(hass: HomeAssistant) -> list[Evidence]:
    """Collect the timestamped fetch outcomes of the loaded coordinators.

    The source of the reachability matrix (plan §5): one ``Evidence`` per
    observed attempt -

    * account catalog fetch -> ``source="models"`` (outcome ``ok``/``fail``),
    * every loaded workspace cycle -> ``source="usage"`` with the workspace
      name and the coordinator's ``last_usage_outcome`` (an unknown/missing
      outcome counts as ``fail``: it must never be able to prove success).

    Returns a plain list (the caller only iterates it). Defensive by design: a
    coordinator without the evidence attributes (older object, test double) is
    skipped instead of raising - this function runs from a sensor property and
    must never take the entity update down.
    """
    from .account_logic import SOURCE_MODELS, SOURCE_USAGE, Evidence

    evidence: list[Evidence] = []
    data = _domain_data(hass)
    account = data.get(DATA_ACCOUNT_KEY) if data is not None else None
    if isinstance(account, AccountCoordinator):
        attempt = getattr(account, "last_models_attempt", None)
        if isinstance(attempt, datetime):
            outcome = "ok" if getattr(account, "last_models_outcome", None) == "ok" else "fail"
            evidence.append(Evidence(SOURCE_MODELS, None, outcome, attempt))

    for coordinator in workspace_coordinators(hass).values():
        attempt = getattr(coordinator, "last_usage_attempt", None)
        if not isinstance(attempt, datetime):
            continue
        usage_outcome = getattr(coordinator, "last_usage_outcome", None)
        if usage_outcome not in ("ok", "neutral", "fail"):
            usage_outcome = "fail"
        evidence.append(
            Evidence(
                SOURCE_USAGE,
                getattr(coordinator, "ws_name", None),
                usage_outcome,
                attempt,
            )
        )
    return evidence


@dataclass(frozen=True)
class ReachabilitySnapshot:
    """One consistent reachability resolution plus the inputs it was built from.

    The binary sensor and the diagnostics export need more than the tri-state
    itself: the TTLs they publish and the per-evidence rows (age/TTL/fresh) come
    from the SAME computation, so a state and its attributes can never disagree.
    Hence this carrier instead of separate TTL helpers at the call sites - see
    :func:`account_reachability_snapshot`.
    """

    reachability: Reachability
    evidence: list[Evidence]
    now: datetime
    models_ttl: timedelta
    usage_ttl: timedelta

    def evidence_attributes(self) -> list[dict[str, Any]]:
        """Render the evidence as JSON-safe attribute rows.

        Each row carries ``age_s``/``ttl_s``/``fresh`` so a support case (and the
        sensor attributes) can see WHY the matrix resolved the way it did. The
        per-row freshness rule (``age <= ttl``, unknown source -> models TTL) is
        applied HERE, on the same ``now``/TTL inputs the matrix itself used, so
        the attributes explain exactly the state they were published with;
        consumers only format the result and never recompute a ``now``.
        """
        rows: list[dict[str, Any]] = []
        for item in self.evidence:
            ttl = self.usage_ttl if item.source == "usage" else self.models_ttl
            at = (
                item.at
                if item.at.tzinfo is not None
                else item.at.replace(tzinfo=timezone.utc)
            )
            age = (self.now - at).total_seconds()
            rows.append({
                "source": item.source,
                "workspace": item.workspace,
                "outcome": item.outcome,
                "age_s": round(age, 3),
                "ttl_s": ttl.total_seconds(),
                "fresh": age <= ttl.total_seconds(),
            })
        return rows


def account_reachability_snapshot(hass: HomeAssistant) -> ReachabilitySnapshot:
    """Collect evidence + TTLs ONCE and resolve the reachability matrix (plan §5).

    The single computation site behind :func:`account_reachability`: it owns both
    inputs of the matrix -

    * the evidence (:func:`collect_evidence`: account catalog fetch + every
      loaded workspace cycle),
    * the TTLs (models = ``MODELS_TTL_FACTOR`` x the account's *configured base*
      interval, fallback when auto-fetch is off; usage = ``USAGE_TTL_FACTOR`` x
      the smallest workspace interval, fallback
      ``DEFAULT_USAGE_REFRESH_MINUTES`` when no workspace is loaded).

    Defensive by design: a missing account coordinator, a non-dict ``hass.data``
    or a coordinator with broken attributes must never take a sensor update or
    the diagnostics export down. In that case the resolution runs with EMPTY
    evidence, which the matrix answers with ``None``/``no_evidence`` - "unknown"
    instead of a made-up state.
    """
    from .account_logic import models_ttl, resolve_reachability, usage_ttl

    now = datetime.now(timezone.utc)
    evidence: list[Evidence] = []
    models_ttl_ = models_ttl(None)
    usage_minutes = DEFAULT_USAGE_REFRESH_MINUTES

    try:
        evidence = collect_evidence(hass)
        data = _domain_data(hass)
        account = data.get(DATA_ACCOUNT_KEY) if data is not None else None
        if account is not None and bool(getattr(account, "auto_fetch", False)):
            models_ttl_ = models_ttl(
                int(getattr(account, "base_interval_minutes"))
            )
        minutes = [
            int(
                getattr(
                    coordinator, "usage_minutes", DEFAULT_USAGE_REFRESH_MINUTES
                )
            )
            for coordinator in workspace_coordinators(hass).values()
        ]
        if minutes:
            usage_minutes = min(minutes)
    except Exception as err:  # noqa: BLE001 - evidence is always optional
        _LOGGER.debug(
            "Go Gauge: reachability inputs unusable (%s) - resolving without "
            "evidence",
            err,
        )
        evidence = []
        models_ttl_ = models_ttl(None)
        usage_minutes = DEFAULT_USAGE_REFRESH_MINUTES

    usage_ttl_ = usage_ttl(usage_minutes)
    return ReachabilitySnapshot(
        reachability=resolve_reachability(
            evidence, now, models_ttl_, usage_ttl_
        ),
        evidence=list(evidence),
        now=now,
        models_ttl=models_ttl_,
        usage_ttl=usage_ttl_,
    )


def account_reachability(hass: HomeAssistant) -> Reachability:
    """Resolve the API reachability of this installation (plan §5).

    THE one place where the TTL/evidence rule is applied for BOTH consumers -
    the account binary sensor (:class:`binary_sensor.ApiReachableBinarySensor`)
    and the diagnostics export. Neither consumer re-implements TTL scaling,
    evidence collection or the resolution order; both go through this function
    (or its :func:`account_reachability_snapshot` carrier when they also need
    the TTLs/evidence rows).

    TTLs: models evidence = ``MODELS_TTL_FACTOR`` x the account's configured
    base interval (``MODELS_TTL_FALLBACK_MINUTES`` when auto-fetch is off),
    usage evidence = ``USAGE_TTL_FACTOR`` x the smallest workspace usage interval
    (``DEFAULT_USAGE_REFRESH_MINUTES`` when no workspace is loaded).

    Never raises: without a coordinator (or with broken attributes) the matrix
    resolves empty evidence into ``None``/``no_evidence``.
    """
    return account_reachability_snapshot(hass).reachability


def _account_entries(entries: list[Any]) -> list[Any]:
    """Return the entries whose ``unique_id`` marks them as the account entry."""
    return [
        entry for entry in entries
        if getattr(entry, "unique_id", None) == ACCOUNT_ENTRY_UNIQUE_ID
    ]


def _entry_is_loaded(entry: Any) -> bool:
    """Return ``True`` when the entry's state is ``ConfigEntryState.LOADED``.

    Compares both by identity and by equality: in production this is the real
    StrEnum (``is``), while the offline test fakes carry the raw string values
    (``==``). A missing/unfamiliar ``state`` attribute is treated as "not
    loaded"; the ensure then only logs at DEBUG level, because HA - not this
    integration - owns (re)loading a stored entry (plan §7.2).
    """
    state = getattr(entry, "state", None)
    loaded = getattr(ConfigEntryState, "LOADED", None)
    if state is None or loaded is None:
        return False
    return bool(state is loaded or state == loaded)


def _has_enabled_workspace(entries: list[Any]) -> bool:
    """Return ``True`` when at least one enabled workspace entry exists.

    Workspace = ``conf_entry_kind(entry.data) == ENTRY_KIND_WORKSPACE`` (the
    default for every pre-2.0.0 entry). A disabled workspace does not count:
    the account entry must not come into existence just because a workspace is
    switched off (plan §3.5 - "deaktiviert = Nutzerintent respektieren").
    """
    for entry in entries:
        if conf_entry_kind(getattr(entry, "data", None)) != ENTRY_KIND_WORKSPACE:
            continue
        if getattr(entry, "disabled_by", None) is None:
            return True
    return False


async def async_ensure_account_entry(hass: HomeAssistant) -> None:
    """Create the singleton account entry when a workspace justifies it.

    REENTRANCY CONTRACT (review fix 2.0.0): ``DATA_ADOPTION_LOCK`` is held ONLY
    for the read/decision window - never across an HA call that can run entry
    setup. The import flow creates the entry and HA sets it up synchronously in
    the same call chain; that setup runs :func:`async_adopt_account_entities`,
    which takes the SAME (non-reentrant) lock. Holding the lock across
    ``flow.async_init`` hard-deadlocked first installations, re-creates after a
    deletion and the reload of an unloaded account entry. The actual creation
    therefore happens AFTER the ``async with`` block; a race between two
    concurrent callers is absorbed by the re-check inside the lock plus the
    flow's ``_abort_if_unique_id_configured`` (plan §7.2).

    No ``hass.config_entries.async_setup`` call at all (plan §7.2: HA sets
    stored entries up itself; an extra setup in the caller would load twice and
    spam ``OperationNotAllowed``). An existing account entry is:

    * disabled -> left alone (user intent, plan §3.5),
    * loaded -> a no-op,
    * present but not loaded -> only a DEBUG log; HA owns that transition.

    No account entry + at least one enabled workspace -> the import flow is
    started (result deliberately not inspected, plan §11.3 V1). No account
    entry + no enabled workspace -> nothing happens; the account entry is
    created by the first workspace setup, never by a bare HA start without any
    Go Gauge entry (plan §7.3).

    Defensive by design (plan §2.4/M5): the whole body is wrapped in a broad
    ``except`` and every individual HA call has its own guard. This double
    protection is intentional - the function is called at the END of a
    workspace setup (and from a background task of the domain setup), so any
    escaping exception would fail that setup and take every workspace entity
    down with it. Failing to create the account entry is always preferable to
    breaking the caller.

    Duplicate account entries (only possible with hand-edited storage) are
    logged as a WARNING and the lowest ``entry_id`` is kept. No repair issue is
    raised here because the issue translations ship together with the adoption
    issues (plan §4.4); a repair issue without a
    ``strings.json``/``translations`` entry would surface as a raw key in the
    UI.
    """
    try:
        # ``hass.data`` is a dict in production; MagicMock-based legacy tests
        # pass a Mock, where ``setdefault``/``async with`` would "work" but
        # nothing observable happens. Bail out cleanly instead of operating on
        # a mock (isinstance guard, see module docstring of tests/conftest.py).
        if not isinstance(hass.data, dict):
            _LOGGER.debug(
                "Go Gauge: account ensure skipped - hass.data is not a dict"
            )
            return

        data = hass.data.setdefault(DOMAIN, {})
        if not isinstance(data, dict):
            _LOGGER.debug(
                "Go Gauge: account ensure skipped - hass.data[%s] is not a dict",
                DOMAIN,
            )
            return
        lock = data.setdefault(DATA_ADOPTION_LOCK, asyncio.Lock())

        # --- decision window: lock held, no HA call that can set entries up ---
        async with lock:
            entries = list(hass.config_entries.async_entries(DOMAIN))
            existing = _account_entries(entries)

            if len(existing) > 1:
                _LOGGER.warning(
                    "Go Gauge: %d account entries found (%s) - keeping %s, "
                    "the others stay untouched",
                    len(existing),
                    ", ".join(
                        str(getattr(entry, "entry_id", "?")) for entry in existing
                    ),
                    min(existing, key=lambda e: e.entry_id).entry_id,
                )

            account_entry = (
                min(existing, key=lambda e: e.entry_id) if existing else None
            )
            if account_entry is not None:
                if getattr(account_entry, "disabled_by", None) is not None:
                    # User switched the account entry off - do not re-create or
                    # re-enable it (plan §3.5).
                    return
                if _entry_is_loaded(account_entry):
                    return
                # Present but not loaded: HA decides when to (re)load a stored
                # entry. Deliberately NO ``async_setup`` here (plan §7.2) - the
                # call would run the entry setup inside this lock's caller and
                # can raise ``OperationNotAllowed`` for an already-loading
                # entry. DEBUG only: this is a normal transient state.
                _LOGGER.debug(
                    "Go Gauge: account entry %s exists but is not loaded - HA "
                    "loads stored entries itself, nothing to do",
                    account_entry.entry_id,
                )
                return

            if not _has_enabled_workspace(entries):
                return

        # --- creation OUTSIDE the lock: the flow's setup takes the lock again ---
        try:
            # The import flow creates the entry; the returned flow result is
            # intentionally NOT evaluated (plan §11.3 V1: ``async_init`` runs
            # the flow to completion, so no extra setup call is needed).
            await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}
            )
        except Exception as err:  # noqa: BLE001 - must never propagate
            _LOGGER.warning(
                "Go Gauge: creating the account entry failed (%s) - "
                "the workspace setup continues", err,
            )
    except Exception as err:  # noqa: BLE001 - outermost safety net
        _LOGGER.warning(
            "Go Gauge: account ensure failed unexpectedly (%s) - continuing "
            "without an account entry", err,
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


def _adoption_issue_ids() -> tuple[str, ...]:
    """Return every stable adoption repair-issue id (plan §4.4).

    Imported lazily: ``account_logic`` is a sibling module, and the offline test
    loader only resolves such imports for modules already registered in
    ``sys.modules``. A module-level import here would force every existing test
    file that loads ``account.py`` to preload it as well; the helper is the only
    consumer, so resolving it on first use keeps that coupling out of the test
    suite (and costs one import lookup per account removal).
    """
    from .account_logic import ACCOUNT_ENTITY_KINDS

    return tuple(
        f"account_entity_collision_{kind}" for kind in ACCOUNT_ENTITY_KINDS
    ) + _GLOBAL_ADOPTION_ISSUE_IDS


def _delete_adoption_issues(hass: HomeAssistant) -> None:
    """Delete all adoption repair issues of this domain - idempotent.

    Deleting a non-existent issue is a no-op in HA, so a second call (e.g.
    remove after a previous remove, or a remove without any adoption run ever
    having happened) is safe. Every single delete is guarded: the issue registry
    is HA machinery that must never be able to fail the entry removal.
    """
    for issue_id in _adoption_issue_ids():
        try:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
        except Exception as err:  # noqa: BLE001 - issue cleanup is best effort
            _LOGGER.debug(
                "Go Gauge: deleting the repair issue %s failed (%s)",
                issue_id,
                err,
            )


@dataclass
class AdoptionReport:
    """What one adoption pass changed (plan §2.4/§4.5, diagnostics source).

    Every field is a plain, JSON-safe record of ONE pass, so the diagnostics
    export can show a support case exactly what the last adoption did without
    re-reading the registries:

    * ``renamed`` - ``(old unique_id, canonical unique_id)`` per rewritten
      winner (plan §4.5 step 1),
    * ``moved`` - ``entity_id``s whose ``config_entry_id`` and/or ``device_id``
      was written (plan §4.5 steps 2-3). Collision losers are reported in
      ``collisions`` instead, so "moved" only ever names adopted winners,
    * ``device_moved`` - id of the device handed over to the account entry, or
      ``None`` when there was nothing to hand over (HA creates the device on the
      platform forward),
    * ``merged_devices`` - ids of EMPTY duplicate devices that were removed
      (plan §3.3 step 3b),
    * ``collisions`` - ``(kind, loser entity_id)`` per disabled duplicate
      (plan §4.3),
    * ``errors`` - human-readable, non-fatal problems of this pass. Non-empty
      means "try again on the next start", never "the setup failed",
    * ``removed_settings`` - legacy workspace control entities removed by this
      pass (plan §4.7: ``*_auto_update_models`` / ``*_models_refresh_minutes``,
      canonical ``account_*`` excluded). ``0`` on every idempotent re-run.
    """

    renamed: list[tuple[str, str]] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    device_moved: str | None = None
    merged_devices: list[str] = field(default_factory=list)
    collisions: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    removed_settings: int = 0


def _user_disabler() -> Any:
    """Return ``RegistryEntryDisabler.USER`` defensively.

    Read through ``getattr`` with the real string as fallback: the offline tests
    hand out a throwaway class for every attribute of a faked ``homeassistant``
    module, and a collision loser must still be disabled instead of crashing the
    sweep (plan §4.3/§11.1 R6). The real enum member is returned unchanged (no
    ``str()`` conversion) so ``async_update_entity`` receives exactly what HA
    expects.
    """
    return getattr(er.RegistryEntryDisabler, "USER", "user")


def _warning_severity() -> Any:
    """Return ``IssueSeverity.WARNING`` unchanged (same reason as above)."""
    return getattr(ir.IssueSeverity, "WARNING", "warning")


def _create_adoption_issue(
    hass: HomeAssistant,
    issue_id: str,
    *,
    translation_key: str | None = None,
    placeholders: dict[str, str] | None = None,
) -> None:
    """Create/update one stable adoption repair issue - never raises.

    Stable issue ids are what makes the whole adoption idempotent (plan §4.4):
    creating an existing id overwrites it instead of piling up duplicates. The
    issue registry is HA machinery; a failing (or unmocked) registry must never
    be able to abort the adoption or the account setup (M5).
    """
    try:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=_warning_severity(),
            translation_key=translation_key or issue_id,
            translation_placeholders=dict(placeholders or {}),
        )
    except Exception as err:  # noqa: BLE001 - repairs are best effort
        _LOGGER.debug(
            "Go Gauge: creating the repair issue %s failed (%s)", issue_id, err
        )


def _delete_adoption_issue(hass: HomeAssistant, issue_id: str) -> None:
    """Delete one adoption repair issue (idempotent, never raises)."""
    try:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    except Exception as err:  # noqa: BLE001 - repairs are best effort
        _LOGGER.debug(
            "Go Gauge: deleting the repair issue %s failed (%s)", issue_id, err
        )


def _fresh_entry(ent_reg: Any, entity_id: str | None, fallback: Any) -> Any:
    """Re-read one registry entry after a write, falling back to the snapshot.

    Real HA hands out immutable ``RegistryEntry`` snapshots, so after a write the
    local object is stale. Re-resolving keeps the following predicates honest
    ("write only on a violated predicate", plan §4.5) without depending on the
    registry returning the mutated object.
    """
    try:
        current = ent_reg.async_get(entity_id)
    except Exception:  # noqa: BLE001 - stale snapshot beats a crash
        return fallback
    return current if current is not None else fallback


def _note_moved(report: AdoptionReport, entity_id: str | None) -> None:
    """Record ``entity_id`` in ``report.moved`` at most once."""
    if entity_id is not None and entity_id not in report.moved:
        report.moved.append(entity_id)


def _canonical_blocker(
    ent_reg: Any, entity_domain: str, canonical: str, winner: Any
) -> Any | None:
    """Return the entry that already owns ``canonical``, if it is not the winner.

    Two independent checks (plan §4.6):

    * ``async_get_entity_id`` is the authoritative lookup real HA uses to reject
      a duplicate ``new_unique_id`` with ``ValueError`` - checking it up front
      is what avoids that exception in the first place,
    * a second scan covers disabled-by-config entries: they have
      ``entity_id is None``, so ``async_get_entity_id`` cannot report them, but
      the registry entry still occupies the unique_id.

    HA semantics trap (the bug this parameter rename exists for): a
    ``RegistryEntry`` carries ``platform`` = the INTEGRATION domain
    (``go_gauge``) and ``domain`` = the ENTITY domain (``sensor``,
    ``binary_sensor``, ...). ``async_get_entity_id`` takes them in the order
    ``(domain, platform, unique_id)`` - the entity domain first. Swapping the
    two makes every lookup miss and the whole adoption a silent no-op.
    """
    try:
        occupant = ent_reg.async_get_entity_id(entity_domain, DOMAIN, canonical)
    except Exception:  # noqa: BLE001 - fall through to the entry scan
        occupant = None
    if occupant and occupant != getattr(winner, "entity_id", None):
        return _fresh_entry(ent_reg, occupant, None)

    for other in ent_reg.entities.values():
        if other is winner:
            continue
        if getattr(other, "platform", None) != DOMAIN:
            continue
        if getattr(other, "domain", None) != entity_domain:
            continue
        if getattr(other, "unique_id", None) != canonical:
            continue
        if getattr(other, "entity_id", None) != getattr(winner, "entity_id", None):
            return other
    return None


def _adopt_winner(
    ent_reg: Any,
    entry: ConfigEntry,
    report: AdoptionReport,
    canonical: str,
    winner: Any,
    device_id: str | None,
) -> None:
    """Migrate one winner to the account identity (plan §4.5 steps 1-3).

    Three independent predicates, each write guarded by its own violation check,
    so a second pass is write-free. The order matters: the ``unique_id`` is
    normalised first, then the ownership, then the device - and any exception
    propagates to the per-entity guard of the caller.
    """
    current = _fresh_entry(ent_reg, winner.entity_id, winner)
    if getattr(current, "unique_id", None) != canonical:
        old_unique_id = getattr(current, "unique_id", None) or ""
        ent_reg.async_update_entity(current.entity_id, new_unique_id=canonical)
        report.renamed.append((str(old_unique_id), canonical))
        current = _fresh_entry(ent_reg, current.entity_id, current)

    if getattr(current, "config_entry_id", None) != entry.entry_id:
        ent_reg.async_update_entity(
            current.entity_id, config_entry_id=entry.entry_id
        )
        _note_moved(report, current.entity_id)
        current = _fresh_entry(ent_reg, current.entity_id, current)

    if device_id is not None and getattr(current, "device_id", None) != device_id:
        ent_reg.async_update_entity(current.entity_id, device_id=device_id)
        _note_moved(report, current.entity_id)


def _adopt_loser(
    ent_reg: Any,
    entry: ConfigEntry,
    report: AdoptionReport,
    kind: str,
    loser: Any,
    device_id: str | None,
) -> None:
    """Neutralise one collision loser without deleting it (plan §4.3).

    ``config_entry_id`` -> account entry (a deleted workspace must not orphan
    the entry), ``device_id`` -> canonical account device (no ghost device left
    behind), ``disabled_by = USER`` (no unique_id clash with the platform, no
    polluting the device card) - and the legacy ``unique_id`` stays untouched so
    the user can still tell the two entries apart. No ``async_remove``:
    deleting a user's registry entry is their decision, not ours (plan §11.1 R5).
    """
    entity_id = getattr(loser, "entity_id", None)
    current = _fresh_entry(ent_reg, entity_id, loser)

    if getattr(current, "config_entry_id", None) != entry.entry_id:
        ent_reg.async_update_entity(entity_id, config_entry_id=entry.entry_id)
        current = _fresh_entry(ent_reg, entity_id, current)

    if device_id is not None and getattr(current, "device_id", None) != device_id:
        ent_reg.async_update_entity(entity_id, device_id=device_id)
        current = _fresh_entry(ent_reg, entity_id, current)

    if getattr(current, "disabled_by", None) is None:
        ent_reg.async_update_entity(entity_id, disabled_by=_user_disabler())

    report.collisions.append((kind, str(entity_id)))


def _sweep_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    ent_reg: Any,
    *,
    kinds: tuple[str, ...],
    entity_domain_by_kind: dict[str, str],
    canonical_unique_id: Any,
    device_id: str | None,
) -> set[str]:
    """Run the per-kind entity sweep and return the candidate device ids.

    Per kind (plan §4.1/§4.2/§4.3):

    1. collect the candidates via the INTEGRATION platform + entity domain +
       ``unique_id`` (canonical OR legacy suffix, so both ``account_<kind>`` and
       ``<any entry>_<kind>`` are found; ``config_entry_id is None`` is
       included). Careful with HA's field names: ``RegistryEntry.platform`` is
       the integration domain (``DOMAIN`` = ``go_gauge``) while
       ``RegistryEntry.domain`` is the entity domain (``sensor``,
       ``binary_sensor``, ...) - ``entity_domain_by_kind`` therefore supplies
       the LATTER, never a platform name.
    2. skip disabled-by-config entries (``entity_id is None``) - they cannot be
       adopted, but they DO occupy their unique_id (see ``_canonical_blocker``),
    3. pick the deterministic winner, run the winner predicates (unique_id ->
       config_entry_id -> device_id) or fall back to the loser path when another
       entry already owns the canonical unique_id,
    4. converge the per-kind repair issue.

    Every entity is processed inside its own ``except (ValueError, TypeError,
    KeyError)``: a concurrent workspace removal may invalidate a registry entry
    mid-sweep, and that must end up in ``report.errors`` instead of aborting the
    pass (plan §4.6/§11.1 R6).
    """
    candidate_device_ids: set[str] = set()

    for kind in kinds:
        entity_domain = entity_domain_by_kind[kind]
        canonical = canonical_unique_id(kind)
        collision_issue_id = f"account_entity_collision_{kind}"

        candidates = [
            candidate
            for candidate in ent_reg.entities.values()
            if getattr(candidate, "platform", None) == DOMAIN
            and getattr(candidate, "domain", None) == entity_domain
            and getattr(candidate, "unique_id", None)
            and (
                candidate.unique_id == canonical
                or candidate.unique_id.endswith(f"_{kind}")
            )
        ]

        adoptable = []
        for candidate in candidates:
            if candidate.entity_id is None:
                _LOGGER.debug(
                    "Go Gauge: account adoption skips %s (%s) - no entity_id "
                    "(disabled by config)",
                    candidate.unique_id,
                    kind,
                )
                continue
            adoptable.append(candidate)
            if candidate.device_id:
                candidate_device_ids.add(candidate.device_id)

        if not adoptable:
            # Nothing to adopt for this kind -> the collision repair (if any) is
            # resolved. Idempotent self-healing, plan §4.4.
            _delete_adoption_issue(hass, collision_issue_id)
            continue

        # Deterministic winner (plan §4.2): canonical first, then the smallest
        # entity_id. The third key is only a tie-breaker for registry doubles -
        # real HA's RegistryEntry has no ``id`` field, hence the defensive
        # ``getattr`` (an AttributeError here would abort the whole kind).
        winner = min(
            adoptable,
            key=lambda candidate: (
                candidate.unique_id != canonical,
                candidate.entity_id or "",
                str(getattr(candidate, "id", None) or ""),
            ),
        )

        blocker = _canonical_blocker(ent_reg, entity_domain, canonical, winner)
        if blocker is None:
            try:
                _adopt_winner(ent_reg, entry, report, canonical, winner, device_id)
            except (ValueError, TypeError, KeyError) as err:
                report.errors.append(f"{kind}:{winner.entity_id}: {err}")
                _LOGGER.warning(
                    "Go Gauge: adopting the account entity %s (%s) failed (%s) - "
                    "continuing with the other kinds",
                    winner.entity_id,
                    kind,
                    err,
                )
            losers = [
                candidate for candidate in adoptable if candidate is not winner
            ]
            winner_label = winner.entity_id or ""
        else:
            # The canonical unique_id is already taken by an entry we cannot
            # (or must not) rename away from: every candidate takes the loser
            # path, no rename is attempted at all (plan §4.6).
            losers = adoptable
            winner_label = getattr(blocker, "entity_id", None) or canonical
            _LOGGER.debug(
                "Go Gauge: canonical unique_id %s is already occupied by %s - "
                "treating all %d candidate(s) as collision losers",
                canonical,
                getattr(blocker, "entity_id", None) or "<disabled>",
                len(losers),
            )

        for loser in losers:
            try:
                _adopt_loser(ent_reg, entry, report, kind, loser, device_id)
            except (ValueError, TypeError, KeyError) as err:
                report.errors.append(f"{kind}:{loser.entity_id}: {err}")
                _LOGGER.warning(
                    "Go Gauge: disabling the duplicate account entity %s (%s) "
                    "failed (%s) - continuing",
                    loser.entity_id,
                    kind,
                    err,
                )

        if losers:
            _create_adoption_issue(
                hass,
                collision_issue_id,
                translation_key="account_entity_collision",
                placeholders={
                    "kind": kind,
                    "winner": winner_label,
                    "losers": ", ".join(
                        str(getattr(loser, "entity_id", "") or "")
                        for loser in losers
                    ),
                },
            )
        else:
            _delete_adoption_issue(hass, collision_issue_id)

    return candidate_device_ids


def _device_entries(dev_reg: Any) -> list[Any]:
    """Return every device entry of the registry - no mapping access.

    The device registry changed shape in HA 2026.9: the ``devices`` collection
    now yields ``DeviceEntry`` objects when iterated (the supported enumeration),
    while using it as a mapping - subscript, membership, ``.values()``, ``.get()``
    - is deprecated and stops working in HA 2027.9. Up to HA 2026.8 the same
    attribute is still a plain ``Mapping`` whose iteration yields device *ids*;
    there ``.values()`` is the only entry access and NOT deprecated.

    The collection type separates both generations reliably (the deprecated
    2026.9 view is not a ``Mapping``). Note that ``DeviceRegistry`` itself is
    NOT iterable on either version, so ``for device in registry`` is not an
    option.

    A missing collection yields an empty list, never an exception: the real
    ``DeviceRegistry`` only creates ``devices`` during ``_async_load()``, so the
    attribute is simply absent on a not-yet-loaded registry (and on foreign
    registry doubles). "No devices known" is the conservative truth in that
    state, and every caller - including the unguarded ones in
    :func:`_device_has_dependencies` and :func:`_move_account_device` - may rely
    on getting a list back instead of having to catch ``AttributeError``.
    """
    devices = getattr(dev_reg, "devices", None)
    if devices is None:
        return []
    if isinstance(devices, Mapping):
        return list(devices.values())
    return list(devices)


def _canonical_device(dev_reg: Any) -> Any | None:
    """Return the device carrying ``(DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)``.

    Primary path is HA's own non-deprecated identifier lookup
    ``DeviceRegistry.async_get_devices(identifiers={...})`` (available since HA
    2026.8, i.e. the declared minimum). It replaces the two deprecated lookup
    styles - ``async_get_device`` and treating the registry's ``devices``
    attribute as a mapping - which stop working in HA 2027.9. Deliberately NOT
    ``async_get_device_by_identifier``: that one is scoped to ONE owning config
    entry (``identifier, config_entry_id``) and would miss the canonical device
    while it is still owned by the legacy workspace entry.

    Version fallback: without that helper the entries are enumerated through
    :func:`_device_entries` and matched by identifier - the path supported by
    older cores. Identifiers are stable for the canonical account device;
    should more than one device carry the identifier (registry drift), the
    smallest ``id`` wins so the choice is deterministic across runs. A registry
    without a usable surface or any lookup error is a normal "no device" state -
    never a setup failure.
    """
    identifier = (DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)

    lookup = getattr(dev_reg, "async_get_devices", None)
    if callable(lookup):
        try:
            matches = list(lookup(identifiers={identifier}))
        except (TypeError, AttributeError):
            # Older core: no such lookup / different signature -> enumerate.
            matches = None
        except Exception:  # noqa: BLE001 - missing device is a normal state
            return None
        if matches is not None:
            if not matches:
                return None
            return min(matches, key=lambda device: str(getattr(device, "id", "")))

    try:
        matches = [
            device
            for device in _device_entries(dev_reg)
            if identifier in getattr(device, "identifiers", set())
        ]
    except Exception:  # noqa: BLE001 - missing device is a normal state
        return None
    if not matches:
        return None
    return min(matches, key=lambda device: str(getattr(device, "id", "")))


def _workspace_device_identifiers(
    hass: HomeAssistant, entry: ConfigEntry
) -> set[tuple[str, str]]:
    """Return the device identifiers of pure workspace devices.

    A workspace device (``identifiers = {(DOMAIN, <workspace entry_id>)}``,
    created by ``entity.GoGaugeEntityBase``) is NEVER a duplicate: it carries
    the workspace's usage entities and legitimately stays its own device. Its
    identifiers are returned so the duplicate-device merge skips it entirely - a
    workspace device must not even produce an ``account_device_not_empty``
    repair (it was never a candidate for merging).
    """
    account_entry_id = getattr(entry, "entry_id", None)
    try:
        entries = list(hass.config_entries.async_entries(DOMAIN))
    except Exception:  # noqa: BLE001 - no entry list -> no exclusions
        return set()

    return {
        (DOMAIN, str(candidate_entry_id))
        for workspace in entries
        if (candidate_entry_id := getattr(workspace, "entry_id", None))
        and candidate_entry_id != account_entry_id
        and conf_entry_kind(getattr(workspace, "data", None)) == ENTRY_KIND_WORKSPACE
    }


def _device_identifiers(device: Any) -> set[tuple[str, str]]:
    """Return a device's identifiers as a plain set (defensive).

    Real HA hands out a ``set``/``frozenset``; a partially faked registry may
    hand out ``None`` or something uniterable - both resolve to an empty set so
    no caller has to guard the type itself.
    """
    identifiers = getattr(device, "identifiers", None) or set()
    try:
        return set(identifiers)
    except TypeError:  # pragma: no cover - defensive for foreign doubles
        return set()


def _is_workspace_device(device: Any, excluded: set[tuple[str, str]]) -> bool:
    """Return whether ``device`` carries a pure workspace identifier."""
    return bool(_device_identifiers(device) & excluded)


def _go_gauge_entry_ids(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Return the entry ids of every go_gauge ConfigEntry of this domain.

    Fallback guard of the device hand-over (review follow-up): a fallback target
    is adopted only while its CURRENT ownership set consists exclusively of this
    integration's entries. ``async_update_device(new_config_entry_id=...)``
    REPLACES the whole set, and HA then removes every entity whose
    ``config_entry_id`` is no longer listed on the device - a device that is
    (also) owned by a foreign integration would lose those entities
    irrecoverably.

    Fail-closed on purpose: when the entry list cannot be read, only the account
    entry itself counts as ours, so a device owned by anything else is never
    adopted.
    """
    allowed: set[str] = set()
    account_entry_id = getattr(entry, "entry_id", None)
    if account_entry_id:
        allowed.add(str(account_entry_id))

    try:
        entries = list(hass.config_entries.async_entries(DOMAIN))
    except Exception:  # noqa: BLE001 - no entry list -> only the account entry
        return allowed

    for candidate in entries:
        candidate_entry_id = getattr(candidate, "entry_id", None)
        if candidate_entry_id:
            allowed.add(str(candidate_entry_id))
    return allowed


def _is_go_gauge_owned(device: Any, allowed_entry_ids: set[str]) -> bool:
    """Return whether every owner of ``device`` is a go_gauge entry.

    A device without owners counts as adoptable: it has no ownership set to
    replace, so the hand-over cannot take anything away. An unreadable ownership
    set is NOT adoptable (fail-closed - see :func:`_go_gauge_entry_ids`).
    """
    owners = getattr(device, "config_entries", None) or set()
    try:
        owner_ids = {str(owner) for owner in owners}
    except TypeError:  # pragma: no cover - defensive for foreign doubles
        return False
    return owner_ids <= allowed_entry_ids


def _device_has_dependencies(ent_reg: Any, dev_reg: Any, device_id: str) -> bool:
    """Return whether a device still has entities or child devices.

    Checked against ALL entities (disabled ones included): a disabled collision
    loser still points at its old device, and removing that device would orphan
    it. ``via_device_id`` covers HA >= 2026.9 child devices (plan §3.3 step 3b).

    The child scan goes through :func:`_device_entries`, which enumerates the
    registry without the deprecated mapping access (see there) - the supported
    path that keeps working after 2027.9.
    """
    for candidate in ent_reg.entities.values():
        if getattr(candidate, "device_id", None) == device_id:
            return True
    for child in _device_entries(dev_reg):
        if getattr(child, "via_device_id", None) == device_id:
            return True
    return False


def _merge_duplicate_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    ent_reg: Any,
    dev_reg: Any,
    candidate_device_ids: set[str],
    canonical_device_id: str | None,
) -> None:
    """Remove EMPTY duplicate devices, report the rest (plan §3.3 step 3b).

    Runs AFTER the entity sweep on purpose: only then is a legacy device really
    empty, and only then may it disappear. A duplicate that still has entities
    or child devices is left in place with an ``account_device_not_empty``
    repair - never deleted, never silently re-parented (plan §11.1 R5).

    Pure workspace devices are excluded up front (see
    ``_workspace_device_identifiers``): their entities are not account entities and
    their device is not an adoption artifact.
    """
    excluded_identifiers = _workspace_device_identifiers(hass, entry)
    duplicates = candidate_device_ids - {canonical_device_id}
    not_empty: list[str] = []

    for duplicate_id in sorted(duplicates):
        device = None
        try:
            device = dev_reg.async_get(duplicate_id)
        except Exception:  # noqa: BLE001 - treat unknown as gone
            device = None
        if device is None:
            continue
        if _is_workspace_device(device, excluded_identifiers):
            _LOGGER.debug(
                "Go Gauge: device %s belongs to a workspace - never a duplicate",
                duplicate_id,
            )
            continue

        if _device_has_dependencies(ent_reg, dev_reg, duplicate_id):
            not_empty.append(duplicate_id)
            label = str(getattr(device, "name", None) or duplicate_id)
            report.errors.append(
                f"device {duplicate_id}: not empty - left in place"
            )
            _create_adoption_issue(
                hass,
                "account_device_not_empty",
                placeholders={"device": label},
            )
            continue

        try:
            dev_reg.async_remove_device(duplicate_id)
        except Exception as err:  # noqa: BLE001 - report, never abort
            report.errors.append(f"device {duplicate_id}: remove failed ({err})")
            _LOGGER.warning(
                "Go Gauge: removing the duplicate account device %s failed (%s)",
                duplicate_id,
                err,
            )
            continue
        report.merged_devices.append(duplicate_id)

    if report.merged_devices:
        _create_adoption_issue(
            hass,
            "account_device_merged",
            placeholders={"device": ", ".join(report.merged_devices)},
        )
    else:
        _delete_adoption_issue(hass, "account_device_merged")

    if not not_empty:
        _delete_adoption_issue(hass, "account_device_not_empty")


def _device_exists(dev_reg: Any, device_id: str) -> bool:
    """Return whether ``device_id`` still resolves to a device after a write.

    ``DeviceRegistry.async_update_device`` returns ``None`` for an unknown id -
    a move that never happened must not be reported as ``device_moved``. An
    unavailable lookup counts as "exists": the write succeeded and we merely
    cannot verify it (older/partial registry double), so the caller is not
    pushed into a false negative.
    """
    try:
        return dev_reg.async_get(device_id) is not None
    except Exception:  # noqa: BLE001 - unverifiable is not a failure
        return True


def _move_account_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    dev_reg: Any,
    candidate_device_ids: set[str],
) -> None:
    """Hand the account device over to the account entry - the LAST step.

    Target resolution (plan §3.3 step 3c): the device carrying
    ``(DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)``; if it does not exist yet, the
    best-ranked surviving candidate device (already owned by the account entry
    first, then lowest id). If neither exists, nothing is written: HA creates the
    device on the platform forward.

    A WORKSPACE device is never a valid fallback target (review blocker fix):
    ``async_update_device(new_config_entry_id=...)`` REPLACES the whole
    ownership set, and HA then removes every entity whose ``config_entry_id`` is
    no longer listed on the device. Handing the workspace device over would take
    its usage entities with it - irrecoverable data loss. Candidate devices
    carrying a workspace identifier are therefore skipped; if that leaves no
    target, ``device_moved`` stays ``None`` and nothing is written.

    A candidate owned by a FOREIGN integration is skipped for the same reason
    (review follow-up): replacing its ownership set would delete the entities of
    that integration. Only a device whose ``config_entries`` are entirely
    go_gauge entries is adopted - see ``_go_gauge_entry_ids``. Whenever that
    check cannot be satisfied with certainty, nothing is written and HA creates
    the account device with the platform forward instead.

    ``new_config_entry_id`` replaces the whole ownership set. An HA version
    without that keyword raises ``TypeError``; the guard then raises a repair
    issue and keeps the integration running (plan §11.1 R1) - it never raises.

    The fallback survivors are enumerated through :func:`_device_entries`,
    which never uses the deprecated mapping access on the registry's ``devices``
    collection (removed in HA 2027.9). Filtering and the deterministic
    best-rank sort are unchanged.
    """
    target = _canonical_device(dev_reg)
    if target is None:
        excluded_identifiers = _workspace_device_identifiers(hass, entry)
        allowed_owner_ids = _go_gauge_entry_ids(hass, entry)
        survivors = []
        for device in _device_entries(dev_reg):
            device_id = getattr(device, "id", None)
            if device_id not in candidate_device_ids:
                continue
            if _is_workspace_device(device, excluded_identifiers):
                continue
            if not _is_go_gauge_owned(device, allowed_owner_ids):
                _LOGGER.debug(
                    "Go Gauge: device %s is (also) owned by a foreign "
                    "integration (%s) - never a fallback move target",
                    device_id,
                    sorted(str(owner) for owner in
                           (getattr(device, "config_entries", None) or set())),
                )
                continue
            survivors.append(device)
        if survivors:
            target = min(
                survivors,
                key=lambda device: (
                    entry.entry_id
                    not in (getattr(device, "config_entries", None) or set()),
                    str(getattr(device, "id", "")),
                ),
            )

    if target is None:
        _LOGGER.debug(
            "Go Gauge: no account device to hand over - HA creates it with the "
            "platform forward"
        )
        return

    target_id = str(getattr(target, "id", ""))
    owners = getattr(target, "config_entries", None) or set()
    if entry.entry_id in owners:
        _delete_adoption_issue(hass, "adoption_unsupported")
        return

    try:
        updated = dev_reg.async_update_device(
            target_id, new_config_entry_id=entry.entry_id
        )
    except TypeError as err:
        # HA without `new_config_entry_id` (plan §11.1 R1).
        report.errors.append(f"device {target_id}: transfer unsupported ({err})")
        _LOGGER.warning(
            "Go Gauge: this Home Assistant version cannot hand the account "
            "device over (%s) - the account entities keep working, please "
            "update Home Assistant",
            err,
        )
        _create_adoption_issue(
            hass,
            "adoption_unsupported",
            placeholders={"error": str(err)},
        )
    except Exception as err:  # noqa: BLE001 - adoption must never raise
        report.errors.append(f"device {target_id}: transfer failed ({err})")
        _LOGGER.warning(
            "Go Gauge: handing the account device %s over failed (%s)",
            target_id,
            err,
        )
    else:
        # Only report a move that really happened: an unknown id makes
        # ``async_update_device`` return ``None``, and HA may have dropped the
        # device between the lookup and the write.
        if updated is not None and _device_exists(dev_reg, target_id):
            report.device_moved = target_id
            _delete_adoption_issue(hass, "adoption_unsupported")
        else:
            _LOGGER.debug(
                "Go Gauge: the account device %s vanished during the hand-over "
                "(%r) - not reported as moved",
                target_id,
                updated,
            )


def async_remove_legacy_model_settings(hass: HomeAssistant) -> int:
    """Remove the pre-2.0.0 workspace model controls from the entity registry.

    Plan §4.7 (Breaking, but deliberate): ``auto_update_models`` and
    ``models_refresh_minutes`` are global account settings now. Leaving the old
    ``<workspace>_auto_update_models`` / ``<workspace>_models_refresh_minutes``
    registry entries behind would show dead controls that write nothing.
    They are REMOVED (not migrated, no value transfer) - the canonical
    ``account_*`` ids are explicitly exempt.

    Returns the number of removed entries and is idempotent: a second pass
    finds no candidate and removes nothing. Every removal is guarded on its
    own, so one failing entry cannot abort the adoption (plan §4.6).

    HA semantics: ``RegistryEntry.platform`` is the integration domain
    (``go_gauge``) and ``RegistryEntry.domain`` is the entity domain
    (``switch``/``number``). The predicates below reflect that order.
    """
    removed = 0
    try:
        ent_reg = er.async_get(hass)
        candidates = list(ent_reg.entities.values())
    except Exception as err:  # noqa: BLE001 - removal is best effort
        _LOGGER.debug(
            "Go Gauge: legacy model-setting removal skipped (%s)", err
        )
        return 0

    for candidate in candidates:
        try:
            # HA field names (see _canonical_blocker): ``platform`` is the
            # integration domain, ``domain`` is the entity domain. Checking
            # them the other way round removed nothing at all.
            if getattr(candidate, "platform", None) != DOMAIN:
                continue
            if getattr(candidate, "domain", None) not in {"switch", "number"}:
                continue
            unique_id = getattr(candidate, "unique_id", None)
            if not isinstance(unique_id, str):
                continue
            if unique_id in _CANONICAL_MODEL_SETTING_IDS:
                continue
            if not unique_id.endswith(_LEGACY_MODEL_SETTING_SUFFIXES):
                continue
            entity_id = getattr(candidate, "entity_id", None)
            if entity_id is None:
                continue
            ent_reg.async_remove(entity_id)
            removed += 1
        except Exception as err:  # noqa: BLE001 - never break the adoption
            _LOGGER.warning(
                "Go Gauge: removing the legacy model setting %s failed (%s)",
                getattr(candidate, "entity_id", "?"),
                err,
            )

    if removed:
        _LOGGER.info(
            "Go Gauge: removed %d legacy workspace model setting(s) - the "
            "controls live on the account entry now",
            removed,
        )
    return removed


def _adopt_registries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    ent_reg: Any,
    dev_reg: Any,
) -> None:
    """Run the adoption phases in their mandatory order (plan §3.3 step 3).

    ENTITIES first, then the duplicate-device merge, then the device hand-over,
    and finally the removal of the legacy workspace model controls.
    The order is not cosmetic: moving/removing a device while entities still
    point at it would take those entities with it (plan §4.5, §11.3 V2).

    Ordering INVARIANT (review fix M1): a single failed entity write stops the
    pass BEFORE any device write. ``report.errors`` non-empty means the sweep is
    incomplete, and an entity may still carry the old ``config_entry_id``;
    handing the device over would then replace the ownership set and HA would
    DELETE that entity (the E2E-B1 cascade). So no merge, no device move and no
    legacy-setting removal happen in that state - ``merged_devices``,
    ``device_moved`` and ``removed_settings`` stay empty/``0``, and the next
    start repeats the whole pass idempotently.
    """
    # Lazy import on purpose: see ``_adoption_issue_ids`` - the offline test
    # loader only resolves sibling modules that are already in ``sys.modules``,
    # so a module-level import here would force every existing test file that
    # loads ``account.py`` to preload ``account_logic`` as well.
    from .account_logic import (
        ACCOUNT_ENTITY_KINDS,
        PLATFORM_BY_KIND,
        canonical_unique_id,
    )

    # ``PLATFORM_BY_KIND`` maps a kind to its ENTITY domain ("sensor",
    # "binary_sensor") - NOT to an integration platform. The sweep filters on
    # ``RegistryEntry.domain`` with it and on ``RegistryEntry.platform`` with
    # ``DOMAIN`` (see _canonical_blocker for the HA semantics trap).

    device = _canonical_device(dev_reg)
    device_id = str(getattr(device, "id", "")) if device is not None else None

    candidate_device_ids = _sweep_entities(
        hass,
        entry,
        report,
        ent_reg,
        kinds=ACCOUNT_ENTITY_KINDS,
        entity_domain_by_kind=PLATFORM_BY_KIND,
        canonical_unique_id=canonical_unique_id,
        device_id=device_id,
    )

    # Review fix M1: no device write while the sweep is incomplete. See the
    # ordering invariant in the docstring - the next run retries idempotently.
    if report.errors:
        _LOGGER.warning(
            "Go Gauge: account adoption stopped after the entity sweep (%d "
            "error(s)) - no device merge, no device hand-over and no legacy "
            "setting removal this pass; the next start retries idempotently",
            len(report.errors),
        )
        return

    _merge_duplicate_devices(
        hass,
        entry,
        report,
        ent_reg,
        dev_reg,
        candidate_device_ids,
        device_id,
    )

    _move_account_device(hass, entry, report, dev_reg, candidate_device_ids)

    # Last step (plan §3.3 step 3d / §4.7): the legacy workspace controls are
    # removed only AFTER the device hand-over, so the account device never
    # inherits an entity that is about to disappear.
    report.removed_settings = async_remove_legacy_model_settings(hass)


async def async_adopt_account_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> AdoptionReport:
    """Move the five catalog entities onto the account entry - never raises.

    Plan §3.3/§4: this is the ONE-TIME (idempotent) registry migration that runs
    at the start of every account setup and therefore on every v7 -> v8 upgrade,
    workspace reload and HA start. It performs the whole registry/device work
    BEFORE the platform forward, because HA then finds the existing
    registry entries through their canonical ``unique_id`` and keeps
    ``entity_id``, history, area, labels and the device assignment (plan §3.3
    step 3e). Running it after the forward would create a second generation of
    entities instead of adopting the existing ones.

    Order, strictly (each step is a conjunction of predicates whose violation is
    the ONLY trigger of the corresponding write, plan §4.5):

    0. take the shared ``DATA_ADOPTION_LOCK`` (serialises HA start, import flow
       and workspace setup; plan §7.2) and resolve both registries,
    1. collect the candidates per kind and remember their devices,
    2. sweep the entities per kind (rename -> config_entry_id -> device_id),
       or disable the losers of a canonical-unique_id collision,
    3. remove duplicate devices - only when empty - and report the rest,
    4. hand the canonical device over to the account entry,
    5. converge the ``adoption_failed`` repair and persist the report in
       ``hass.data[DOMAIN][DATA_ADOPTION_REPORT]``.

    NEVER raises (plan §2.4/M5, §4.6, §11.1 R6): an adoption problem -
    registry drift, a concurrent workspace removal, an HA version without
    ``new_config_entry_id`` - must not fail the account setup, which in turn must
    not take the workspaces down. Problems end up in ``report.errors`` plus a
    repair issue and are retried idempotently on the next start. The function is
    a no-op (plus a debug log) when ``hass.data`` is not a plain dict, e.g. in
    the legacy MagicMock-based tests.
    """
    report = AdoptionReport()

    try:
        data = _domain_data(hass, create=True)
        if data is None:
            _LOGGER.debug(
                "Go Gauge: account adoption skipped - hass.data is not a dict"
            )
            return report

        lock = data.setdefault(DATA_ADOPTION_LOCK, asyncio.Lock())
        async with lock:
            try:
                _adopt_registries(
                    hass, entry, report, er.async_get(hass), dr.async_get(hass)
                )
            except Exception as err:  # noqa: BLE001 - never break the setup
                _LOGGER.warning(
                    "Go Gauge: adopting the account entities failed unexpectedly "
                    "(%s) - the setup continues, the next start retries "
                    "idempotently",
                    err,
                )
                report.errors.append(f"adoption: {err}")

            # Step 5 - all bookkeeping inside the lock, so two concurrent
            # callers cannot interleave "report written" and "issue converged".
            data[DATA_ADOPTION_REPORT] = report
            if report.errors:
                _create_adoption_issue(
                    hass,
                    "adoption_failed",
                    placeholders={"count": str(len(report.errors))},
                )
            else:
                _delete_adoption_issue(hass, "adoption_failed")
    except Exception as err:  # noqa: BLE001 - outermost safety net
        _LOGGER.warning(
            "Go Gauge: account adoption failed unexpectedly (%s) - the setup "
            "continues, the next start retries idempotently",
            err,
        )
        report.errors.append(f"adoption: {err}")

    return report


async def _async_shutdown_coordinator(coordinator: Any) -> None:
    """Shut the coordinator's own scheduled refresh down - best effort (M2).

    Home Assistant calls ``DataUpdateCoordinator.async_shutdown`` itself only
    for a coordinator constructed WITH a ``config_entry``. ``AccountCoordinator``
    is not (it owns two independent clocks, plan §6.2), so HA never shuts it
    down and the 5 min publish tick would keep running after an unload or a
    removal. The caller therefore stops the fetch timer first and then calls
    this helper - both clocks have to be ended explicitly.

    Tolerant on purpose: an older HA version, a test double without
    ``async_shutdown`` or a failing shutdown must never break an unload/remove
    (cleanup is best effort and the entry is going away anyway). The return
    value is awaited only when it is awaitable, so a ``@callback``-style
    implementation works as well as the real coroutine.
    """
    shutdown = getattr(coordinator, "async_shutdown", None)
    if not callable(shutdown):
        return
    try:
        result = shutdown()
        if inspect.isawaitable(result):
            await result
    except Exception as err:  # noqa: BLE001 - cleanup is best effort
        _LOGGER.debug(
            "Go Gauge: shutting the account coordinator down failed (%s)", err
        )


async def _async_teardown_account_runtime(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: Any
) -> None:
    """Drop the account runtime again after a failed setup step (M1/M2).

    The setup registered the coordinator under both ``hass.data[DOMAIN]`` keys
    BEFORE the adoption ran. When setup has to report ``False`` (SETUP_RETRY)
    that runtime must not survive: a live publish tick, a live fetch timer and a
    stale adoption report behind a retrying entry would be exactly the
    half-initialised state this fix is about.

    Order: stop the fetch timer, shut the coordinator down (the publish tick -
    HA does not do that for a coordinator without ``config_entry``), then clear
    the registry keys. ``DATA_ACCOUNT_KEY`` is only popped while it still points
    at THIS coordinator (identity or ``account.entry is entry``), so a foreign
    object another subsystem placed there is never dropped. Never raises.
    """
    try:
        await coordinator.async_stop_fetch_timer()
    except Exception as err:  # noqa: BLE001 - cleanup is best effort
        _LOGGER.debug(
            "Go Gauge: stopping the account fetch timer during teardown failed "
            "(%s)",
            err,
        )
    await _async_shutdown_coordinator(coordinator)

    try:
        data = _domain_data(hass)
        if data is None:
            return
        data.pop(entry.entry_id, None)
        account = data.get(DATA_ACCOUNT_KEY)
        if account is coordinator or getattr(account, "entry", None) is entry:
            data.pop(DATA_ACCOUNT_KEY, None)
        data.pop(DATA_ADOPTION_REPORT, None)
    except Exception as err:  # noqa: BLE001 - teardown must never raise
        _LOGGER.debug(
            "Go Gauge: clearing the account runtime keys during teardown failed "
            "(%s)",
            err,
        )


async def async_setup_account_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Set up the account entry: options seed, runtime, adoption, platforms.

    Strict order (plan §3.1/§3.2/§3.3, implementation step 13/14):

    1. seed the account options BEFORE the coordinator exists - a v1.x
       installation keeps its model settings in the workspace options, and the
       very first runtime must already see them (``_seed_account_options``),
    2. construct the coordinator and register it in ``hass.data[DOMAIN]`` under
       BOTH keys (``entry.entry_id`` AND ``DATA_ACCOUNT_KEY``, the SAME object -
       plan §7.3),
    3. run the tolerant publish tick (``async_refresh``, no HTTP),
    4. run the entity adoption (registry/device work, legacy-setting removal),
    5a. check the adoption report: a non-empty ``report.errors`` tears the
       runtime down again and returns ``False`` BEFORE any platform forward -
       the entry goes into ``SETUP_RETRY`` and the next start repeats the
       idempotent adoption (review fix M1). No entity is forwarded and no device
       is moved in that state, so the HA "device move deletes an un-adopted
       entity" cascade (E2E-B1) cannot happen,
    5. forward ``ACCOUNT_PLATFORMS`` - **this call deliberately propagates**:
       HA must see its own platform errors, and nothing (registry work, adoption)
       is left half-done at this point because everything before it is
       idempotent and already persisted,
    6. one-time first fetch only when there is no catalog block yet, then arm
       the fetch timer,
    7. return ``True`` - or ``False`` when the runtime could not be constructed
       (the entry then goes into ``SETUP_RETRY``; see the note below).

    Why the adoption runs BEFORE the forward (plan §3.3 step 3e): HA re-binds
    the existing registry entries through their now canonical ``unique_id`` and
    keeps ``entity_id``, history, area and labels. Forwarding first would create
    a second generation of catalog entities.

    Everything BEFORE the forward is guarded (plan §2.4/M5, §11.1 R6): a
    registry problem or a failing publish/fetch/timer must not break the account
    setup. There are exactly two exceptions, both returning ``False``
    (SETUP_RETRY, HA retries with backoff; the workspace entries are never
    affected):

    * a coordinator that could not be CONSTRUCTED (review fix M5) - ``True``
      would leave the entry in ``LOADED`` with no runtime at all ("loaded but
      empty"), with entities but no coordinator until the next manual reload,
    * an adoption pass with ``report.errors`` (review fix M1) - the registry
      migration is incomplete, so the entry must not be forwarded; the runtime
      is torn down again (:func:`_async_teardown_account_runtime`) before
      ``False`` is returned.
    """
    coordinator: AccountCoordinator | None = None
    adoption_report: AdoptionReport | None = None
    try:
        data = _domain_data(hass, create=True)
        if data is None:
            # Test-double fallback ONLY: a real Home Assistant always hands out
            # a plain ``hass.data`` dict. The offline doubles use a MagicMock
            # there, and for those setup keeps reporting success without a
            # coordinator (nothing on a real instance reaches this branch).
            _LOGGER.warning(
                "Go Gauge: account runtime not registered - hass.data is not a "
                "dict (test-double fallback); setup reports success without a "
                "coordinator, real Home Assistant always provides a dict"
            )
            return True

        # 1. Options BEFORE the coordinator: the constructor reads them once.
        _seed_account_options(hass, entry)

        # 2. Coordinator + registry (same object under both keys, plan §7.3).
        try:
            coordinator = AccountCoordinator(hass, entry)
        except Exception as err:  # noqa: BLE001 - M5: caught here, False below
            coordinator = None
            _LOGGER.warning(
                "Go Gauge: creating the account coordinator for %s failed (%s) - "
                "setup returns False so Home Assistant retries the entry with "
                "backoff; the workspace entries are unaffected",
                entry.entry_id,
                err,
            )
        if coordinator is not None:
            data[entry.entry_id] = coordinator
            data[DATA_ACCOUNT_KEY] = coordinator

            # 3. Publish tick (no HTTP). Must already see the seeded options.
            try:
                await coordinator.async_refresh()
            except Exception as err:  # noqa: BLE001 - must not fail the setup
                _LOGGER.warning(
                    "Go Gauge: first account publish failed (%s) - continuing", err
                )

            # 4. Entity adoption (never raises, doubly guarded here).
            try:
                adoption_report = await async_adopt_account_entities(hass, entry)
            except Exception as err:  # noqa: BLE001 - second net
                _LOGGER.warning(
                    "Go Gauge: account entity adoption failed (%s) - continuing "
                    "with the account runtime, the next start retries",
                    err,
                )
    except Exception as err:  # noqa: BLE001 - outermost safety net (M5)
        _LOGGER.warning(
            "Go Gauge: account setup failed unexpectedly before the platform "
            "forward (%s) - setup returns False so Home Assistant retries the "
            "entry with backoff; the workspace entries are unaffected",
            err,
        )
        coordinator = None

    if coordinator is None:
        # M5 (review fix): no runtime -> the entry is BROKEN, not "loaded but
        # empty". ``False`` sends the entry to SETUP_RETRY (HA retries with
        # backoff); the workspace entries are untouched by it.
        _LOGGER.warning(
            "Go Gauge: account entry %s has no runtime - setup returns False so "
            "Home Assistant retries it; the workspace entries are unaffected",
            getattr(entry, "entry_id", "?"),
        )
        return False

    # 5a. Adoption report (review fix M1): a non-empty ``errors`` means the
    #     registry migration is INCOMPLETE - an entity may still carry the old
    #     config_entry_id. Forwarding platforms or moving the account device in
    #     that state would delete that entity (E2E-B1 cascade). So the runtime
    #     is torn down completely and setup reports False: the entry goes into
    #     SETUP_RETRY, HA retries with backoff and the next run repeats the
    #     idempotent adoption. The workspace entries are never affected.
    if adoption_report is not None and adoption_report.errors:
        _LOGGER.warning(
            "Go Gauge: account adoption of %s reported %d error(s) - setup "
            "returns False so Home Assistant retries the entry with backoff; "
            "no platform is forwarded and the account device is not moved in "
            "this state",
            getattr(entry, "entry_id", "?"),
            len(adoption_report.errors),
        )
        await _async_teardown_account_runtime(hass, entry, coordinator)
        return False

    # 5. Platform forward - INTENTIONALLY outside every guard: Home Assistant
    #    must see a failing forward (plan §2.4/§3.3 step 3e).
    await hass.config_entries.async_forward_entry_setups(entry, ACCOUNT_PLATFORMS)

    # 6. One-time first fetch + timer - both guarded, retried by the next tick.
    if coordinator.models_block is None:
        try:
            await coordinator.async_fetch_now()
        except Exception as err:  # noqa: BLE001 - second net, fetch never raises
            _LOGGER.warning(
                "Go Gauge: first account catalog fetch failed (%s) - "
                "continuing, the timer retries",
                err,
            )
    try:
        await coordinator.async_start_fetch_timer()
    except Exception as err:  # noqa: BLE001 - no timer beats a broken setup
        _LOGGER.warning(
            "Go Gauge: starting the account fetch timer failed (%s) - "
            "automatic catalog fetches stay off until the next reload",
            err,
        )

    # 7. Done.
    _LOGGER.info(
        "Go Gauge: account entry %s loaded (catalog fetch every %s min)",
        entry.entry_id,
        coordinator.fetch_interval_minutes,
    )
    return True


async def async_unload_account_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> bool:
    """Unload the account entry: platforms first, then timer and runtime.

    The platform unload decides everything: only when HA reports ``unload_ok``
    are the fetch timer stopped, the coordinator shut down, dropped from
    ``hass.data[DOMAIN]`` and ``DATA_ACCOUNT_KEY`` cleared - but only while that
    key still points at THIS coordinator (identity check) or at a coordinator of
    THIS entry (``account.entry is entry``). A foreign object another subsystem
    placed there is never dropped by an account unload.

    Why the explicit shutdown (review fix M2): Home Assistant only calls
    ``DataUpdateCoordinator.async_shutdown`` for a coordinator constructed WITH a
    ``config_entry``. ``AccountCoordinator`` has none, so stopping the fetch
    timer alone would leave the 5 min publish tick running. Order: fetch timer
    first, then the coordinator shutdown.

    The ``DATA_ACCOUNT_KEY`` fallback mirrors :func:`async_remove_account_entry`
    (review follow-up): a runtime that is only reachable through that key still
    has its fetch timer stopped, so an unload can never leave a live polling
    timer behind.

    ``DATA_ADOPTION_REPORT`` is dropped too: the report belongs to a loaded
    account runtime, and a stale report would otherwise survive into the next
    setup/diagnostics export.

    A failing platform unload is NOT swallowed into ``True``: HA uses the return
    value to decide whether the entry may be unloaded/removed, so an exception
    and a ``False`` both return ``False`` (plan §3.4).
    """
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(
            entry, ACCOUNT_PLATFORMS
        )
    except Exception as err:  # noqa: BLE001 - report the failed unload
        _LOGGER.warning(
            "Go Gauge: unloading the account platforms of %s failed (%s)",
            getattr(entry, "entry_id", "?"),
            err,
        )
        return False

    if not unload_ok:
        return False

    try:
        data = _domain_data(hass)
        if data is not None:
            coordinator = data.pop(entry.entry_id, None)
            account = data.get(DATA_ACCOUNT_KEY)
            if coordinator is None and account is not None and (
                getattr(account, "entry", None) is entry
            ):
                coordinator = account
            if coordinator is not None:
                try:
                    await coordinator.async_stop_fetch_timer()
                except Exception as err:  # noqa: BLE001 - cleanup is best effort
                    _LOGGER.debug(
                        "Go Gauge: stopping the account fetch timer failed (%s)",
                        err,
                    )
                # Review fix M2: HA only calls ``async_shutdown`` itself for a
                # coordinator constructed WITH a ``config_entry``. The account
                # coordinator has none, so its publish tick must be ended here
                # explicitly - after the fetch timer, so no clock survives.
                await _async_shutdown_coordinator(coordinator)
            if account is not None and (
                account is coordinator or getattr(account, "entry", None) is entry
            ):
                data.pop(DATA_ACCOUNT_KEY, None)
            data.pop(DATA_ADOPTION_REPORT, None)
    except Exception as err:  # noqa: BLE001 - unload must never raise
        _LOGGER.warning(
            "Go Gauge: cleaning up the unloaded account entry %s failed (%s)",
            getattr(entry, "entry_id", "?"),
            err,
        )
    return True


async def async_remove_account_entry(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle removal of the account entry: delete repairs, clean ``hass.data``.

    All adoption repair issues use stable ids (plan §4.4), so they are deleted
    here rather than being left behind as orphaned repairs of a removed entry.
    Deleting is idempotent - removing a second time (or removing without any
    adoption having run) is a no-op.

    Remaining ``hass.data`` leftovers are cleaned defensively: the per-entry key,
    the account key if it still belongs to THIS entry, and a stale adoption
    report. HA has normally unloaded the entry already; nothing here may raise.
    The fetch timer is additionally stopped here (review fix M4): a removal that
    is not preceded by a successful platform unload must not leave a live timer
    behind. The coordinator is shut down as well (review fix M2): without a
    ``config_entry`` HA never calls ``async_shutdown`` itself, so the publish
    tick would otherwise poll a removed entry forever.
    """
    _delete_adoption_issues(hass)
    try:
        data = _domain_data(hass)
        if data is not None:
            coordinator = data.pop(entry.entry_id, None)
            account = data.get(DATA_ACCOUNT_KEY)
            if coordinator is None and account is not None and (
                getattr(account, "entry", None) is entry
            ):
                coordinator = account
            if coordinator is not None:
                try:
                    await coordinator.async_stop_fetch_timer()
                except Exception as err:  # noqa: BLE001 - cleanup is best effort
                    _LOGGER.debug(
                        "Go Gauge: stopping the account fetch timer on removal "
                        "failed (%s)",
                        err,
                    )
                # Review fix M2: same as in the unload - no ``config_entry``
                # means no HA-driven ``async_shutdown``; the publish tick would
                # poll a removed entry forever without this call.
                await _async_shutdown_coordinator(coordinator)
            if account is not None and (
                account is coordinator or getattr(account, "entry", None) is entry
            ):
                data.pop(DATA_ACCOUNT_KEY, None)
            data.pop(DATA_ADOPTION_REPORT, None)
    except Exception as err:  # noqa: BLE001 - removal must never raise
        _LOGGER.warning(
            "Go Gauge: cleaning up after the removed account entry %s failed "
            "unexpectedly (%s)",
            getattr(entry, "entry_id", "?"),
            err,
        )
    _LOGGER.debug("Go Gauge: account entry %s removed", entry.entry_id)
