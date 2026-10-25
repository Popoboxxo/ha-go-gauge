#!/usr/bin/env python3
"""Account-entry platform wiring (2.0.0, plan steps 13/14 = plan §8.3/§8.4).

What this file pins down (implementation steps 13 + 14 of
``docs/PLAN-account-entry-2.0.0.md``):

* the account entry creates EXACTLY the account platform entities (4 catalog
  sensors, reachability binary sensor, refresh button, auto-update switch,
  models-interval number) with their canonical ``account_*`` unique_ids,
* a workspace entry creates NONE of them (no second generation, no owner),
* ``ACCOUNT_PLATFORMS`` is forwarded exactly once and only by the account setup,
* the adoption + account setup leave exactly ONE registry entry per kind,
* the legacy workspace model controls are removed and the removal is
  idempotent,
* the account options are seeded from a pre-2.0.0 workspace entry (migration
  without data loss) with documented defaults as fallback,
* account switch/number setters change the coordinator + ``entry.options``
  live and never reload the entry.

The shared HA fakes live in ``tests/conftest.py``; this file loads the
integration modules in dependency order like the other account test modules.
"""
from __future__ import annotations

import importlib.util
import sys
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


# Re-assert the shared fakes immediately before the integration imports.
conftest.install_ha_stubs()
_CONFIG_ENTRY_STATE = conftest.install_config_entry_state_stub()
conftest.install_coordinator_stub()
conftest.install_timer_stub()
conftest.install_issue_registry_stub()
conftest.install_registry_stubs()

# Entity classes call ``super().__init__(coordinator)``; the generic _Flexible
# stand-in cannot take an argument, so install a minimal real CoordinatorEntity.
_uc = sys.modules["homeassistant.helpers.update_coordinator"]


class _CoordinatorEntity:
    """Minimal ``CoordinatorEntity`` stand-in (stores the coordinator)."""

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.ha_state_writes = 0

    @property
    def available(self) -> bool:
        return True

    def async_write_ha_state(self) -> None:
        """Count state writes (setters must refresh the UI)."""
        self.ha_state_writes += 1


_uc.CoordinatorEntity = _CoordinatorEntity


def _enum_like(*members):
    """Return a tiny enum stand-in with UPPERCASE members."""
    return type("EnumStub", (), {m.upper(): m for m in members})


sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like(
    "measurement"
)
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like(
    "timestamp", "duration", "enum"
)
sys.modules["homeassistant.components.binary_sensor"].BinarySensorDeviceClass = (
    _enum_like("problem", "connectivity")
)
sys.modules["homeassistant.components.number"].NumberMode = _enum_like("box")

const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
account_logic = _load("go_gauge.account_logic", str(BASE / "account_logic.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
account = _load("go_gauge.account", str(BASE / "account.py"))
sensor = _load("go_gauge.sensor", str(BASE / "sensor.py"))
binary_sensor = _load("go_gauge.binary_sensor", str(BASE / "binary_sensor.py"))
button = _load("go_gauge.button", str(BASE / "button.py"))
switch = _load("go_gauge.switch", str(BASE / "switch.py"))
number = _load("go_gauge.number", str(BASE / "number.py"))

ACCOUNT_ENTRY_ID = "account-entry"
WORKSPACE_ENTRY_ID = "ws-entry"


# ============================================================================
# Doubles
# ============================================================================

class _FakeEntry:
    """Minimal ConfigEntry double for the platform wiring."""

    def __init__(
        self,
        entry_id: str = ACCOUNT_ENTRY_ID,
        *,
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.unique_id = (
            const.ACCOUNT_ENTRY_UNIQUE_ID
            if (data or {}).get(const.CONF_ENTRY_KIND) == const.ENTRY_KIND_ACCOUNT
            else f"go_gauge_{entry_id}"
        )
        self.title = const.ENTITY_NAME_ACCOUNT
        self.data = dict(data or {const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT})
        self.options = dict(options or {})
        self.state = _CONFIG_ENTRY_STATE.LOADED
        self.disabled_by = None
        self.unload_callbacks: list[Any] = []
        self.update_listeners: list[Any] = []

    def async_on_unload(self, callback: Any) -> None:
        """Record an unload callback (the account setter must register none)."""
        self.unload_callbacks.append(callback)

    def add_update_listener(self, callback: Any) -> Any:
        """Record a reload listener (the account entry must have none)."""
        self.update_listeners.append(callback)
        return callback


class _FakeAccountCoordinator:
    """Account coordinator surface used by the platform entities."""

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        options = options or {}
        self.auto_fetch = bool(
            options.get(
                const.CONF_AUTO_UPDATE_MODELS,
                const.DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS,
            )
        )
        self.base_interval_minutes = int(
            options.get(
                const.CONF_MODELS_REFRESH_MINUTES,
                const.DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
            )
        )
        self.data: dict[str, Any] = {}
        self.models_block: dict[str, Any] | None = None
        self.last_models_outcome: str | None = None
        self.fetch_now_calls = 0
        self.set_auto_fetch_calls: list[bool] = []
        self.set_fetch_interval_calls: list[int] = []

    @property
    def fetch_interval_minutes(self) -> int | None:
        return self.base_interval_minutes if self.auto_fetch else None

    async def async_fetch_now(self) -> None:
        self.fetch_now_calls += 1

    def set_auto_fetch(self, enabled: bool) -> None:
        self.set_auto_fetch_calls.append(bool(enabled))
        self.auto_fetch = bool(enabled)

    def set_fetch_interval(self, minutes: int) -> None:
        self.set_fetch_interval_calls.append(int(minutes))
        self.base_interval_minutes = max(
            const.ACCOUNT_MODELS_REFRESH_MIN,
            min(int(minutes), const.ACCOUNT_MODELS_REFRESH_MAX),
        )


class _FakeWorkspaceCoordinator:
    """Usage coordinator surface used by the workspace entities."""

    ws_name = "Work"

    def __init__(self) -> None:
        self.data = {
            "workspaces": [{
                "key": "ws1",
                "status": "ok",
                "windows": {
                    win: {"percent": 10.0, "status": "ok", "resets_at": None}
                    for win in ("5h", "week", "month")
                },
            }],
            "auto_usage": True,
            "usage_refresh_minutes": 10,
        }
        self.auto_usage = True
        self.usage_minutes = 10
        self.warn_percent = const.DEFAULT_WARN_PERCENT
        self.pace_red_percent = const.DEFAULT_PACE_RED_PERCENT
        self.last_usage_fetch = None
        self.last_usage_attempt = None
        self.last_usage_ok = None
        self.last_usage_outcome = None
        self.recalculate_calls = 0

    def recalculate_interval(self) -> None:
        self.recalculate_calls += 1


class _Hass:
    """``HomeAssistant`` double with a real ``data`` dict + config entries."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {const.DOMAIN: {}}
        self.config_entries = self._make_config_entries()

    @staticmethod
    def _make_config_entries() -> Any:
        config_entries = conftest.make_applying_config_entries()
        config_entries.async_entries = MagicMock(return_value=[])
        config_entries.async_forward_entry_setups = AsyncMock()
        config_entries.async_unload_platforms = AsyncMock(return_value=True)
        config_entries.async_reload = AsyncMock()
        return config_entries


def _install_client(monkeypatch) -> MagicMock:
    """Inject a fake API client - no HTTP leaves the process."""
    client = MagicMock()
    client.fetch_models = AsyncMock(return_value=["glm-5", "glm-5-free"])
    monkeypatch.setattr(account, "OpenCodeGoApiClient", lambda _session: client)
    monkeypatch.setattr(
        account, "async_get_clientsession", lambda _hass: MagicMock()
    )
    return client


def _entity_ids(entities: list[Any]) -> set[str]:
    """Return the unique_ids of the created entities."""
    return {entity._attr_unique_id for entity in entities}


# ============================================================================
# 1. Account branch: exactly the account platform entities
# ============================================================================

@pytest.mark.asyncio
async def test_account_branch_creates_exactly_the_account_entities():
    """[PLAN2-E4][PLAN2-B1] Account entry -> one entity per account platform."""
    entry = _FakeEntry()
    coordinator = _FakeAccountCoordinator()
    hass = _Hass()
    hass.data[const.DOMAIN][entry.entry_id] = coordinator

    sensors: list[Any] = []
    await sensor.async_setup_entry(hass, entry, sensors.extend)
    binaries: list[Any] = []
    await binary_sensor.async_setup_entry(hass, entry, binaries.extend)
    buttons: list[Any] = []
    await button.async_setup_entry(hass, entry, buttons.extend)
    switches: list[Any] = []
    await switch.async_setup_entry(hass, entry, switches.extend)
    numbers: list[Any] = []
    await number.async_setup_entry(hass, entry, numbers.extend)

    assert len(sensors) == 4
    assert {type(entity) for entity in sensors} == {
        sensor.ModelCatalogSensor,
        sensor.LiveModelsCountSensor,
        sensor.CheapestModelSensor,
        sensor.FreeModelsSensor,
    }
    assert len(binaries) == 1 and type(binaries[0]) is binary_sensor.ApiReachableBinarySensor
    assert len(buttons) == 1 and type(buttons[0]) is button.AccountRefreshButton
    assert len(switches) == 1 and type(switches[0]) is switch.AccountAutoUpdateModelsSwitch
    assert len(numbers) == 1 and type(numbers[0]) is number.AccountModelsRefreshMinutesNumber

    assert _entity_ids(sensors) == {
        "account_model_catalog",
        "account_models_live_count",
        "account_cheapest_model",
        "account_free_models",
    }
    assert _entity_ids(binaries) == {"account_api_reachable"}
    assert _entity_ids(buttons) == {"account_refresh"}
    assert _entity_ids(switches) == {"account_auto_update_models"}
    assert _entity_ids(numbers) == {"account_models_refresh_minutes"}


# ============================================================================
# 2. Workspace branch: none of the account entities
# ============================================================================

@pytest.mark.asyncio
async def test_workspace_branch_creates_no_account_entities():
    """[PLAN2-B2][PLAN2-M3] Workspace creates usage entities only."""
    entry = _FakeEntry(
        WORKSPACE_ENTRY_ID,
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_WORKSPACE},
        options={"token": "tok"},
    )
    coordinator = _FakeWorkspaceCoordinator()
    hass = _Hass()
    hass.data[const.DOMAIN][entry.entry_id] = coordinator

    sensors: list[Any] = []
    await sensor.async_setup_entry(hass, entry, sensors.extend)
    binaries: list[Any] = []
    await binary_sensor.async_setup_entry(hass, entry, binaries.extend)
    buttons: list[Any] = []
    await button.async_setup_entry(hass, entry, buttons.extend)
    switches: list[Any] = []
    await switch.async_setup_entry(hass, entry, switches.extend)
    numbers: list[Any] = []
    await number.async_setup_entry(hass, entry, numbers.extend)

    # No catalog sensor and no account-level entity of any platform.
    assert not any(
        isinstance(entity, sensor.ModelCatalogSensor) for entity in sensors
    )
    assert not any(
        isinstance(entity, binary_sensor.ApiReachableBinarySensor)
        for entity in binaries
    )
    assert not any(
        isinstance(entity, button.AccountRefreshButton) for entity in buttons
    )
    assert not any(
        isinstance(entity, switch.AccountAutoUpdateModelsSwitch) for entity in switches
    )
    assert not any(
        isinstance(entity, number.AccountModelsRefreshMinutesNumber) for entity in numbers
    )

    # ... but the workspace entities themselves are all there.
    assert {type(entity) for entity in sensors} == {
        sensor.UsagePercentSensor,
        sensor.ResetTimestampSensor,
        sensor.UsageForecastSensor,
        sensor.UsagePaceSensor,
        sensor.RemainingBudgetSensor,
        sensor.TimeUntilResetSensor,
        sensor.BurnRateSensor,
        sensor.ApiStatusSensor,
    }
    assert {type(entity) for entity in buttons} == {button.RefreshButton}
    assert {type(entity) for entity in switches} == {switch.AutoUpdateUsageSwitch}
    assert {type(entity) for entity in numbers} == {
        number.WarnPercentNumber,
        number.PaceRedPercentNumber,
        number.UsageRefreshMinutesNumber,
    }


@pytest.mark.asyncio
async def test_workspace_refresh_button_only_resets_usage_fetch():
    """[PLAN2-M3] Workspace refresh never touches the account catalog."""
    entry = _FakeEntry(
        WORKSPACE_ENTRY_ID,
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_WORKSPACE},
    )
    coordinator = _FakeWorkspaceCoordinator()
    coordinator.last_usage_fetch = object()
    coordinator.async_request_refresh = AsyncMock()
    refresh = button.RefreshButton(coordinator, entry)
    refresh.hass = _Hass()

    await refresh.async_press()

    assert coordinator.last_usage_fetch is None
    coordinator.async_request_refresh.assert_awaited_once()
    assert not hasattr(coordinator, "last_models_fetch")


@pytest.mark.asyncio
async def test_account_refresh_button_forces_a_catalog_fetch_only():
    """[PLAN2-E4] Account refresh uses ``async_fetch_now`` (no usage fetch)."""
    entry = _FakeEntry()
    coordinator = _FakeAccountCoordinator()
    refresh = button.AccountRefreshButton(coordinator, entry)
    refresh.hass = _Hass()

    await refresh.async_press()

    assert coordinator.fetch_now_calls == 1


# ============================================================================
# 3. Account setup: forward ACCOUNT_PLATFORMS exactly once, after adoption
# ============================================================================

@pytest.mark.asyncio
async def test_account_setup_forwards_account_platforms_once_after_adoption(
    monkeypatch,
):
    """[PLAN2-M1][PLAN2-B1] Adoption first, then exactly one platform forward."""
    _install_client(monkeypatch)
    entry = _FakeEntry()
    hass = _Hass()
    call_order: list[str] = []

    async def _adoption(*_args, **_kwargs):
        call_order.append("adoption")
        return account.AdoptionReport()

    async def _forward(*_args, **_kwargs):
        call_order.append("forward")

    monkeypatch.setattr(account, "async_adopt_account_entities", _adoption)
    hass.config_entries.async_forward_entry_setups = AsyncMock(side_effect=_forward)

    assert await account.async_setup_account_entry(hass, entry) is True

    assert const.ACCOUNT_PLATFORMS == [
        "sensor", "binary_sensor", "button", "switch", "number",
    ]
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, const.ACCOUNT_PLATFORMS
    )
    assert call_order == ["adoption", "forward"]


# ============================================================================
# 4. No duplicates: adoption + account setup -> one registry entry per kind
# ============================================================================

def _seed_legacy_catalog(entity_registry: Any) -> None:
    """Create the five v1.x catalog entities on the canonical device.

    ``PLATFORM_BY_KIND`` supplies the ENTITY domain (``RegistryEntry.domain``);
    ``RegistryEntry.platform`` is the integration domain (``const.DOMAIN``).
    """
    entity_domains = account_logic.PLATFORM_BY_KIND
    for kind in account_logic.ACCOUNT_ENTITY_KINDS:
        entity_registry.add(conftest.RegistryEntry(
            entity_id=f"{entity_domains[kind]}.ws_{kind}",
            unique_id=f"{WORKSPACE_ENTRY_ID}_{kind}",
            platform=const.DOMAIN,
            domain=entity_domains[kind],
            config_entry_id=WORKSPACE_ENTRY_ID,
            device_id="dev-account",
        ))


@pytest.mark.asyncio
async def test_account_setup_leaves_exactly_one_entry_per_kind(monkeypatch):
    """[PLAN2-M1] Adoption + account setup -> 1 registry entry per kind.

    This is the duplicate-freedom proof for the transition: the workspace owner
    no longer creates catalog entities, the account entry adopts the existing
    five, and the platform forward re-binds them - never a second generation.
    """
    _install_client(monkeypatch)
    entity_registry = conftest.install_entity_registry_stub()
    device_registry = conftest.install_device_registry_stub(
        conftest.FakeDeviceRegistry(entity_registry)
    )
    device_registry.add(conftest.DeviceEntry(
        id="dev-account",
        identifiers={(const.DOMAIN, const.ACCOUNT_DEVICE_IDENTIFIER)},
        config_entries={WORKSPACE_ENTRY_ID},
    ))
    conftest.install_issue_registry_stub()
    account.er = sys.modules["homeassistant.helpers.entity_registry"]
    account.dr = sys.modules["homeassistant.helpers.device_registry"]
    account.ir = sys.modules["homeassistant.helpers.issue_registry"]
    _seed_legacy_catalog(entity_registry)

    entry = _FakeEntry()
    hass = _Hass()
    assert await account.async_setup_account_entry(hass, entry) is True

    for kind in account_logic.ACCOUNT_ENTITY_KINDS:
        canonical = account_logic.canonical_unique_id(kind)
        matches = [
            registry_entry
            for registry_entry in entity_registry.entities.values()
            if registry_entry.unique_id == canonical
        ]
        assert len(matches) == 1, f"{kind}: expected exactly one canonical entry"
        assert matches[0].config_entry_id == entry.entry_id
        assert matches[0].device_id == "dev-account"


# ============================================================================
# 5. Legacy model settings: removed, canonical kept, idempotent
# ============================================================================

def test_legacy_model_settings_removed_and_idempotent():
    """[PLAN2-E4] Legacy workspace controls removed; ``account_*`` survives."""
    entity_registry = conftest.install_entity_registry_stub()
    account.er = sys.modules["homeassistant.helpers.entity_registry"]
    hass = _Hass()

    legacy = [
        ("switch.ws_auto_update_models", "ws_auto_update_models", "switch"),
        ("number.ws_models_refresh_minutes", "ws_models_refresh_minutes", "number"),
    ]
    for entity_id, unique_id, entity_domain in legacy:
        entity_registry.add(conftest.RegistryEntry(
            entity_id=entity_id,
            unique_id=unique_id,
            platform=const.DOMAIN,
            domain=entity_domain,
        ))
    canonical = [
        ("switch.account_auto_update_models", "account_auto_update_models", "switch"),
        ("number.account_models_refresh_minutes", "account_models_refresh_minutes", "number"),
        # Unrelated entity domain must never be touched either.
        ("sensor.account_model_catalog", "account_model_catalog", "sensor"),
    ]
    for entity_id, unique_id, entity_domain in canonical:
        entity_registry.add(conftest.RegistryEntry(
            entity_id=entity_id,
            unique_id=unique_id,
            platform=const.DOMAIN,
            domain=entity_domain,
        ))

    assert account.async_remove_legacy_model_settings(hass) == 2
    assert set(entity_registry.remove_calls) == {
        "switch.ws_auto_update_models",
        "number.ws_models_refresh_minutes",
    }
    for _, unique_id, _ in canonical:
        assert any(
            registry_entry.unique_id == unique_id
            for registry_entry in entity_registry.entities.values()
        )

    # Idempotent: nothing left to remove on a second pass.
    assert account.async_remove_legacy_model_settings(hass) == 0
    assert len(entity_registry.remove_calls) == 2


def test_legacy_model_settings_removed_with_real_ha_semantics():
    """[E2E-2026-10-25] Removal works with HA's real field semantics.

    ``platform`` is the integration domain (``go_gauge``), ``domain`` the entity
    domain (``switch``/``number``). The inverted predicate removed nothing, the
    dead controls stayed behind on the live instance.
    """
    entity_registry = conftest.install_entity_registry_stub()
    account.er = sys.modules["homeassistant.helpers.entity_registry"]
    hass = _Hass()

    legacy = [
        (f"switch.{WORKSPACE_ENTRY_ID}_auto_update_models",
         f"{WORKSPACE_ENTRY_ID}_auto_update_models", "switch"),
        (f"number.{WORKSPACE_ENTRY_ID}_models_refresh_minutes",
         f"{WORKSPACE_ENTRY_ID}_models_refresh_minutes", "number"),
    ]
    for entity_id, unique_id, entity_domain in legacy:
        entity_registry.add(conftest.RegistryEntry(
            entity_id=entity_id,
            unique_id=unique_id,
            platform=const.DOMAIN,
            domain=entity_domain,
        ))
    canonical = [
        ("switch.account_auto_update_models",
         "account_auto_update_models", "switch"),
        ("number.account_models_refresh_minutes",
         "account_models_refresh_minutes", "number"),
    ]
    for entity_id, unique_id, entity_domain in canonical:
        entity_registry.add(conftest.RegistryEntry(
            entity_id=entity_id,
            unique_id=unique_id,
            platform=const.DOMAIN,
            domain=entity_domain,
        ))

    assert account.async_remove_legacy_model_settings(hass) == 2

    assert set(entity_registry.remove_calls) == {
        entity_id for entity_id, _uid, _domain in legacy
    }
    for _entity_id, unique_id, _domain in canonical:
        assert any(
            entry.unique_id == unique_id
            for entry in entity_registry.entities.values()
        )


@pytest.mark.asyncio
async def test_adoption_reports_removed_settings_and_second_pass_removes_zero():
    """[PLAN2-E4] ``report.removed_settings``: 2 on adoption, 0 on re-run."""
    entity_registry = conftest.install_entity_registry_stub()
    device_registry = conftest.install_device_registry_stub(
        conftest.FakeDeviceRegistry(entity_registry)
    )
    device_registry.add(conftest.DeviceEntry(
        id="dev-account",
        identifiers={(const.DOMAIN, const.ACCOUNT_DEVICE_IDENTIFIER)},
        config_entries={WORKSPACE_ENTRY_ID},
    ))
    conftest.install_issue_registry_stub()
    account.er = sys.modules["homeassistant.helpers.entity_registry"]
    account.dr = sys.modules["homeassistant.helpers.device_registry"]
    account.ir = sys.modules["homeassistant.helpers.issue_registry"]
    entity_registry.add(conftest.RegistryEntry(
        entity_id="switch.ws_auto_update_models",
        unique_id="ws_auto_update_models",
        platform=const.DOMAIN,
        domain="switch",
    ))
    entity_registry.add(conftest.RegistryEntry(
        entity_id="number.ws_models_refresh_minutes",
        unique_id="ws_models_refresh_minutes",
        platform=const.DOMAIN,
        domain="number",
    ))

    entry = _FakeEntry()
    hass = _Hass()
    first = await account.async_adopt_account_entities(hass, entry)
    assert first.removed_settings == 2
    assert len(entity_registry.remove_calls) == 2

    second = await account.async_adopt_account_entities(hass, entry)
    assert second.removed_settings == 0
    assert len(entity_registry.remove_calls) == 2


# ============================================================================
# 6. Account options seed (migration without data loss)
# ============================================================================

def _setup_with_workspace_options(
    monkeypatch,
    workspace_options: dict[str, Any] | None,
) -> tuple[_FakeEntry, Any]:
    """Run the account setup with one workspace entry carrying `options`."""
    _install_client(monkeypatch)
    entry = _FakeEntry()
    hass = _Hass()
    workspace = _FakeEntry(
        WORKSPACE_ENTRY_ID,
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_WORKSPACE},
        options=workspace_options or {},
    )
    hass.config_entries.async_entries = MagicMock(
        return_value=[workspace] if workspace_options is not None else []
    )
    return entry, hass


@pytest.mark.asyncio
async def test_account_options_seeded_from_first_workspace(monkeypatch):
    """[PLAN2-E4] A v1.x workspace value migrates to the account entry."""
    entry, hass = _setup_with_workspace_options(
        monkeypatch,
        {
            const.CONF_AUTO_UPDATE_MODELS: False,
            const.CONF_MODELS_REFRESH_MINUTES: 15,
        },
    )

    assert await account.async_setup_account_entry(hass, entry) is True

    assert entry.options[const.CONF_AUTO_UPDATE_MODELS] is False
    assert entry.options[const.CONF_MODELS_REFRESH_MINUTES] == 15
    coordinator = hass.data[const.DOMAIN][entry.entry_id]
    assert coordinator.auto_fetch is False
    assert coordinator.base_interval_minutes == 15


@pytest.mark.asyncio
async def test_account_options_use_defaults_without_workspace_values(monkeypatch):
    """[PLAN2-E4] No workspace values -> documented defaults ON / 60 min."""
    entry, hass = _setup_with_workspace_options(monkeypatch, None)

    assert await account.async_setup_account_entry(hass, entry) is True

    assert entry.options[const.CONF_AUTO_UPDATE_MODELS] is True
    assert entry.options[const.CONF_MODELS_REFRESH_MINUTES] == 60
    coordinator = hass.data[const.DOMAIN][entry.entry_id]
    assert coordinator.auto_fetch is True
    assert coordinator.base_interval_minutes == 60


# ============================================================================
# 7. Switch/Number setters: live + persisted, never a reload
# ============================================================================

@pytest.mark.asyncio
async def test_account_switch_updates_coordinator_and_options_without_reload():
    """[PLAN2-E4] Switch off -> auto_fetch False + options; no reload call."""
    entry = _FakeEntry()
    coordinator = _FakeAccountCoordinator()
    hass = _Hass()
    hass.config_entries.async_reload = AsyncMock()
    toggle = switch.AccountAutoUpdateModelsSwitch(coordinator, entry)
    toggle.hass = hass

    assert toggle._attr_unique_id == "account_auto_update_models"
    await toggle.async_turn_off()

    assert coordinator.auto_fetch is False
    assert entry.options[const.CONF_AUTO_UPDATE_MODELS] is False
    assert toggle.ha_state_writes == 1
    hass.config_entries.async_reload.assert_not_awaited()
    assert entry.update_listeners == []
    assert entry.unload_callbacks == []


@pytest.mark.asyncio
async def test_account_number_updates_coordinator_and_options_without_reload():
    """[PLAN2-E4] Number -> base interval + options; no reload call."""
    entry = _FakeEntry()
    coordinator = _FakeAccountCoordinator()
    hass = _Hass()
    hass.config_entries.async_reload = AsyncMock()
    interval = number.AccountModelsRefreshMinutesNumber(coordinator, entry)
    interval.hass = hass

    assert interval._attr_unique_id == "account_models_refresh_minutes"
    await interval.async_set_native_value(15)

    assert coordinator.base_interval_minutes == 15
    assert entry.options[const.CONF_MODELS_REFRESH_MINUTES] == 15
    assert interval.ha_state_writes == 1
    hass.config_entries.async_reload.assert_not_awaited()
    assert entry.update_listeners == []


# ============================================================================
# 13. Canon v1.4.0 guard: statically parseable account entity identity
# ============================================================================
#
# The canon checker parses ``_attr_unique_id`` textually (it cannot resolve
# helper calls) and requires the literal suffix after the placeholder. These
# guard tests pin the runtime values that the literal templates must produce:
# a future refactor back to a helper call would keep them green here, but the
# explicit literal-token assertion below documents the checker contract.

ACCOUNT_ENTITY_CLASSES = (
    ("model_catalog", sensor.ModelCatalogSensor),
    ("models_live_count", sensor.LiveModelsCountSensor),
    ("cheapest_model", sensor.CheapestModelSensor),
    ("free_models", sensor.FreeModelsSensor),
    ("api_reachable", binary_sensor.ApiReachableBinarySensor),
    ("refresh", button.AccountRefreshButton),
    ("auto_update_models", switch.AccountAutoUpdateModelsSwitch),
    ("models_refresh_minutes", number.AccountModelsRefreshMinutesNumber),
)


@pytest.mark.parametrize(("kind", "entity_cls"), ACCOUNT_ENTITY_CLASSES)
def test_account_entity_unique_id_matches_canonical_helper(kind, entity_cls):
    """[CANON1.4] All 8 account entities carry ``canonical_unique_id(kind)``."""
    coordinator = _FakeAccountCoordinator()
    entry = _FakeEntry()
    entity_obj = entity_cls(coordinator, entry)

    assert entity_obj._attr_unique_id == account_logic.canonical_unique_id(kind)
    assert entity_obj._attr_unique_id == f"account_{kind}"


@pytest.mark.parametrize(("kind", "entity_cls"), ACCOUNT_ENTITY_CLASSES)
def test_account_entity_unique_id_template_is_statically_parseable(kind, entity_cls):
    """[CANON1.4] ``_attr_unique_id`` text ends with the literal kind token.

    Mirrors the checker's ``_literal_token`` rule: split on ``{...}`` and take
    the last non-empty part, stripped of ``_``. A helper call in ``__init__``
    would not be visible to that regex and the canon check would fail again.
    """
    import inspect
    import re

    source = inspect.getsource(entity_cls)
    match = re.search(r'_attr_unique_id\s*=\s*f?"([^"]+)"', source)
    assert match is not None, (
        f"{entity_cls.__name__}: _attr_unique_id must be a literal f-string "
        "(the canon checker cannot parse helper calls)"
    )
    parts = [part for part in re.split(r"\{[^}]*\}", match.group(1)) if part.strip("_")]
    assert parts and parts[-1].strip("_") == kind


def test_account_refresh_button_translation_key_is_canon_refresh():
    """[CANON1.4] Button uses the canon key ``refresh`` (no account-specific key)."""
    entity_obj = button.AccountRefreshButton(_FakeAccountCoordinator(), _FakeEntry())

    assert entity_obj._attr_translation_key == "refresh"
    # The unique_id is what distinguishes the account button from the
    # workspace refresh button - it must stay ``account_refresh``.
    assert entity_obj._attr_unique_id == "account_refresh"


def test_account_refresh_button_key_is_translated_everywhere():
    """[CANON1.4] ``refresh`` is the ONLY button translation key in all files."""
    import json

    assert button.AccountRefreshButton.__dict__["_attr_translation_key"] == "refresh"
    for rel in ("strings.json", "translations/de.json", "translations/en.json"):
        data = json.loads((BASE / rel).read_text(encoding="utf-8"))
        assert set(data["entity"]["button"]) == {"refresh"}, rel


def test_account_models_number_bounds_are_literal_1_to_1440():
    """[CANON1.4] Range is literal 1..1440 (checker parses digits, not consts)."""
    entity_obj = number.AccountModelsRefreshMinutesNumber(
        _FakeAccountCoordinator(), _FakeEntry()
    )

    assert entity_obj._attr_native_min_value == 1
    assert entity_obj._attr_native_max_value == 1440
    assert entity_obj._attr_native_step == 1


def test_account_unique_id_prefix_is_the_single_source():
    """[CANON1.4] Prefix constant + helper agree on the canonical shape."""
    assert const.ACCOUNT_UNIQUE_ID_PREFIX == "account_"
    assert account_logic.canonical_unique_id("model_catalog") == "account_model_catalog"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
