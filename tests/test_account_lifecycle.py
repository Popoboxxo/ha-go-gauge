#!/usr/bin/env python3
"""Account entry lifecycle: setup, unload and remove (2.0.0, phase B).

Plan: ``docs/PLAN-account-entry-2.0.0.md`` §2.4 (account runtime), §3.1 (HA
start), §3.4 (deleting the account entry), §3.5 (disabled entry), §3.6
(workspace reload/reauth), §6.2 (two clocks), §6.4 (live effect), §7.2/§7.3
(singleton + setup order matrix), §8.3 (M5 "Account-Startup/Fehlerisolation",
B2 "kein Besitzerwechsel") and §8.4 (this file).

Scope of phase B: the REAL setup/unload/remove body of ``account.py``. NOT in
scope (phase C, plan steps 8-14): platform forwarding, entities, adoption
sweeps, legacy-setting removal and consumer rewiring. The musts below therefore
assert the *absence* of forwarding too.

Deliberately NOT duplicated here: the negative cases of
``async_ensure_account_entry`` (create via import flow, loaded = no-op, disabled
= no re-create, no enabled workspace = create nothing). They already live in
``tests/test_init.py::TestEnsureAccountEntry`` - plan §7.2/§3.5 are covered
there; this file only pins the runtime lifecycle.

The shared Home-Assistant module fakes live in ``tests/conftest.py``; this file
re-installs them right before the imports (other test modules replace the
``homeassistant.*`` sys.modules entries at collection time) and then loads the
integration modules in dependency order. ``account_logic`` is loaded explicitly
because ``account.py`` imports it lazily when deleting the adoption repair
issues (the offline loader only resolves siblings already in ``sys.modules``).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


def _load(name: str, path: str):
    """Dynamically import a module from file, bypassing sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Re-assert the shared fakes immediately before the integration imports:
# ``account.py`` uses the coordinator double, the ConfigEntryState double, the
# counting timer double and the issue-registry dispatchers.
conftest.install_ha_stubs()
_CONFIG_ENTRY_STATE = conftest.install_config_entry_state_stub()
conftest.install_coordinator_stub()
conftest.install_timer_stub()
conftest.install_issue_registry_stub()

const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
account_logic = _load("go_gauge.account_logic", str(BASE / "account_logic.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
account = _load("go_gauge.account", str(BASE / "account.py"))

# The four non-per-kind adoption issue ids (plan §4.4/§4.7).
GLOBAL_ADOPTION_ISSUE_IDS = (
    "account_device_merged",
    "account_device_not_empty",
    "adoption_unsupported",
    "adoption_failed",
)

ALL_ADOPTION_ISSUE_IDS = tuple(
    f"account_entity_collision_{kind}" for kind in account_logic.ACCOUNT_ENTITY_KINDS
) + GLOBAL_ADOPTION_ISSUE_IDS


class _FakeAccountEntry:
    """Minimal account ConfigEntry double with the attributes the code reads.

    Not :class:`conftest.StrictConfigEntry`: the lifecycle needs ``state``,
    ``disabled_by`` and ``async_on_unload`` (defensive - phase B registers no
    unload callback, phase C will). ``entry_id``/``options`` stay writable, the
    code never mutates ``data``.
    """

    def __init__(
        self,
        *,
        entry_id: str = "account-entry-1",
        options: dict[str, Any] | None = None,
        disabled_by: str | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.unique_id = const.ACCOUNT_ENTRY_UNIQUE_ID
        self.title = const.ENTITY_NAME_ACCOUNT
        self.data = {const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT}
        self.options = dict(options or {})
        self.state = _CONFIG_ENTRY_STATE.LOADED
        self.disabled_by = disabled_by
        self.unload_callbacks: list[Any] = []

    def async_on_unload(self, callback: Any) -> None:
        """Record an unload callback (HA's registration API)."""
        self.unload_callbacks.append(callback)


@pytest.fixture
def hass():
    """``HomeAssistant`` double with a REAL ``data`` dict and mock plumbing."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_update_entry = MagicMock(return_value=True)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass


@pytest.fixture
def timer():
    """Install a FRESH counting timer double for one test."""
    return conftest.install_timer_stub()


def _install_client(monkeypatch, *, error: Exception | None = None) -> MagicMock:
    """Inject a fake API client - no HTTP leaves the process.

    Both symbols matter: the coordinator constructor asks
    ``async_get_clientsession(hass)`` for a session and passes it to
    ``OpenCodeGoApiClient``, so patching the class/session on the ``account``
    module replaces the real client without touching ``api.py``.
    """
    client = MagicMock()
    if error is not None:
        client.fetch_models = AsyncMock(side_effect=error)
    else:
        client.fetch_models = AsyncMock(return_value=["glm-5", "glm-5-free"])
    monkeypatch.setattr(account, "OpenCodeGoApiClient", lambda _session: client)
    monkeypatch.setattr(
        account, "async_get_clientsession", lambda _hass: MagicMock()
    )
    return client


def _coordinator(hass, entry: _FakeAccountEntry):
    """Return the coordinator the setup registered for `entry`."""
    return hass.data[const.DOMAIN][entry.entry_id]


# ============================================================================
# Setup: hass.data registration, first publish/fetch, timer, isolation
# ============================================================================

@pytest.mark.asyncio
async def test_setup_registers_coordinator_under_both_keys(hass, monkeypatch):
    """[PLAN2-E2][PLAN2-B2] One object under entry_id AND DATA_ACCOUNT_KEY."""
    _install_client(monkeypatch)
    entry = _FakeAccountEntry()

    result = await account.async_setup_account_entry(hass, entry)

    data = hass.data[const.DOMAIN]
    coordinator = data[entry.entry_id]
    assert result is True
    assert isinstance(coordinator, account.AccountCoordinator)
    # Same object under both keys (plan §7.3, last paragraph) - a copy would
    # leave diagnostics and entities with diverging runtimes.
    assert data[const.DATA_ACCOUNT_KEY] is coordinator


@pytest.mark.asyncio
async def test_setup_creates_no_owner_keys(hass, monkeypatch):
    """[PLAN2-B2] Setup adds no legacy owner key to ``hass.data[DOMAIN]``.

    Plan §8.3 (B2/M1): there is no owner hand-over any more, so the account
    setup must not resurrect ``_catalog_owner``-style keys. Since phase B's
    adoption step (plan step 10) the setup additionally creates the adoption
    bookkeeping entries - the lock shared with ``async_ensure_account_entry``
    and the last ``AdoptionReport`` for the diagnostics export - so those two
    foreign keys are expected and explicitly allowed here.
    """
    _install_client(monkeypatch)
    entry = _FakeAccountEntry()

    await account.async_setup_account_entry(hass, entry)

    assert set(hass.data[const.DOMAIN]) == {
        entry.entry_id,
        const.DATA_ACCOUNT_KEY,
        const.DATA_ADOPTION_LOCK,
        const.DATA_ADOPTION_REPORT,
    }
    assert "_catalog_owner" not in hass.data[const.DOMAIN]


@pytest.mark.asyncio
async def test_setup_starts_fetch_timer(hass, monkeypatch, timer):
    """[PLAN2-E4] Setup leaves exactly ONE live fetch timer at 60 min.

    The ``finally`` block of ``async_fetch_now`` already re-arms the timer, so
    ``async_start_fetch_timer`` replaces that arm instead of adding a second
    clock: the double therefore records two arms and one cancel - net one live
    timer (the "1 Arm" of plan §6.2). Unload cancels it.
    """
    _install_client(monkeypatch)
    entry = _FakeAccountEntry()

    await account.async_setup_account_entry(hass, entry)

    assert set(timer.calls) == {timedelta(minutes=60)}
    assert timer.unsub_calls == 1  # fetch_now's arm was replaced, not kept
    assert len(timer.calls) - timer.unsub_calls == 1  # exactly one live timer

    await account.async_unload_account_entry(hass, entry)

    assert timer.unsub_calls == 2  # the live arm was cancelled on unload


@pytest.mark.asyncio
async def test_setup_first_fetch_only_when_no_block(hass, monkeypatch):
    """[PLAN2-E2] Fresh coordinator (no models_block) -> exactly one fetch."""
    client = _install_client(monkeypatch)
    entry = _FakeAccountEntry()

    await account.async_setup_account_entry(hass, entry)

    client.fetch_models.assert_awaited_once()
    assert _coordinator(hass, entry).models_block is not None


@pytest.mark.asyncio
async def test_setup_skips_fetch_when_block_present(hass, monkeypatch):
    """[PLAN2-E2] Existing catalog block -> no first fetch (plan §3.6).

    A real coordinator always starts with ``models_block is None``, so "block
    already present" cannot be reached through the constructor. The class
    property is therefore patched for this test: it is the exact attribute the
    setup reads before deciding to fetch, and patching it at class level makes
    the freshly constructed coordinator report a block.
    """
    monkeypatch.setattr(
        account.AccountCoordinator,
        "models_block",
        property(lambda _self: {"model_count_live": 1, "models": []}),
    )
    client = _install_client(monkeypatch)
    entry = _FakeAccountEntry()

    result = await account.async_setup_account_entry(hass, entry)

    assert result is True
    client.fetch_models.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_survives_fetch_error(hass, monkeypatch):
    """[PLAN2-M5] A failing first fetch never fails the setup.

    Plan §8.3 (M5): setup True, outcome ``fail``, error message kept, and the
    publish path still succeeded (``last_update_success is True`` because
    ``async_fetch_now`` publishes its snapshot in ``finally``).
    """
    _install_client(monkeypatch, error=RuntimeError("api down"))
    entry = _FakeAccountEntry()

    result = await account.async_setup_account_entry(hass, entry)

    coordinator = _coordinator(hass, entry)
    assert result is True
    assert coordinator.last_models_outcome == "fail"
    assert coordinator.last_error is not None and "api down" in coordinator.last_error
    assert coordinator.last_update_success is True


@pytest.mark.asyncio
async def test_setup_returns_false_when_coordinator_construction_fails(
    hass, monkeypatch
):
    """[PLAN2-M5] A broken constructor yields setup ``False`` (SETUP_RETRY).

    Review fix M5: ``True`` would leave the entry LOADED with no runtime at all
    ("loaded but empty") until a manual reload. ``False`` makes HA retry with
    backoff; the workspace entries are unaffected.
    """

    def _boom(_hass, _entry):
        raise RuntimeError("constructor boom")

    monkeypatch.setattr(account, "AccountCoordinator", _boom)
    entry = _FakeAccountEntry()
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    result = await account.async_setup_account_entry(hass, entry)

    assert result is False
    assert hass.data[const.DOMAIN] == {}
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_first_refresh_call(hass, monkeypatch):
    """[PLAN2-M5] The first publish is ``async_refresh``, never
    ``async_config_entry_first_refresh`` (that one raises and would fail setup).
    """
    _install_client(monkeypatch)
    entry = _FakeAccountEntry()

    await account.async_setup_account_entry(hass, entry)

    coordinator = _coordinator(hass, entry)
    assert coordinator.first_refresh_calls == 0
    assert coordinator.refresh_calls == 1


@pytest.mark.asyncio
async def test_setup_forwards_account_platforms_once(hass, monkeypatch):
    """[PLAN2-E4][PLAN2-M1] Phase C forwards ACCOUNT_PLATFORMS exactly once.

    The forward is the call that re-binds the adopted registry entries. It must
    run exactly once and only after the adoption (plan §3.3 step 3e).
    """
    _install_client(monkeypatch)
    entry = _FakeAccountEntry()

    await account.async_setup_account_entry(hass, entry)

    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, const.ACCOUNT_PLATFORMS
    )


# ============================================================================
# Unload
# ============================================================================

@pytest.mark.asyncio
async def test_unload_removes_coordinator_and_account_key(hass, monkeypatch, timer):
    """[PLAN2-E2] Unload drops both keys, stops the timer, returns ``True``."""
    _install_client(monkeypatch)
    entry = _FakeAccountEntry()
    await account.async_setup_account_entry(hass, entry)
    hass.data[const.DOMAIN][const.DATA_ADOPTION_REPORT] = {"renamed": []}

    result = await account.async_unload_account_entry(hass, entry)

    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, const.ACCOUNT_PLATFORMS
    )
    data = hass.data[const.DOMAIN]
    assert result is True
    assert entry.entry_id not in data
    assert const.DATA_ACCOUNT_KEY not in data
    assert const.DATA_ADOPTION_REPORT not in data
    assert timer.unsub_calls == 2  # one replacement arm + the unload cancel


@pytest.mark.asyncio
async def test_unload_keeps_foreign_account_key(hass, monkeypatch):
    """[PLAN2-B2] A foreign object under DATA_ACCOUNT_KEY survives the unload."""
    _install_client(monkeypatch)
    entry = _FakeAccountEntry()
    await account.async_setup_account_entry(hass, entry)

    foreign = object()
    hass.data[const.DOMAIN][const.DATA_ACCOUNT_KEY] = foreign

    result = await account.async_unload_account_entry(hass, entry)

    assert result is True
    assert hass.data[const.DOMAIN].get(const.DATA_ACCOUNT_KEY) is foreign
    assert entry.entry_id not in hass.data[const.DOMAIN]


@pytest.mark.asyncio
async def test_unload_is_tolerant_without_coordinator(hass):
    """[PLAN2-M5] Unload after a failed/no setup is a clean ``True``."""
    entry = _FakeAccountEntry()

    result = await account.async_unload_account_entry(hass, entry)

    assert result is True
    assert hass.data == {}


@pytest.mark.asyncio
async def test_unload_stops_the_timer_via_the_account_key_fallback(
    hass, monkeypatch, timer
):
    """[REVIEW-2.0.0-FOLLOWUP3] A runtime only under DATA_ACCOUNT_KEY is torn down.

    Unload mirrors :func:`async_remove_account_entry`: when the coordinator is
    missing under ``entry.entry_id`` but reachable through ``DATA_ACCOUNT_KEY``
    and it belongs to THIS entry, the fetch timer is stopped and the key is
    cleared. Otherwise an unload could leave a live polling timer behind.
    """
    entry = _FakeAccountEntry()
    coordinator, _started, _release = _register_coordinator(
        hass, entry, monkeypatch=monkeypatch
    )
    await coordinator.async_start_fetch_timer()
    data = hass.data[const.DOMAIN]
    data.pop(entry.entry_id, None)  # only DATA_ACCOUNT_KEY remains
    data[const.DATA_ADOPTION_REPORT] = {"renamed": []}

    assert await account.async_unload_account_entry(hass, entry) is True

    assert coordinator._stopped is True
    assert coordinator._unsub_fetch is None
    assert timer.unsub_calls == 1  # exactly the unload cancel
    assert const.DATA_ACCOUNT_KEY not in data
    assert const.DATA_ADOPTION_REPORT not in data
    assert entry.entry_id not in data


def _register_coordinator(hass, entry, *, slow_fetch: bool = False, monkeypatch=None):
    """Build a real coordinator, register it under both keys and return it.

    Used by the timer-leak regressions: they need a coordinator whose fetch can
    be blocked, so the entry can be torn down while a fetch is still in flight.
    """

    async def _slow():
        started.set()
        await release.wait()
        return ["glm-5"]

    started = asyncio.Event()
    release = asyncio.Event()

    client = MagicMock()
    client.fetch_models = _slow if slow_fetch else AsyncMock(return_value=["glm-5"])
    if monkeypatch is not None:
        monkeypatch.setattr(account, "OpenCodeGoApiClient", lambda _session: client)
        monkeypatch.setattr(
            account, "async_get_clientsession", lambda _hass: MagicMock()
        )

    coordinator = account.AccountCoordinator(hass, entry)
    data = hass.data.setdefault(const.DOMAIN, {})
    data[entry.entry_id] = coordinator
    data[const.DATA_ACCOUNT_KEY] = coordinator
    return coordinator, started, release


@pytest.mark.asyncio
async def test_unload_during_a_running_fetch_does_not_rearm_the_timer(
    hass, monkeypatch, timer
):
    """[REVIEW-2.0.0-M4] A fetch in flight at unload must not leak a timer.

    Regression: the fetch's ``finally`` block re-armed the fetch timer, so an
    entry that was unloaded (or removed) while a fetch was running kept polling
    forever. ``async_stop_fetch_timer`` latches ``_stopped`` and the re-arm is a
    no-op until the next ``async_start_fetch_timer``.
    """
    entry = _FakeAccountEntry()
    coordinator, started, release = _register_coordinator(
        hass, entry, slow_fetch=True, monkeypatch=monkeypatch
    )
    await coordinator.async_start_fetch_timer()
    arms_before = len(timer.calls)

    fetch_task = asyncio.ensure_future(coordinator.async_fetch_now())
    await started.wait()

    assert await account.async_unload_account_entry(hass, entry) is True

    release.set()
    await asyncio.wait_for(fetch_task, timeout=1)

    assert len(timer.calls) == arms_before, "no new timer arm after the unload"
    assert coordinator._unsub_fetch is None
    assert coordinator._stopped is True


@pytest.mark.asyncio
async def test_remove_stops_a_leftover_fetch_timer(hass, monkeypatch, timer):
    """[REVIEW-2.0.0-M4] Removal defensively stops a still-armed fetch timer."""
    entry = _FakeAccountEntry()
    coordinator, _started, _release = _register_coordinator(
        hass, entry, monkeypatch=monkeypatch
    )
    await coordinator.async_start_fetch_timer()
    assert coordinator._unsub_fetch is not None

    await account.async_remove_account_entry(hass, entry)

    assert coordinator._stopped is True
    assert coordinator._unsub_fetch is None
    assert timer.unsub_calls == 1


# ============================================================================
# Remove: repair issues + hass.data leftovers
# ============================================================================

@pytest.mark.asyncio
async def test_remove_deletes_all_adoption_issues(hass):
    """[PLAN2-E2] Removal deletes every stable adoption issue id (plan §4.4).

    Stable ids are what make the cleanup complete: the per-kind collision ids
    plus the four global ones. An unrelated issue must stay untouched.
    """
    registry = conftest.install_issue_registry_stub()
    for issue_id in ALL_ADOPTION_ISSUE_IDS:
        registry.async_create_issue(hass, const.DOMAIN, issue_id, severity="warning")
    registry.async_create_issue(hass, const.DOMAIN, "unrelated_issue")
    entry = _FakeAccountEntry()

    await account.async_remove_account_entry(hass, entry)

    assert set(ALL_ADOPTION_ISSUE_IDS) <= set(registry.deleted)
    assert set(registry.issues) == {"unrelated_issue"}
    assert "unrelated_issue" not in registry.deleted


@pytest.mark.asyncio
async def test_remove_is_idempotent(hass):
    """[PLAN2-E2] A second removal raises nothing and deletes nothing new."""
    registry = conftest.install_issue_registry_stub()
    entry = _FakeAccountEntry()

    await account.async_remove_account_entry(hass, entry)
    await account.async_remove_account_entry(hass, entry)

    assert registry.issues == {}
    # Deleting a missing issue is a recorded no-op in the double - both passes
    # walk the same stable id list, so nothing can pile up.
    assert len(registry.deleted) == 2 * len(ALL_ADOPTION_ISSUE_IDS)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
