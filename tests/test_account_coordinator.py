#!/usr/bin/env python3
"""AccountCoordinator tests - publish tick, fetch timer, backoff (2.0.0).

Plan: ``docs/PLAN-account-entry-2.0.0.md`` §2.4 (coordinator shape), §6.2 (two
clocks), §6.3 (backoff table), §6.4 (live effect without reload), §6.5
(visibility), §8.3 (review gaps "Backoff" / "Publish-Tick ohne HTTP"), §8.4
(``tests/test_account_coordinator.py``) and §11.1 R7/R9 (5 min publish latency
is accepted, the tick never performs HTTP).

Scope: the coordinator ONLY. No platform forwarding, no entities, no adoption -
those are phase C. All HTTP is replaced by an ``AsyncMock`` client, so the tests
stay offline and instant.

The shared Home-Assistant module fakes live in ``tests/conftest.py``; this file
re-installs them right before the import (other test modules replace the
``homeassistant.*`` sys.modules entries at collection time) and then loads the
integration modules in dependency order, exactly like
``tests/test_coordinator_parsing.py`` does.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

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


# Re-assert the shared fakes immediately before the integration imports: the
# coordinator double, the ConfigEntryState double, the counting timer double and
# the issue registry are all bound/used by ``account.py``.
conftest.install_ha_stubs()
_CONFIG_ENTRY_STATE = conftest.install_config_entry_state_stub()
conftest.install_coordinator_stub()
conftest.install_timer_stub()
conftest.install_issue_registry_stub()

const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
coordinator_module = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))
account = _load("go_gauge.account", str(BASE / "account.py"))

SNAPSHOT_KEYS = {
    "fetched_at",
    "models_block",
    "last_models_attempt",
    "last_models_ok",
    "last_models_outcome",
    "last_error",
    "auto_fetch",
    "base_interval_minutes",
    "fetch_interval_minutes",
    "backoff_active",
    "backoff_failures",
}


@pytest.fixture
def timer():
    """Install a FRESH counting timer double for one test (unsub is per-test)."""
    return conftest.install_timer_stub()


def _make_coordinator(
    *,
    options: dict[str, Any] | None = None,
    models: list[str] | None = None,
):
    """Build a real AccountCoordinator with a fake entry, hass and API client."""
    entry = conftest.StrictConfigEntry(
        unique_id=const.ACCOUNT_ENTRY_UNIQUE_ID,
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT},
        options=dict(options or {}),
        entry_id="account-entry-1",
        title="Go Gauge Konto",
    )
    entry.state = _CONFIG_ENTRY_STATE.LOADED
    hass = MagicMock()
    hass.data = {}
    # ``async_get_clientsession`` is HA machinery (the fake module hands out a
    # no-arg class) - the coordinator only needs *a* session object.
    with patch.object(account, "async_get_clientsession", lambda _hass: MagicMock()):
        coordinator = account.AccountCoordinator(hass, entry)
    client = MagicMock()
    client.fetch_models = AsyncMock(return_value=["glm-5"] if models is None else models)
    coordinator._client = client
    return coordinator, client, hass


# ============================================================================
# Publish tick: 5 minutes, no HTTP
# ============================================================================

def test_publish_tick_is_five_minutes():
    """[PLAN2-E4] The publish tick is exactly ACCOUNT_PUBLISH_TICK_MINUTES (5 min)."""
    coordinator, _client, _hass = _make_coordinator()

    assert const.ACCOUNT_PUBLISH_TICK_MINUTES == 5
    assert coordinator.update_interval == timedelta(minutes=5)


@pytest.mark.asyncio
async def test_publish_tick_does_not_fetch():
    """[PLAN2-E4][PLAN2-M2] The publish tick re-renders the snapshot, never HTTP.

    Plan §6.2/§11.1 R7: the 5-minute tick only publishes (TTL expiry becomes
    visible); the API is polled exclusively by ``async_fetch_now``.
    """
    coordinator, client, _hass = _make_coordinator()

    await coordinator.async_refresh()

    client.fetch_models.assert_not_awaited()
    assert coordinator.last_update_success is True
    assert SNAPSHOT_KEYS <= set(coordinator.data)
    # Not even the coroutine is created: the tick has no client access at all.
    assert client.fetch_models.call_count == 0


def test_snapshot_keys_exact():
    """[PLAN2-E4] The snapshot has EXACTLY the eleven documented keys."""
    coordinator, _client, _hass = _make_coordinator()

    snapshot = coordinator._snapshot(datetime.now(timezone.utc))

    assert set(snapshot) == SNAPSHOT_KEYS
    assert len(snapshot) == 11
    # Untouched coordinator: no evidence yet, auto-fetch on, base interval 60.
    assert snapshot["models_block"] is None
    assert snapshot["last_models_ok"] is None
    assert snapshot["last_models_attempt"] is None
    assert snapshot["last_models_outcome"] is None
    assert snapshot["auto_fetch"] is True
    assert snapshot["base_interval_minutes"] == 60
    assert snapshot["fetch_interval_minutes"] == 60
    assert snapshot["backoff_active"] is False
    assert snapshot["backoff_failures"] == 0


# ============================================================================
# Fetch timer (second clock)
# ============================================================================

@pytest.mark.asyncio
async def test_fetch_timer_uses_base_interval(timer):
    """[PLAN2-E4] Starting the fetch timer arms it with the base interval (60 min)."""
    coordinator, _client, _hass = _make_coordinator()
    # Setup deliberately does not arm anything: __init__ only reads options.
    assert timer.calls == []

    await coordinator.async_start_fetch_timer()

    assert timer.calls == [timedelta(minutes=60)]


@pytest.mark.asyncio
async def test_set_fetch_interval_reschedules(timer):
    """[PLAN2-E4] set_fetch_interval(10) cancels the old timer and re-arms at 10 min."""
    coordinator, _client, _hass = _make_coordinator()
    await coordinator.async_start_fetch_timer()

    coordinator.set_fetch_interval(10)

    assert timer.unsub_calls == 1
    assert timer.calls == [timedelta(minutes=60), timedelta(minutes=10)]
    assert coordinator.data["base_interval_minutes"] == 10
    assert coordinator.data["fetch_interval_minutes"] == 10


@pytest.mark.asyncio
async def test_set_auto_fetch_false_cancels_timer(timer):
    """[PLAN2-E4] Auto-fetch off cancels the timer; the publish tick keeps running."""
    coordinator, _client, _hass = _make_coordinator()
    await coordinator.async_start_fetch_timer()

    coordinator.set_auto_fetch(False)

    assert timer.unsub_calls == 1
    assert coordinator._unsub_fetch is None
    assert coordinator.fetch_interval_minutes is None
    assert coordinator.data["auto_fetch"] is False
    assert coordinator.data["fetch_interval_minutes"] is None
    assert coordinator.update_interval == timedelta(minutes=5)

    # Switching back on re-arms with the base interval again.
    coordinator.set_auto_fetch(True)
    assert timer.calls == [timedelta(minutes=60), timedelta(minutes=60)]
    assert coordinator.data["fetch_interval_minutes"] == 60


@pytest.mark.asyncio
async def test_stop_fetch_timer_is_idempotent(timer):
    """[PLAN2-E4] Stopping twice unsubscribes once and never raises."""
    coordinator, _client, _hass = _make_coordinator()
    await coordinator.async_start_fetch_timer()

    await coordinator.async_stop_fetch_timer()
    await coordinator.async_stop_fetch_timer()

    assert timer.unsub_calls == 1
    assert coordinator._unsub_fetch is None


# ============================================================================
# async_fetch_now: the ONLY HTTP path
# ============================================================================

@pytest.mark.asyncio
async def test_async_fetch_now_fetches_and_stores_block(timer):
    """[PLAN2-E4] A successful fetch stores the catalog block and resets backoff."""
    coordinator, client, _hass = _make_coordinator(models=["glm-5"])

    await coordinator.async_fetch_now()

    client.fetch_models.assert_awaited_once()
    assert coordinator.models_block is not None
    assert coordinator.models_block["model_count_live"] == 1
    assert coordinator.data["models_block"] is coordinator.models_block
    assert coordinator.last_models_outcome == "ok"
    assert coordinator.data["last_models_outcome"] == "ok"
    assert coordinator.data["backoff_failures"] == 0
    assert coordinator.data["backoff_active"] is False
    assert coordinator.data["last_error"] is None
    assert coordinator.data["last_models_ok"] is not None
    assert coordinator.data["last_models_attempt"] is not None


@pytest.mark.asyncio
async def test_concurrent_fetch_calls_do_one_http_round_trip(timer):
    """[REVIEW-2.0.0-MINOR] Parallel fetch (button + timer) -> one HTTP call.

    Without the single-flight guard two overlapping fetches increment the
    backoff twice on failure and publish racing snapshots.
    """
    coordinator, client, _hass = _make_coordinator()
    started = asyncio.Event()
    release = asyncio.Event()
    http_calls: list[int] = []

    async def _slow_fetch():
        http_calls.append(1)
        started.set()
        await release.wait()
        return ["glm-5"]

    client.fetch_models = _slow_fetch

    first = asyncio.ensure_future(coordinator.async_fetch_now())
    await started.wait()

    # Second caller (the refresh button) arrives while the first is in flight.
    second = asyncio.ensure_future(coordinator.async_fetch_now())
    for _ in range(5):  # let the second task reach its busy check
        await asyncio.sleep(0)
    assert second.done(), "the concurrent call is skipped, not queued"

    release.set()
    await asyncio.wait_for(first, timeout=1)
    await second

    assert len(http_calls) == 1
    assert coordinator.last_models_outcome == "ok"


# ============================================================================
# Backoff (§6.3)
# ============================================================================
@pytest.mark.asyncio
async def test_backoff_doubles_from_base(timer):
    """[PLAN2-E4] base 10 -> 20 -> 40 -> 60 -> 60 (last one capped)."""
    coordinator, client, _hass = _make_coordinator(
        options={const.CONF_MODELS_REFRESH_MINUTES: 10}
    )
    client.fetch_models = AsyncMock(side_effect=RuntimeError("HTTP 403 blocked"))
    assert coordinator.fetch_interval_minutes == 10

    intervals = []
    for _ in range(4):
        await coordinator.async_fetch_now()
        intervals.append(coordinator.fetch_interval_minutes)

    assert intervals == [20, 40, 60, 60]
    assert coordinator.data["backoff_failures"] == 4
    assert coordinator.data["backoff_active"] is True


@pytest.mark.asyncio
async def test_backoff_caps_at_60(timer):
    """[PLAN2-E4] base 60 (already at the cap) never exceeds 60."""
    coordinator, client, _hass = _make_coordinator()
    client.fetch_models = AsyncMock(side_effect=RuntimeError("network down"))

    for _ in range(3):
        await coordinator.async_fetch_now()

    assert coordinator.fetch_interval_minutes == 60
    assert const.ACCOUNT_BACKOFF_MAX_MINUTES == 60


@pytest.mark.asyncio
async def test_backoff_skipped_above_60(timer):
    """[PLAN2-E4] base 120 > cap is NEVER shortened, even with failures."""
    coordinator, client, _hass = _make_coordinator(
        options={const.CONF_MODELS_REFRESH_MINUTES: 120}
    )
    client.fetch_models = AsyncMock(side_effect=RuntimeError("network down"))

    for _ in range(3):
        await coordinator.async_fetch_now()

    assert coordinator.fetch_interval_minutes == 120
    assert coordinator.data["base_interval_minutes"] == 120


@pytest.mark.asyncio
async def test_backoff_resets_on_success(timer):
    """[PLAN2-E4] A success resets failures to 0 -> next fetch in base interval."""
    coordinator, client, _hass = _make_coordinator(
        options={const.CONF_MODELS_REFRESH_MINUTES: 10}
    )
    client.fetch_models = AsyncMock(side_effect=RuntimeError("network down"))
    await coordinator.async_fetch_now()
    await coordinator.async_fetch_now()
    assert coordinator.fetch_interval_minutes == 40

    client.fetch_models = AsyncMock(return_value=["glm-5"])
    await coordinator.async_fetch_now()

    assert coordinator.data["backoff_failures"] == 0
    assert coordinator.fetch_interval_minutes == 10
    assert coordinator.data["backoff_active"] is False


# ============================================================================
# Error isolation (§6.3, §8.3 "Account-Startup/Fehlerisolation")
# ============================================================================

@pytest.mark.asyncio
async def test_fetch_error_keeps_old_block(timer):
    """[PLAN2-M5] A failed fetch keeps the last catalog and records the error."""
    coordinator, client, _hass = _make_coordinator(models=["glm-5"])
    await coordinator.async_fetch_now()
    before = coordinator.models_block
    assert before is not None

    client.fetch_models = AsyncMock(side_effect=RuntimeError("HTTP 403 blocked"))
    await coordinator.async_fetch_now()  # must not raise

    assert coordinator.models_block is before
    assert coordinator.data["models_block"] is before
    assert coordinator.last_models_outcome == "fail"
    assert coordinator.data["last_models_outcome"] == "fail"
    assert coordinator.data["last_error"] == "HTTP 403 blocked"
    assert coordinator.data["backoff_failures"] == 1


@pytest.mark.asyncio
async def test_fetch_error_never_raises_auth_failed(timer):
    """[PLAN2-M5] GoGaugeAuthError from the client is a plain failure, no reauth.

    The account entry carries no token (plan §6.3), so a 401/403 must never
    propagate as ``ConfigEntryAuthFailed`` - the coordinator keeps publishing.
    """
    coordinator, client, _hass = _make_coordinator()
    client.fetch_models = AsyncMock(
        side_effect=const.GoGaugeAuthError("HTTP 401 - token invalid/expired")
    )

    await coordinator.async_fetch_now()

    assert coordinator.last_update_success is True
    assert coordinator.data["last_models_outcome"] == "fail"
    assert coordinator.data["last_error"] == "HTTP 401 - token invalid/expired"
    assert coordinator.data["models_block"] is None


# ============================================================================
# Construction contract (§2.4, §8.3 "HA-Min 2026.8.0")
# ============================================================================

def test_no_config_entry_kwarg_and_no_first_refresh():
    """[PLAN2-M5] No ``config_entry=`` kwarg, no ``config_entry`` attr, no first refresh."""
    coordinator, _client, _hass = _make_coordinator()

    assert coordinator.first_refresh_calls == 0
    assert "config_entry" not in coordinator.init_kwargs
    assert not hasattr(coordinator, "config_entry")
    assert coordinator.name == "go_gauge_account"


def test_init_reads_options_and_clamps():
    """[PLAN2-E4] Options drive auto_fetch/base interval; values are clamped 1-1440."""
    coordinator, _client, _hass = _make_coordinator(
        options={
            const.CONF_AUTO_UPDATE_MODELS: False,
            const.CONF_MODELS_REFRESH_MINUTES: 99999,
        }
    )
    assert coordinator.auto_fetch is False
    assert coordinator.base_interval_minutes == const.ACCOUNT_MODELS_REFRESH_MAX
    assert coordinator.fetch_interval_minutes is None

    low, _client2, _hass2 = _make_coordinator(
        options={const.CONF_MODELS_REFRESH_MINUTES: 0}
    )
    assert low.base_interval_minutes == const.ACCOUNT_MODELS_REFRESH_MIN


# ============================================================================
# workspace_coordinators() - evidence source for phase C
# ============================================================================

def test_workspace_coordinators_filters_by_type():
    """[PLAN2-M2] Only GoGaugeCoordinator instances are returned, foreign keys dropped."""
    hass = MagicMock()
    with patch.object(
        coordinator_module, "async_get_clientsession", lambda _hass: MagicMock()
    ):
        workspace_1 = coordinator_module.GoGaugeCoordinator(hass, [])
        workspace_2 = coordinator_module.GoGaugeCoordinator(hass, [])
    hass.data = {
        const.DOMAIN: {
            "ws-1": workspace_1,
            "ws-2": workspace_2,
            const.DATA_ACCOUNT_KEY: object(),
            const.DATA_ADOPTION_LOCK: object(),
            "ws-3": "not a coordinator",
        }
    }

    result = account.workspace_coordinators(hass)

    assert set(result) == {"ws-1", "ws-2"}
    assert all(
        isinstance(item, coordinator_module.GoGaugeCoordinator)
        for item in result.values()
    )


def test_workspace_coordinators_tolerates_missing_data():
    """[PLAN2-M2] Missing/foreign ``hass.data`` yields an empty mapping, never a raise."""
    hass = MagicMock()
    hass.data = {}
    assert account.workspace_coordinators(hass) == {}

    broken = MagicMock()  # MagicMock.data is not a dict
    assert account.workspace_coordinators(broken) == {}

    hass_no_domain = MagicMock()
    hass_no_domain.data = {"other_domain": {}}
    assert account.workspace_coordinators(hass_no_domain) == {}
