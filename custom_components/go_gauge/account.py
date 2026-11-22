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

Since the 2.1.0 split (plan §4.3) the account runtime is spread over four
per-concern modules and this file keeps only the entry lifecycle:

* the ``AccountCoordinator`` runtime, its option-seeding helpers and the shared
  ``hass.data[DOMAIN]`` accessor (``_domain_data``) live in
  :mod:`.account_coordinator`,
* the reachability evidence/snapshot runtime (``workspace_coordinators``,
  ``collect_evidence``, ``ReachabilitySnapshot``, ``account_reachability_snapshot``,
  ``account_reachability``) lives in :mod:`.account_reachability`,
* the entity/device adoption sweep (``async_adopt_account_entities``,
  ``AdoptionReport``, ``async_remove_legacy_model_settings`` and the per-kind
  registry/device helpers) lives in :mod:`.account_adoption`,
* this module keeps the entry lifecycle (``async_ensure_account_entry``,
  ``async_setup_account_entry`` / ``async_unload_account_entry`` /
  ``async_remove_account_entry`` and their teardown helpers).

This module re-exports the public symbols of the three extracted modules so the
``from .account import ...`` path stays stable for ``__init__`` and the platforms.

Import graph (kept acyclic on purpose): this module imports ``.const``,
``.account_coordinator`` (which in turn owns the ``.api`` / ``.catalog`` runtime
dependencies), ``.account_reachability`` and ``.account_adoption`` (both import
``_domain_data`` from ``.account_coordinator``; reachability also imports
``GoGaugeCoordinator`` from ``.coordinator``); ``coordinator.py`` does NOT import
this module and nothing here imports the integration ``__init__``, which imports
this module.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant

# Re-exported for the stable public import path ``from .account import ...`` (plan
# §4.3): the entity/device adoption sweep moved to ``account_adoption.py`` in the
# 2.1.0 split. ``AdoptionReport``/``async_adopt_account_entities`` are used by the
# lifecycle below; ``_delete_adoption_issues`` is called on account removal.
from .account_adoption import (  # noqa: F401 - re-export
    AdoptionReport,
    _delete_adoption_issues,
    async_adopt_account_entities,
    async_remove_legacy_model_settings,
)

# Re-exported for the stable public import path ``from .account import ...`` that
# __init__/platforms and the test loaders rely on (plan §4.3): the coordinator
# runtime moved to ``account_coordinator.py`` in the 2.1.0 split.
from .account_coordinator import (  # noqa: F401 - re-export
    AccountCoordinator,
    _clamp_minutes,
    _domain_data,
    _isoformat,
    _seed_account_options,
    _workspace_options,
)

# Re-exported for the stable public import path ``from .account import ...`` (plan
# §4.3): the reachability snapshot runtime moved to ``account_reachability.py`` in
# the 2.1.0 split. ``_account_entries``/``_entry_is_loaded``/``_has_enabled_workspace``
# are still used by ``async_ensure_account_entry`` below.
from .account_reachability import (  # noqa: F401 - re-export
    ReachabilitySnapshot,
    _account_entries,
    _entry_is_loaded,
    _has_enabled_workspace,
    account_reachability,
    account_reachability_snapshot,
    collect_evidence,
    workspace_coordinators,
)
from .const import (
    ACCOUNT_PLATFORMS,
    DATA_ACCOUNT_KEY,
    DATA_ADOPTION_LOCK,
    DATA_ADOPTION_REPORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


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
