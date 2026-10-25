#!/usr/bin/env python3
"""Account-entry config flow tests (2.0.0 account split).

Plan: ``docs/PLAN-account-entry-2.0.0.md`` §7.1 (flow step), §7.2 (singleton),
§8.4 (``tests/test_account_flow.py``), §11.3 V1 (``async_init`` runs the flow to
completion).

Scope of this file: the IMPORT flow (phase A, plan step 5) and the account
options flow (plan step 13, §2.14/§6.4/§8.4). Platform/entity tests live in
``tests/test_account_platforms.py``; the adoption has its own module.

The shared Home-Assistant module fakes live centrally in ``tests/conftest.py``
(see its docstring); this file only re-installs them right before importing the
real modules - no local ``sys.modules`` stub block, matching
``tests/test_config_flow.py``.

``_probe_token`` / the base ``ConfigFlow`` machinery are Home Assistant
services: the flow object is instantiated for real and its base methods are
replaced by doubles, which is enough to assert the *data contract* of the
created entry.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol

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


# Other test modules run their own inline fake setup at collection time and
# replace the homeassistant.* sys.modules entries (alphabetical collection puts
# this file early), so re-assert the shared fakes immediately before the
# imports. ``account.py`` binds ``ConfigEntryState`` at import time and compares
# an entry's state against it -> the stable double must be in place first.
conftest.install_ha_stubs()
# The stable ``ConfigEntryState`` double - ``account.py`` compares the entry
# state against it, so the fake entry below uses the same object's values.
_CONFIG_ENTRY_STATE = conftest.install_config_entry_state_stub()
# The coordinator/timer/issue doubles are bound by ``account.py`` at import time
# (AccountCoordinator subclasses the coordinator, the fetch timer uses the
# counting interval double, the adoption issue helpers use the dispatchers).
conftest.install_coordinator_stub()
conftest.install_timer_stub()
conftest.install_issue_registry_stub()

const = _load("go_gauge.const", str(BASE / "const.py"))
config_flow = _load("go_gauge.config_flow", str(BASE / "config_flow.py"))
# ``account.py`` imports its siblings relatively; the offline ``_load`` loader
# only resolves modules already registered under their dotted ``go_gauge.*``
# name. Load the dependency chain first (same order as
# tests/test_account_lifecycle.py), otherwise a standalone run of this file
# fails with ``ModuleNotFoundError: No module named 'go_gauge'``.
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
account_logic = _load("go_gauge.account_logic", str(BASE / "account_logic.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
account = _load("go_gauge.account", str(BASE / "account.py"))


class _AbortFlow(Exception):
    """Local stand-in for ``homeassistant.data_entry_flow.AbortFlow``.

    The real class is what ``_abort_if_unique_id_configured`` raises; the
    offline fakes do not implement the flow machinery, so the double raises
    this and the test asserts only "propagates, no entry created".
    """


def _make_flow(*, abort: bool = False, create_result: dict | None = None):
    """Build a real ``GoGaugeConfigFlow`` with doubled HA base methods."""
    flow = config_flow.GoGaugeConfigFlow()
    flow.hass = MagicMock()
    flow.context = {"source": "import"}
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    if abort:
        flow._abort_if_unique_id_configured.side_effect = _AbortFlow(
            "already_configured"
        )
    flow.async_create_entry = MagicMock(
        return_value=create_result if create_result is not None
        else {"type": "create_entry"}
    )
    return flow


class TestImportCreatesAccountEntry:
    """[PLAN2-E2] The import flow creates the singleton account entry."""

    @pytest.mark.asyncio
    async def test_import_creates_account_entry(self):
        """[PLAN2-E2] Title/unique_id/data contract of the created entry.

        The account entry carries the constant title, the fixed unique_id and
        ONLY the ``entry_kind`` marker - no token, no workspace name.
        """
        flow = _make_flow()
        result = await flow.async_step_import(None)

        flow.async_set_unique_id.assert_awaited_once_with(
            const.ACCOUNT_ENTRY_UNIQUE_ID
        )
        assert const.ACCOUNT_ENTRY_UNIQUE_ID == "go_gauge_account"
        flow._abort_if_unique_id_configured.assert_called_once()
        flow.async_create_entry.assert_called_once()
        assert result == {"type": "create_entry"}

        kwargs = flow.async_create_entry.call_args.kwargs
        assert kwargs["title"] == "Go Gauge Konto"
        assert kwargs["title"] == const.ENTITY_NAME_ACCOUNT
        assert kwargs["data"] == {"entry_kind": "account"}
        assert kwargs["data"] == {
            const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT
        }
        # The account entry holds no secret and no workspace identity.
        assert "token" not in kwargs["data"]
        assert "workspace_name" not in kwargs["data"]

    @pytest.mark.asyncio
    async def test_import_aborts_when_already_configured(self):
        """[PLAN2-B3] A configured unique_id aborts instead of creating a twin."""
        flow = _make_flow(abort=True)

        with pytest.raises(_AbortFlow, match="already_configured"):
            await flow.async_step_import(None)

        flow.async_set_unique_id.assert_awaited_once_with(
            const.ACCOUNT_ENTRY_UNIQUE_ID
        )
        flow.async_create_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_does_no_http_probe(self):
        """[PLAN2-E2] No token means no probe: ``_probe_token`` stays untouched."""
        flow = _make_flow()
        with patch.object(
            config_flow, "_probe_token", new_callable=AsyncMock
        ) as probe, patch.object(config_flow, "async_get_clientsession") as session:
            await flow.async_step_import(None)

        probe.assert_not_awaited()
        session.assert_not_called()

    @pytest.mark.asyncio
    async def test_import_ignores_user_input(self):
        """[PLAN2-E2] An import payload can never reach the entry (no passthrough)."""
        flow = _make_flow()
        await flow.async_step_import({
            "token": "should-be-ignored",
            "workspace_name": "Ignored WS",
            "title": "Ignored Title",
        })

        kwargs = flow.async_create_entry.call_args.kwargs
        assert kwargs["title"] == const.ENTITY_NAME_ACCOUNT
        assert kwargs["data"] == {
            const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT
        }
        assert "should-be-ignored" not in str(kwargs)


class TestConfigFlowVersion:
    """[PLAN2-A1] One target version for account AND workspace entries."""

    def test_config_flow_version_is_eight(self):
        """[PLAN2-A1] ``GoGaugeConfigFlow.VERSION`` is 8 (entry_kind marker)."""
        assert config_flow.GoGaugeConfigFlow.VERSION == 8


def _account_entry(*, options: dict | None = None):
    """Return a minimal account ConfigEntry double."""
    return conftest.StrictConfigEntry(
        entry_id="account-entry",
        unique_id=const.ACCOUNT_ENTRY_UNIQUE_ID,
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT},
        options=dict(options or {}),
        title=const.ENTITY_NAME_ACCOUNT,
    )


class _FakeAccountCoordinator:
    """Records the two live setters the account options flow must call."""

    def __init__(self) -> None:
        self.auto_fetch = True
        self.base_interval_minutes = const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES
        self.auto_fetch_calls: list[bool] = []
        self.interval_calls: list[int] = []

    def set_auto_fetch(self, enabled: bool) -> None:
        self.auto_fetch_calls.append(bool(enabled))
        self.auto_fetch = bool(enabled)

    def set_fetch_interval(self, minutes: int) -> None:
        self.interval_calls.append(int(minutes))
        self.base_interval_minutes = int(minutes)


def _make_account_options_flow(
    *, options: dict | None = None, coordinator: object | None = None,
    under_entry_id: bool = False,
):
    """Build the real account options flow with doubled HA services.

    ``hass.data`` is a plain dict (the flow is defensive about that); the
    coordinator is registered under ``DATA_ACCOUNT_KEY`` or, when
    ``under_entry_id`` is set, only under the entry id (fallback path).
    """
    entry = _account_entry(options=options)
    flow = config_flow.GoGaugeAccountOptionsFlowHandler(entry)
    flow.hass = MagicMock()
    flow.hass.data = {}
    if coordinator is not None:
        key = entry.entry_id if under_entry_id else const.DATA_ACCOUNT_KEY
        flow.hass.data[const.DOMAIN] = {key: coordinator}
    # The account entry has no update listener - neither call may happen.
    flow.hass.config_entries.async_reload = MagicMock()
    flow.hass.config_entries.async_update_entry = MagicMock()
    flow.async_show_form = MagicMock(return_value={"type": "form"})
    flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})
    return flow, entry


def _shown_schema(flow) -> tuple[str, vol.Schema]:
    """Return (step_id, schema) of the last ``async_show_form`` call."""
    kwargs = flow.async_show_form.call_args.kwargs
    return kwargs["step_id"], kwargs["data_schema"]


class TestAccountOptionsFlow:
    """[PLAN2-E4] Account options flow: exactly two fields, live, no reload."""

    @pytest.mark.asyncio
    async def test_account_options_flow_has_exactly_two_fields(self):
        """[PLAN2-E4] ``step_id="account"``; defaults ON/60 without options.

        The rendered step id must be ``account`` (not ``init``) so the UI finds
        ``options.step.account.*``; the schema is limited to the two global
        account settings and falls back to the documented defaults.
        """
        flow, _ = _make_account_options_flow()
        result = await flow.async_step_init(None)

        assert result == {"type": "form"}
        step_id, schema = _shown_schema(flow)
        assert step_id == "account"
        assert {str(key) for key in schema.schema} == {
            const.CONF_AUTO_UPDATE_MODELS,
            const.CONF_MODELS_REFRESH_MINUTES,
        }
        assert schema({}) == {
            const.CONF_AUTO_UPDATE_MODELS: True,
            const.CONF_MODELS_REFRESH_MINUTES: 60,
        }

        configured, _ = _make_account_options_flow(options={
            const.CONF_AUTO_UPDATE_MODELS: False,
            const.CONF_MODELS_REFRESH_MINUTES: 15,
        })
        await configured.async_step_init(None)
        _, configured_schema = _shown_schema(configured)
        assert configured_schema({}) == {
            const.CONF_AUTO_UPDATE_MODELS: False,
            const.CONF_MODELS_REFRESH_MINUTES: 15,
        }

    @pytest.mark.asyncio
    async def test_account_options_flow_bounds_reject_out_of_range(self):
        """[PLAN2-E4] Interval 1..1440; 0 and 1441 are rejected by the schema."""
        flow, _ = _make_account_options_flow()
        await flow.async_step_account(None)
        _, schema = _shown_schema(flow)

        for value in (0, 1441, -5):
            with pytest.raises(vol.Invalid):
                schema({
                    const.CONF_AUTO_UPDATE_MODELS: True,
                    const.CONF_MODELS_REFRESH_MINUTES: value,
                })

        for value in (const.ACCOUNT_MODELS_REFRESH_MIN, const.ACCOUNT_MODELS_REFRESH_MAX):
            validated = schema({
                const.CONF_AUTO_UPDATE_MODELS: True,
                const.CONF_MODELS_REFRESH_MINUTES: value,
            })
            assert validated[const.CONF_MODELS_REFRESH_MINUTES] == value
        assert (const.ACCOUNT_MODELS_REFRESH_MIN, const.ACCOUNT_MODELS_REFRESH_MAX) == (
            1, 1440
        )

    @pytest.mark.asyncio
    async def test_account_options_flow_applies_live(self):
        """[PLAN2-E4] Submit drives the setters, persists, never reloads.

        Live use of the same setters as the switch/number entities is the whole
        point of §6.4: the account entry has no update listener, so a reload
        would never happen - the runtime must be updated in place.
        """
        coordinator = _FakeAccountCoordinator()
        flow, _ = _make_account_options_flow(coordinator=coordinator)
        payload = {
            const.CONF_AUTO_UPDATE_MODELS: False,
            const.CONF_MODELS_REFRESH_MINUTES: 15,
        }

        result = await flow.async_step_init(payload)

        assert result == {"type": "create_entry"}
        assert coordinator.auto_fetch is False
        assert coordinator.base_interval_minutes == 15
        assert coordinator.auto_fetch_calls == [False]
        assert coordinator.interval_calls == [15]
        flow.async_create_entry.assert_called_once_with(title="", data=payload)
        # No reload, and no premature async_update_entry (the OptionsFlowManager
        # persists the returned data itself).
        flow.hass.config_entries.async_reload.assert_not_called()
        flow.hass.config_entries.async_update_entry.assert_not_called()

    @pytest.mark.asyncio
    async def test_account_options_flow_without_coordinator(self):
        """[PLAN2-E4] Missing runtime is tolerated: persist, no exception."""
        flow, _ = _make_account_options_flow()
        payload = {
            const.CONF_AUTO_UPDATE_MODELS: False,
            const.CONF_MODELS_REFRESH_MINUTES: 15,
        }

        result = await flow.async_step_init(payload)

        assert result == {"type": "create_entry"}
        flow.async_create_entry.assert_called_once_with(title="", data=payload)
        flow.hass.config_entries.async_reload.assert_not_called()

    @pytest.mark.asyncio
    async def test_account_options_flow_finds_coordinator_under_entry_id(self):
        """[PLAN2-E4] ``entry.entry_id`` is the fallback registry key (§7.3)."""
        coordinator = _FakeAccountCoordinator()
        flow, _ = _make_account_options_flow(
            coordinator=coordinator, under_entry_id=True
        )

        await flow.async_step_init({
            const.CONF_AUTO_UPDATE_MODELS: False,
            const.CONF_MODELS_REFRESH_MINUTES: 30,
        })

        assert coordinator.auto_fetch is False
        assert coordinator.base_interval_minutes == 30

    def test_get_options_flow_branches_by_entry_kind(self):
        """[PLAN2-E4] Account -> account handler; workspace -> workspace one."""
        account_entry = _account_entry()
        workspace_entry = conftest.StrictConfigEntry(
            entry_id="ws-entry",
            unique_id=const.token_unique_id("tok"),
            data={
                "workspace_name": "WS",
                "token": "tok",
                const.CONF_ENTRY_KIND: const.ENTRY_KIND_WORKSPACE,
            },
        )

        account_flow = config_flow.GoGaugeConfigFlow.async_get_options_flow(
            account_entry
        )
        workspace_flow = config_flow.GoGaugeConfigFlow.async_get_options_flow(
            workspace_entry
        )

        assert isinstance(account_flow, config_flow.GoGaugeAccountOptionsFlowHandler)
        assert isinstance(workspace_flow, config_flow.GoGaugeOptionsFlowHandler)
        assert not isinstance(
            workspace_flow, config_flow.GoGaugeAccountOptionsFlowHandler
        )


class _FakeFlowManager:
    """Minimal ``hass.config_entries.flow`` double running the REAL flow.

    Mirrors the two HA behaviours the ensure logic relies on (plan §7.2, §11.3
    V1): ``async_init(..., source=import)`` runs the flow to its result, and an
    import entry is set up by HA right after creation. The created entry is a
    plain object with the fields ``account.py`` reads (``entry_id``,
    ``unique_id``, ``data``, ``disabled_by``, ``state``).
    """

    def __init__(self, entries: list, config_entries) -> None:
        self.entries = entries
        self.config_entries = config_entries
        self.init_calls: list[tuple[str, dict]] = []

    async def async_init(self, domain: str, context: dict | None = None):
        """Run ``async_step_import`` and finalize the flow like HA does."""
        context = dict(context or {})
        self.init_calls.append((domain, context))

        flow = config_flow.GoGaugeConfigFlow()
        flow.hass = self.config_entries.hass
        flow.context = context
        flow._unique_id = None

        async def _set_unique_id(unique_id: str) -> None:
            flow._unique_id = unique_id

        def _abort_if_configured() -> None:
            if any(
                getattr(entry, "unique_id", None) == flow._unique_id
                for entry in self.entries
            ):
                raise _AbortFlow("already_configured")

        def _create_entry(*, title: str, data: dict):
            return {"type": "create_entry", "title": title, "data": dict(data)}

        flow.async_set_unique_id = _set_unique_id
        flow._abort_if_unique_id_configured = _abort_if_configured
        flow.async_create_entry = _create_entry

        result = await flow.async_step_import(None)
        if result["type"] != "create_entry":
            return result

        entry = MagicMock()
        entry.entry_id = "account-entry"
        entry.unique_id = flow._unique_id
        entry.title = result["title"]
        entry.data = result["data"]
        entry.options = {}
        entry.disabled_by = None
        entry.state = _CONFIG_ENTRY_STATE.NOT_LOADED
        self.entries.append(entry)
        # HA sets an import entry up immediately (plan §7.2), which also marks
        # it LOADED - the ensure logic must see it as a no-op afterwards.
        if await self.config_entries.async_setup(entry.entry_id):
            entry.state = _CONFIG_ENTRY_STATE.LOADED
        return result


class TestEnsureWithRealFlow:
    """[PLAN2-V1] ``async_init`` runs the flow to completion (plan §11.3 V1)."""

    @pytest.mark.asyncio
    async def test_ensure_creates_entry_once(self):
        """[PLAN2-V1] First ensure creates exactly one entry; second is a no-op.

        This is the A2/§11.3 V1 evidence: the entry exists right after the
        ``await account.async_ensure_account_entry(hass)`` - i.e. the import
        flow is not merely started, it finished inside ``async_init``. The
        second call proves the re-check inside the lock (``async_entries``)
        prevents a duplicate.
        """
        entries: list = [conftest.StrictConfigEntry(
            entry_id="ws1", data={"token": "tok", "workspace_name": "WS"}
        )]
        hass = MagicMock()
        hass.data = {}
        config_entries = conftest.install_applying_config_entries(hass)
        config_entries.hass = hass
        config_entries.async_entries = MagicMock(
            side_effect=lambda domain: list(entries)
        )
        config_entries.async_setup = AsyncMock(return_value=True)
        flow_manager = _FakeFlowManager(entries, config_entries)
        config_entries.flow = flow_manager

        await account.async_ensure_account_entry(hass)

        assert len(entries) == 1 + 1  # the workspace + one account entry
        account_entries = [
            entry for entry in entries
            if getattr(entry, "unique_id", None) == const.ACCOUNT_ENTRY_UNIQUE_ID
        ]
        assert len(account_entries) == 1
        assert account_entries[0].data == {
            const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT
        }
        assert len(flow_manager.init_calls) == 1
        assert flow_manager.init_calls[0][0] == const.DOMAIN
        assert flow_manager.init_calls[0][1] == {"source": "import"}
        # The entry really went through HA's setup, and only once.
        config_entries.async_setup.assert_awaited_once_with("account-entry")

        # Second run: re-check inside the lock sees the existing entry.
        await account.async_ensure_account_entry(hass)

        assert len(entries) == 2
        assert len(flow_manager.init_calls) == 1
        config_entries.async_setup.assert_awaited_once_with("account-entry")


class TestEnsureAdoptionLockReentrancy:
    """[REVIEW-2.0.0-BLOCKER2] ``ensure`` must not hold the adoption lock.

    Regression: the import flow creates the account entry and HA runs its setup
    synchronously in the same call chain. That setup runs
    ``async_adopt_account_entities``, which takes the SAME non-reentrant
    ``DATA_ADOPTION_LOCK``. Holding the lock across ``flow.async_init`` therefore
    hard-deadlocked the first installation (and every re-create / reload of an
    unloaded account entry). The ensure now releases the lock before creating.
    """

    @pytest.mark.asyncio
    async def test_ensure_returns_while_the_flow_really_sets_the_entry_up(
        self, monkeypatch
    ):
        """[BLOCKER2] Real ``async_setup_account_entry`` inside the flow - no hang.

        The flow manager below calls the REAL account setup (seed, coordinator,
        adoption, legacy-sweep, forward) instead of a stub, so the second lock
        acquisition really happens. ``asyncio.wait_for(..., timeout=1)`` turns
        the former deadlock into a deterministic failure.
        """
        entries: list = [conftest.StrictConfigEntry(
            entry_id="ws1", data={"token": "tok", "workspace_name": "WS"}
        )]
        hass = MagicMock()
        hass.data = {}
        config_entries = conftest.install_applying_config_entries(hass)
        config_entries.hass = hass
        config_entries.async_entries = MagicMock(
            side_effect=lambda domain: list(entries)
        )
        config_entries.async_forward_entry_setups = AsyncMock()
        config_entries.async_unload_platforms = AsyncMock(return_value=True)
        flow_manager = _FakeFlowManager(entries, config_entries)
        config_entries.flow = flow_manager

        setup_calls: list[str] = []

        async def _real_setup(entry_id: str) -> bool:
            """HA behaviour: run the entry setup of the freshly created entry."""
            setup_calls.append(entry_id)
            entry = next(item for item in entries if item.entry_id == entry_id)
            return await account.async_setup_account_entry(hass, entry)

        config_entries.async_setup = AsyncMock(side_effect=_real_setup)

        # Real registry/issue/timer doubles for the adoption that runs inside the
        # setup (and therefore takes the lock a second time).
        conftest.install_issue_registry_stub()
        entity_registry = conftest.install_entity_registry_stub()
        conftest.install_device_registry_stub(
            conftest.FakeDeviceRegistry(entity_registry)
        )
        account.er = sys.modules["homeassistant.helpers.entity_registry"]
        account.dr = sys.modules["homeassistant.helpers.device_registry"]
        account.ir = sys.modules["homeassistant.helpers.issue_registry"]
        conftest.reset_write_log()
        conftest.install_timer_stub()

        # Offline client for the setup's one-time first fetch.
        client = MagicMock()
        client.fetch_models = AsyncMock(return_value=["glm-5", "glm-5-free"])
        monkeypatch.setattr(account, "OpenCodeGoApiClient", lambda _session: client)
        monkeypatch.setattr(
            account, "async_get_clientsession", lambda _hass: MagicMock()
        )

        await asyncio.wait_for(account.async_ensure_account_entry(hass), timeout=1)

        account_entries = [
            entry for entry in entries
            if getattr(entry, "unique_id", None) == const.ACCOUNT_ENTRY_UNIQUE_ID
        ]
        assert len(account_entries) == 1, "exactly one account entry"
        assert len(flow_manager.init_calls) == 1
        assert setup_calls == ["account-entry"], "the flow set the entry up once"
        assert client.fetch_models.await_count == 1
        lock = hass.data[const.DOMAIN][const.DATA_ADOPTION_LOCK]
        assert not lock.locked(), "the adoption lock must be released again"

        # Second call is a no-op: the loaded entry is left alone.
        await asyncio.wait_for(account.async_ensure_account_entry(hass), timeout=1)

        assert len(entries) == 2
        assert len(flow_manager.init_calls) == 1
        assert setup_calls == ["account-entry"]
        assert not lock.locked()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
