#!/usr/bin/env python3
"""Account entity adoption tests - sweep, collisions, devices (2.0.0, phase B).

Plan: ``docs/PLAN-account-entry-2.0.0.md`` §3.3 (v7 -> v8 update order: registry
work BEFORE the platform forward), §4.1 (candidate set), §4.2 (deterministic
winner), §4.3 (collision loser), §4.4 (stable repair-issue ids), §4.5
(idempotency predicates), §4.6 (error path), §8.3 (review gaps "Adoption
idempotent", "Entities-vor-Device", "Reihenfolgeunabhängig", "Kollision +
Repair", "Duplikat-Device nur wenn leer") and §8.4 (this file).

Scope: the REAL ``async_adopt_account_entities`` of ``account.py`` plus its
wiring into ``async_setup_account_entry``. NOT in scope (phase C): legacy-setting
removal (plan step 9 - ``report.removed_settings`` therefore stays ``0``
everywhere), platform forwarding and the entity classes themselves.

Distinctive fact about this project's v1.x layout (measured on the real
instance): the five catalog entities already hang on the CANONICAL device
``(DOMAIN, "account")`` - only their ``unique_id`` (``<entry>_<kind>``) and their
``config_entry_id`` (the catalog-owner workspace) are legacy. The canonical
device therefore has to be handed over to the account entry, and a "duplicate
device" is a rare leftover: a device that candidates pointed at before the
sweep. A pure workspace device (usage entities, identifier
``(DOMAIN, <workspace entry_id>)``) is NEVER a duplicate and must not even raise
a repair issue.

The shared Home-Assistant module fakes live in ``tests/conftest.py``; the
registry doubles there are stateful (``FakeEntityRegistry``,
``FakeDeviceRegistry``, ``FakeIssueRegistry``) and journal every write in
``WRITE_LOG`` for the cross-registry ordering assertion.

``account.py`` binds ``er``/``dr``/``ir`` at import time, while a later-collected
test module may replace those ``sys.modules`` entries with its own ``_Flexible``
fake. The fixtures therefore re-point ``account.er``/``account.dr``/``account.ir``
at the currently installed fake module before wiring a fresh registry into it -
otherwise the per-test registry would never be reached.
"""
from __future__ import annotations

import importlib.util
import itertools
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


# Re-assert the shared fakes immediately before the integration imports:
# ``account.py`` uses the coordinator double, the ConfigEntryState double, the
# counting timer double, the issue-registry dispatchers and the registry classes.
conftest.install_ha_stubs()
_CONFIG_ENTRY_STATE = conftest.install_config_entry_state_stub()
conftest.install_coordinator_stub()
conftest.install_timer_stub()
conftest.install_issue_registry_stub()
conftest.install_registry_stubs()

const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
account_logic = _load("go_gauge.account_logic", str(BASE / "account_logic.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
account = _load("go_gauge.account", str(BASE / "account.py"))

ACCOUNT_ENTRY_ID = "account-entry"
OWNER_ENTRY_ID = "ws-owner"
CANONICAL_DEVICE_ID = "dev-account"

# Legacy entity_id/ platform per kind: what a v1.x instance carries today.
_ENTITY_FIXTURES: dict[str, tuple[str, str]] = {
    "model_catalog": ("sensor", "sensor.ws_model_catalog"),
    "models_live_count": ("sensor", "sensor.ws_models_live_count"),
    "cheapest_model": ("sensor", "sensor.ws_cheapest_model"),
    "free_models": ("sensor", "sensor.ws_free_models"),
    "api_reachable": ("binary_sensor", "binary_sensor.ws_api_reachable"),
}


# ============================================================================
# Fixtures / helpers
# ============================================================================

class _Entry:
    """Minimal ConfigEntry double: adoption only reads ``entry_id``/``data``."""

    def __init__(
        self,
        entry_id: str = ACCOUNT_ENTRY_ID,
        *,
        data: dict[str, Any] | None = None,
        disabled_by: str | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.unique_id = const.ACCOUNT_ENTRY_UNIQUE_ID
        self.title = const.ENTITY_NAME_ACCOUNT
        self.data = dict(data or {const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT})
        self.options: dict[str, Any] = {}
        self.state = _CONFIG_ENTRY_STATE.LOADED
        self.disabled_by = disabled_by


class _Registries:
    """Bundle of the three per-test registry doubles."""

    def __init__(self, hass: MagicMock) -> None:
        entity = conftest.install_entity_registry_stub()
        device = conftest.install_device_registry_stub(
            conftest.FakeDeviceRegistry(entity)
        )
        issue = conftest.install_issue_registry_stub()
        # Re-point the module references ``account.py`` bound at import time at
        # whatever object is registered NOW (see module docstring).
        account.er = sys.modules["homeassistant.helpers.entity_registry"]
        account.dr = sys.modules["homeassistant.helpers.device_registry"]
        account.ir = sys.modules["homeassistant.helpers.issue_registry"]
        conftest.reset_write_log()
        self.hass = hass
        self.entity = entity
        self.device = device
        self.issue = issue


@pytest.fixture
def hass():
    """``HomeAssistant`` double with a REAL ``data`` dict and no entries."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_update_entry = MagicMock(return_value=True)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    return hass


@pytest.fixture
def registries(hass):
    """Install fresh, stateful registry doubles for one test."""
    return _Registries(hass)


def _entry(entry_id: str = ACCOUNT_ENTRY_ID) -> _Entry:
    """Return the account ConfigEntry double."""
    return _Entry(entry_id)


def _workspace_entry(entry_id: str = OWNER_ENTRY_ID) -> _Entry:
    """Return a workspace ConfigEntry double (device-exclusion tests)."""
    return _Entry(
        entry_id, data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_WORKSPACE}
    )


def _seed_account_device(
    registries: _Registries,
    *,
    owners: set[str] | None = None,
    device_id: str = CANONICAL_DEVICE_ID,
) -> conftest.DeviceEntry:
    """Register the canonical account device (identifier ``(DOMAIN, account)``)."""
    device = conftest.DeviceEntry(
        id=device_id,
        name="Go Gauge Konto",
        identifiers={(const.DOMAIN, const.ACCOUNT_DEVICE_IDENTIFIER)},
        config_entries=set(owners) if owners is not None else {OWNER_ENTRY_ID},
    )
    return registries.device.add(device)


def _seed_legacy(
    registries: _Registries,
    *,
    owner_entry_id: str | None = OWNER_ENTRY_ID,
    device_ids: dict[str, str] | None = None,
    order: tuple[str, ...] | None = None,
) -> dict[str, conftest.RegistryEntry]:
    """Create the five v1.x catalog entities and return them keyed by kind.

    Insertion order follows ``order`` (default: the canonical kind tuple), which
    is what the order-independence test permutes - the sweep reads the persisted
    registry state, so insertion order must not influence the outcome.
    """
    created: dict[str, conftest.RegistryEntry] = {}
    for kind in order or account_logic.ACCOUNT_ENTITY_KINDS:
        # `_ENTITY_FIXTURES` carries the ENTITY domain ("sensor"/
        # "binary_sensor"), which is `RegistryEntry.domain`. `platform` is the
        # integration domain - never the other way round (HA semantics).
        entity_domain, entity_id = _ENTITY_FIXTURES[kind]
        entry = conftest.RegistryEntry(
            entity_id=entity_id,
            unique_id=f"{owner_entry_id or 'orphan'}_{kind}",
            platform=const.DOMAIN,
            domain=entity_domain,
            config_entry_id=owner_entry_id,
            device_id=(device_ids or {}).get(kind, CANONICAL_DEVICE_ID),
            id=f"registry-{kind}",
        )
        registries.entity.add(entry)
        created[kind] = entry
    return created


def _by_unique_id(
    registry: conftest.FakeEntityRegistry, unique_id: str
) -> conftest.RegistryEntry | None:
    """Return the registry entry owning ``unique_id`` (``None`` when absent)."""
    for entry in registry.entities.values():
        if entry.unique_id == unique_id:
            return entry
    return None


def _install_client(monkeypatch) -> MagicMock:
    """Inject a fake API client - the setup's first fetch must stay offline."""
    client = MagicMock()
    client.fetch_models = AsyncMock(return_value=["glm-5", "glm-5-free"])
    monkeypatch.setattr(account, "OpenCodeGoApiClient", lambda _session: client)
    monkeypatch.setattr(
        account, "async_get_clientsession", lambda _hass: MagicMock()
    )
    return client


# ============================================================================
# 1-3: the five-kind sweep, orphan entries, disabled-by-config entries
# ============================================================================

@pytest.mark.asyncio
async def test_sweep_covers_all_five_kinds(hass, registries):
    """[PLAN2-E7] One pass adopts all five kinds onto the account entry.

    Plan §8.3 (B4/E7): after a single run every kind carries the canonical
    ``unique_id`` and belongs to the account ConfigEntry - the legacy suffix and
    the workspace ownership are gone.
    """
    _seed_account_device(registries)
    _seed_legacy(registries)
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert report.errors == []
    assert len(report.renamed) == 5
    for kind in account_logic.ACCOUNT_ENTITY_KINDS:
        canonical = account_logic.canonical_unique_id(kind)
        adopted = _by_unique_id(registries.entity, canonical)
        assert adopted is not None, kind
        assert adopted.unique_id == canonical
        assert adopted.config_entry_id == ACCOUNT_ENTRY_ID
        assert adopted.device_id == CANONICAL_DEVICE_ID
        # entity_id is untouched -> history/area/labels survive (plan §3.3 3e).
        assert adopted.entity_id == _ENTITY_FIXTURES[kind][1]
    assert report.device_moved == CANONICAL_DEVICE_ID
    assert hass.data[const.DOMAIN][const.DATA_ADOPTION_REPORT] is report


@pytest.mark.asyncio
async def test_entity_with_config_entry_none_is_adopted(hass, registries):
    """[PLAN2-E7] An orphan registry entry (``config_entry_id is None``) is adopted.

    Plan §4.1: the candidate set is built from ``platform`` + ``unique_id``
    only, so a deleted workspace leaving the entry behind does not block the
    migration.
    """
    _seed_account_device(registries)
    created = _seed_legacy(registries)
    created["free_models"].config_entry_id = None
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    adopted = _by_unique_id(registries.entity, "account_free_models")
    assert adopted is not None
    assert adopted.config_entry_id == ACCOUNT_ENTRY_ID
    assert report.errors == []


@pytest.mark.asyncio
async def test_entity_without_entity_id_is_skipped(hass, registries):
    """[PLAN2-R6] A disabled-by-config entry (``entity_id is None``) is skipped.

    Plan §8.4: such an entry cannot be adopted, so the sweep must ignore it
    silently (debug log only) instead of writing or raising.
    """
    _seed_account_device(registries)
    _seed_legacy(registries)
    orphan = conftest.RegistryEntry(
        entity_id=None,
        unique_id="ghost_model_catalog",
        platform=const.DOMAIN,
        domain="sensor",
        config_entry_id=OWNER_ENTRY_ID,
        device_id=None,
        id="registry-ghost",
    )
    registries.entity.add(orphan)
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert report.errors == []
    assert orphan.unique_id == "ghost_model_catalog"
    assert orphan.config_entry_id == OWNER_ENTRY_ID
    assert None not in [entity_id for entity_id, _ in registries.entity.update_calls]


# ============================================================================
# 4-6: idempotency, ordering, order independence
# ============================================================================

@pytest.mark.asyncio
async def test_second_pass_writes_nothing(hass, registries):
    """[PLAN2-E7][PLAN2-B4] A second pass performs ZERO registry writes.

    Plan §4.5/§8.3: every write is guarded by the violation of its own
    predicate, so after one pass all and any subsequent passes are no-ops. Issue
    registry traffic may differ (the per-kind repairs converge either way).
    """
    _seed_account_device(registries)
    _seed_legacy(registries)
    entry = _entry()

    await account.async_adopt_account_entities(hass, entry)

    registries.entity.update_calls.clear()
    registries.entity.remove_calls.clear()
    registries.device.update_calls.clear()
    registries.device.remove_calls.clear()
    conftest.reset_write_log()

    report = await account.async_adopt_account_entities(hass, entry)

    assert registries.entity.update_calls == []
    assert registries.entity.remove_calls == []
    assert registries.device.update_calls == []
    assert registries.device.remove_calls == []
    assert conftest.WRITE_LOG == []
    assert (report.renamed, report.moved, report.merged_devices) == ([], [], [])
    assert report.device_moved is None
    assert report.errors == []


@pytest.mark.asyncio
async def test_entities_moved_before_device_move(hass, registries):
    """[PLAN2-E7] Every entity write happens BEFORE the device write.

    Plan §4.5/§11.3 V2: moving a device while entities still point at it would
    take those entities with it, so the entity sweep must finish first. Proven on
    the shared cross-registry journal (per-registry call lists cannot show
    interleaving).
    """
    _seed_account_device(registries)
    _seed_legacy(registries)
    entry = _entry()

    await account.async_adopt_account_entities(hass, entry)

    entity_writes = [
        index
        for index, (kind, _key) in enumerate(conftest.WRITE_LOG)
        if kind == "entity_update"
    ]
    device_writes = [
        index
        for index, (kind, _key) in enumerate(conftest.WRITE_LOG)
        if kind in ("device_update", "device_remove")
    ]
    assert len(entity_writes) == 10  # 5x unique_id + 5x config_entry_id
    assert device_writes, "the canonical device must be handed over"
    assert max(entity_writes) < min(device_writes)


@pytest.mark.asyncio
async def test_permutation_order_same_result(hass, registries):
    """[PLAN2-E7] The end state is independent of the registry insertion order.

    Plan §4.2/§8.3: the sweep reads persisted state and sorts candidates
    deterministically, so the order in which HA happens to have stored the five
    entries cannot influence the result. All 120 permutations of the five kinds
    are replayed against a FRESH registry set each; the resulting
    (unique_id, config_entry_id, device_id) triple per kind must be identical.
    """
    entry = _entry()
    states: set[tuple[tuple[str, str, str | None], ...]] = set()

    for order in itertools.permutations(account_logic.ACCOUNT_ENTITY_KINDS):
        fresh = _Registries(hass)
        _seed_account_device(fresh)
        _seed_legacy(fresh, order=order)
        report = await account.async_adopt_account_entities(hass, entry)
        assert report.errors == []
        states.add(
            tuple(
                (
                    str(_by_unique_id(fresh.entity, account_logic.canonical_unique_id(kind)).unique_id),
                    str(_by_unique_id(fresh.entity, account_logic.canonical_unique_id(kind)).config_entry_id),
                    _by_unique_id(fresh.entity, account_logic.canonical_unique_id(kind)).device_id,
                )
                for kind in account_logic.ACCOUNT_ENTITY_KINDS
            )
        )

    assert len(states) == 1
    state = states.pop()
    assert {unique_id for unique_id, _entry_id, _device in state} == {
        account_logic.canonical_unique_id(kind)
        for kind in account_logic.ACCOUNT_ENTITY_KINDS
    }


# ============================================================================
# 7-9: collisions - deterministic winner, disabled loser, repair issue
# ============================================================================

def _seed_collision(
    registries: _Registries, *, canonical_first: bool = True
) -> tuple[conftest.RegistryEntry, conftest.RegistryEntry]:
    """Register a canonical AND a legacy entry for ``model_catalog``."""
    canonical = conftest.RegistryEntry(
        entity_id="sensor.account_models",
        unique_id="account_model_catalog",
        platform=const.DOMAIN,
        domain="sensor",
        config_entry_id=ACCOUNT_ENTRY_ID,
        device_id=CANONICAL_DEVICE_ID,
        id="registry-canonical",
    )
    legacy = conftest.RegistryEntry(
        entity_id="sensor.ws_model_catalog",
        unique_id=f"{OWNER_ENTRY_ID}_model_catalog",
        platform=const.DOMAIN,
        domain="sensor",
        config_entry_id=OWNER_ENTRY_ID,
        device_id=CANONICAL_DEVICE_ID,
        id="registry-legacy",
    )
    for entry in (canonical, legacy) if canonical_first else (legacy, canonical):
        registries.entity.add(entry)
    return canonical, legacy


@pytest.mark.asyncio
async def test_collision_deterministic_winner(hass, registries):
    """[PLAN2-E7] The canonical entry wins - regardless of insert order.

    Plan §4.2: the sort key starts with "is canonical", so a duplicate can never
    steal the identity no matter which entry HA stored first.
    """
    entry = _entry()

    for canonical_first in (True, False):
        fresh = _Registries(hass)
        _seed_account_device(fresh)
        canonical, legacy = _seed_collision(fresh, canonical_first=canonical_first)
        report = await account.async_adopt_account_entities(hass, entry)

        assert report.renamed == []  # the winner already is canonical
        assert canonical.entity_id == "sensor.account_models"
        assert canonical.config_entry_id == ACCOUNT_ENTRY_ID
        assert canonical.device_id == CANONICAL_DEVICE_ID
        assert canonical.disabled_by is None
        assert [kind for kind, _loser in report.collisions] == ["model_catalog"]
        assert report.collisions[0][1] == legacy.entity_id


@pytest.mark.asyncio
async def test_collision_loser_disabled_not_removed(hass, registries):
    """[PLAN2-R5] The loser is disabled, re-owned, re-deviced - never deleted.

    Plan §4.3/§11.1 R5: deleting a user's registry entry is their decision, so
    the loser keeps its legacy ``unique_id`` (still distinguishable in the UI),
    is moved onto the account entry/device and disabled by the USER.
    """
    _seed_account_device(registries)
    _canonical, legacy = _seed_collision(registries)
    entry = _entry()

    await account.async_adopt_account_entities(hass, entry)

    assert legacy.unique_id == f"{OWNER_ENTRY_ID}_model_catalog"
    assert legacy.config_entry_id == ACCOUNT_ENTRY_ID
    assert legacy.device_id == CANONICAL_DEVICE_ID
    assert legacy.disabled_by == conftest.RegistryEntryDisabler.USER
    assert registries.entity.remove_calls == []


@pytest.mark.asyncio
async def test_disabled_canonical_holder_blocks_the_rename(hass, registries):
    """[PLAN2-R6] A canonical unique_id held by a disabled entry is never renamed.

    Plan §4.6: the ``async_get_entity_id`` pre-check cannot see a
    disabled-by-config entry (no ``entity_id``), so the defensive entry scan has
    to treat it as occupied - no ``new_unique_id`` write may be attempted (real
    HA would raise ``ValueError``), the candidate takes the loser path instead.
    """
    _seed_account_device(registries)
    holder = registries.entity.add(
        conftest.RegistryEntry(
            entity_id=None,
            unique_id="account_model_catalog",
            platform=const.DOMAIN,
            domain="sensor",
            config_entry_id=OWNER_ENTRY_ID,
            id="registry-holder",
        )
    )
    legacy = registries.entity.add(
        conftest.RegistryEntry(
            entity_id="sensor.ws_model_catalog",
            unique_id=f"{OWNER_ENTRY_ID}_model_catalog",
            platform=const.DOMAIN,
            domain="sensor",
            config_entry_id=OWNER_ENTRY_ID,
            device_id=CANONICAL_DEVICE_ID,
            id="registry-legacy",
        )
    )
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert report.renamed == []
    assert [changes for _id, changes in registries.entity.update_calls
            if "new_unique_id" in changes] == []
    assert holder.unique_id == "account_model_catalog"
    assert legacy.unique_id == f"{OWNER_ENTRY_ID}_model_catalog"
    assert legacy.disabled_by == conftest.RegistryEntryDisabler.USER
    assert report.collisions == [("model_catalog", "sensor.ws_model_catalog")]
    issue = registries.issue.issues["account_entity_collision_model_catalog"]
    assert issue["translation_placeholders"]["winner"] == "account_model_catalog"


@pytest.mark.asyncio
async def test_collision_creates_repair_issue(hass, registries):
    """[PLAN2-E7][PLAN2-M5] Collision -> stable repair issue, resolved -> deleted.

    Plan §4.4: the issue id is per kind (so it is updated instead of
    duplicated), the translation key is shared and the placeholders name kind,
    winner and losers. Once the duplicate is gone, the next pass deletes the
    issue - the self-healing half of the same contract.
    """
    _seed_account_device(registries)
    _canonical, legacy = _seed_collision(registries)
    entry = _entry()

    await account.async_adopt_account_entities(hass, entry)

    issue_id = "account_entity_collision_model_catalog"
    issue = registries.issue.issues[issue_id]
    assert issue["translation_key"] == "account_entity_collision"
    assert issue["translation_placeholders"] == {
        "kind": "model_catalog",
        "winner": "sensor.account_models",
        "losers": legacy.entity_id,
    }
    assert issue["is_fixable"] is False

    # Clean-up: the user (or any other path) removed the duplicate.
    registries.entity.entities.pop(legacy.entity_id, None)
    await account.async_adopt_account_entities(hass, entry)

    assert issue_id not in registries.issue.issues
    assert issue_id in registries.issue.deleted


# ============================================================================
# 10-12: duplicate devices and the device hand-over
# ============================================================================

@pytest.mark.asyncio
async def test_device_duplicate_merged_and_reported(hass, registries):
    """[PLAN2-E7] An EMPTY duplicate device is removed and reported.

    Plan §3.3 step 3b/§8.3: the candidates pointed at a second device before the
    sweep; once the entity sweep moved them onto the canonical device it is
    empty and may disappear - with a convergent `account_device_merged` repair.
    """
    _seed_account_device(registries)
    registries.device.add(
        conftest.DeviceEntry(
            id="dev-duplicate",
            name="Go Gauge (alt)",
            config_entries={OWNER_ENTRY_ID},
        )
    )
    _seed_legacy(registries, device_ids={"model_catalog": "dev-duplicate"})
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert registries.device.remove_calls == ["dev-duplicate"]
    assert report.merged_devices == ["dev-duplicate"]
    assert "dev-duplicate" not in registries.device.devices
    merged_issue = registries.issue.issues["account_device_merged"]
    assert merged_issue["translation_placeholders"] == {"device": "dev-duplicate"}
    assert "account_device_not_empty" not in registries.issue.issues


@pytest.mark.asyncio
async def test_device_duplicate_not_empty_keeps_device_and_reports(hass, registries):
    """[PLAN2-R5] A non-empty duplicate device stays and raises a repair.

    Two blockers are covered, because both mean "still in use": a remaining
    entity (here a workspace usage sensor) and a child device. Neither may be
    deleted, and neither may vanish silently.
    """
    _seed_account_device(registries)
    registries.device.add(
        conftest.DeviceEntry(
            id="dev-with-entity",
            name="Go Gauge (Entity)",
            config_entries={OWNER_ENTRY_ID},
        )
    )
    registries.device.add(
        conftest.DeviceEntry(
            id="dev-with-child",
            name="Go Gauge (Child)",
            config_entries={OWNER_ENTRY_ID},
        )
    )
    registries.device.add(
        conftest.DeviceEntry(id="dev-child", via_device_id="dev-with-child")
    )
    # A non-candidate entity keeps "dev-with-entity" occupied.
    registries.entity.add(
        conftest.RegistryEntry(
            entity_id="sensor.ws_usage",
            unique_id=f"{OWNER_ENTRY_ID}_usage",
            platform=const.DOMAIN,
            domain="sensor",
            config_entry_id=OWNER_ENTRY_ID,
            device_id="dev-with-entity",
        )
    )
    _seed_legacy(
        registries,
        device_ids={
            "model_catalog": "dev-with-entity",
            "free_models": "dev-with-child",
        },
    )
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert registries.device.remove_calls == []
    assert report.merged_devices == []
    assert {"dev-with-entity", "dev-with-child"} <= set(registries.device.devices)
    assert report.errors  # reported, not fatal
    not_empty_issue = registries.issue.issues["account_device_not_empty"]
    assert not_empty_issue["translation_placeholders"]["device"] in {
        "Go Gauge (Entity)",
        "Go Gauge (Child)",
    }


@pytest.mark.asyncio
async def test_workspace_device_is_never_treated_as_duplicate(hass, registries):
    """[PLAN2-R5] A pure workspace device is not a duplicate and raises no issue.

    Project-specific guard: workspace devices carry the usage entities and the
    identifier ``(DOMAIN, <workspace entry_id>)``. Even when a stray catalog
    entry referenced one before the sweep, it must never be merged, never be
    reported as "not empty" and never receive a repair issue.
    """
    hass.config_entries.async_entries = MagicMock(
        return_value=[_workspace_entry(), _entry()]
    )
    _seed_account_device(registries)
    workspace_device = conftest.DeviceEntry(
        id="dev-workspace",
        name="Go Gauge Honcho",
        identifiers={(const.DOMAIN, OWNER_ENTRY_ID)},
        config_entries={OWNER_ENTRY_ID},
    )
    registries.device.add(workspace_device)
    registries.entity.add(
        conftest.RegistryEntry(
            entity_id="sensor.go_gauge_honcho_usage",
            unique_id=f"{OWNER_ENTRY_ID}_usage",
            platform=const.DOMAIN,
            domain="sensor",
            config_entry_id=OWNER_ENTRY_ID,
            device_id="dev-workspace",
        )
    )
    _seed_legacy(registries, device_ids={"model_catalog": "dev-workspace"})
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert "dev-workspace" in registries.device.devices
    assert "dev-workspace" not in registries.device.remove_calls
    assert report.merged_devices == []
    assert "account_device_not_empty" not in registries.issue.issues


@pytest.mark.asyncio
async def test_device_move_never_falls_back_to_a_workspace_device(hass, registries):
    """[REVIEW-2.0.0-BLOCKER3] A workspace device is never a move target.

    ``async_update_device(new_config_entry_id=...)`` REPLACES the ownership set,
    and HA then removes every entity whose ``config_entry_id`` is no longer
    listed on the device. Falling back to the workspace device (the only
    candidate here) would therefore delete the workspace usage entities. The
    adoption must write nothing instead; HA creates the account device with the
    platform forward.
    """
    hass.config_entries.async_entries = MagicMock(
        return_value=[_workspace_entry(), _entry()]
    )
    workspace_device = conftest.DeviceEntry(
        id="dev-workspace",
        name="Go Gauge Honcho",
        identifiers={(const.DOMAIN, OWNER_ENTRY_ID)},
        config_entries={OWNER_ENTRY_ID},
    )
    registries.device.add(workspace_device)
    usage_entity = conftest.RegistryEntry(
        entity_id="sensor.go_gauge_honcho_usage",
        unique_id=f"{OWNER_ENTRY_ID}_usage",
        platform=const.DOMAIN,
        domain="sensor",
        config_entry_id=OWNER_ENTRY_ID,
        device_id="dev-workspace",
    )
    registries.entity.add(usage_entity)
    _seed_legacy(registries, device_ids={"model_catalog": "dev-workspace"})
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    # No write at all: neither the update nor a removal happened.
    assert report.device_moved is None
    assert registries.device.update_calls == []
    assert registries.device.remove_calls == []
    # The workspace device and its usage entity are untouched.
    assert registries.device.async_get("dev-workspace") is workspace_device
    assert workspace_device.identifiers == {(const.DOMAIN, OWNER_ENTRY_ID)}
    assert workspace_device.config_entries == {OWNER_ENTRY_ID}
    current_usage = registries.entity.async_get("sensor.go_gauge_honcho_usage")
    assert current_usage is usage_entity
    assert current_usage.device_id == "dev-workspace"
    assert current_usage.config_entry_id == OWNER_ENTRY_ID
    assert report.errors == []


@pytest.mark.asyncio
async def test_device_move_never_falls_back_to_a_foreign_owned_device(
    hass, registries
):
    """[REVIEW-2.0.0-FOLLOWUP2] A foreign-owned device is never a move target.

    ``async_update_device(new_config_entry_id=...)`` REPLACES the whole
    ownership set, and HA then removes every entity whose ``config_entry_id`` is
    no longer listed on the device. A candidate without the workspace identifier
    but owned by ANOTHER integration therefore must not be adopted either: its
    ownership set must stay untouched. The guard is checked directly on
    ``_move_account_device`` so the assertion cannot be blurred by the
    duplicate-device merge (which would legitimately report a not-empty device).
    """
    hass.config_entries.async_entries = MagicMock(
        return_value=[_workspace_entry(), _entry()]
    )
    foreign_device = conftest.DeviceEntry(
        id="dev-foreign",
        name="Fremdgerät",
        identifiers={("other_domain", "device-1")},
        config_entries={"other-entry"},
    )
    registries.device.add(foreign_device)
    report = account.AdoptionReport()

    account._move_account_device(
        hass, _entry(), report, registries.device, {"dev-foreign"}
    )

    # No write at all and nothing reported as moved.
    assert report.device_moved is None
    assert registries.device.update_calls == []
    assert registries.device.remove_calls == []
    assert conftest.WRITE_LOG == []
    # The foreign device keeps its ownership set and still exists.
    assert registries.device.async_get("dev-foreign") is foreign_device
    assert foreign_device.config_entries == {"other-entry"}
    assert report.errors == []


@pytest.mark.asyncio
async def test_device_move_falls_back_to_a_go_gauge_owned_device(hass, registries):
    """[REVIEW-2.0.0-FOLLOWUP2] A purely go_gauge-owned device IS adopted.

    Counterpart of the foreign-owner guard: a legacy device owned only by
    go_gauge entries (here the workspace, the realistic v1.x state) stays a valid
    fallback target, so the hardening cannot silently disable the hand-over for
    existing installations.
    """
    hass.config_entries.async_entries = MagicMock(
        return_value=[_workspace_entry(), _entry()]
    )
    legacy_device = conftest.DeviceEntry(
        id="dev-legacy",
        name="Go Gauge Alt",
        config_entries={OWNER_ENTRY_ID},
    )
    registries.device.add(legacy_device)
    report = account.AdoptionReport()

    account._move_account_device(
        hass, _entry(), report, registries.device, {"dev-legacy"}
    )

    assert report.device_moved == "dev-legacy"
    assert legacy_device.config_entries == {ACCOUNT_ENTRY_ID}
    assert registries.device.update_calls == [
        ("dev-legacy", {"new_config_entry_id": ACCOUNT_ENTRY_ID})
    ]


@pytest.mark.asyncio
async def test_device_move_reports_only_a_device_that_still_exists(hass, registries):
    """[REVIEW-2.0.0-MINOR] ``device_moved`` requires a real post-write device.

    ``async_update_device`` returns ``None`` for an unknown id. A move that never
    happened must not be reported (the diagnostics export would otherwise claim
    a device that is gone).
    """
    hass.config_entries.async_entries = MagicMock(
        return_value=[_workspace_entry(), _entry()]
    )
    _seed_account_device(registries)
    _seed_legacy(registries)
    entry = _entry()

    real_update = registries.device.async_update_device

    def _vanishing_update(device_id: str, **kwargs: Any):
        result = real_update(device_id, **kwargs)
        # Simulate HA dropping the device between lookup and write.
        registries.device.devices.pop(device_id, None)
        return result

    registries.device.async_update_device = _vanishing_update  # type: ignore[method-assign]

    report = await account.async_adopt_account_entities(hass, entry)

    assert registries.device.update_calls, "the write itself still happened"
    assert report.device_moved is None


@pytest.mark.asyncio
async def test_device_move_without_candidates(hass, registries):
    """[PLAN2-E7] No device and no candidate -> nothing is written.

    Plan §3.3 step 3c/§8.4: a fresh install has neither a canonical account
    device nor a legacy catalog device; HA creates the device on the phase-C
    platform forward, so the adoption must not invent one.
    """
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert report.device_moved is None
    assert registries.device.update_calls == []
    assert registries.device.remove_calls == []
    assert registries.entity.update_calls == []
    assert report.errors == []


# ============================================================================
# 13-15: error paths and setup wiring
# ============================================================================

@pytest.mark.asyncio
async def test_transfer_typeerror_creates_issue_and_setup_stays_true(
    hass, registries, monkeypatch
):
    """[PLAN2-R1] An HA version without ``new_config_entry_id`` only raises a repair.

    Plan §11.1 R1: ``TypeError`` from ``async_update_device`` must be turned into
    ``adoption_unsupported`` + a report error, and the setup must still return
    ``True`` - the account runtime keeps working, the entities simply stay with
    their old device owner until HA is updated.
    """
    _install_client(monkeypatch)
    _seed_account_device(registries)
    _seed_legacy(registries)
    registries.device.raise_on_update = TypeError(
        "async_update_device() got an unexpected keyword argument "
        "'new_config_entry_id'"
    )
    entry = _entry()

    result = await account.async_setup_account_entry(hass, entry)

    report = hass.data[const.DOMAIN][const.DATA_ADOPTION_REPORT]
    assert result is True
    assert report.errors
    assert report.device_moved is None
    issue = registries.issue.issues["adoption_unsupported"]
    assert issue["translation_placeholders"]["error"]
    assert "new_config_entry_id" in issue["translation_placeholders"]["error"]


@pytest.mark.asyncio
async def test_value_error_from_update_entity_goes_to_errors(hass, registries):
    """[PLAN2-R6] A rejected entity write is collected, the sweep continues.

    Plan §4.6: every entity is guarded individually, so one failing
    ``new_unique_id`` write neither aborts the pass nor the other kinds. The
    account setup itself is not involved here - the pure adoption API must not
    raise either.
    """
    _seed_account_device(registries)
    _seed_legacy(registries)
    registries.entity.raise_on_update = ValueError("unique_id already taken")
    entry = _entry()

    report = await account.async_adopt_account_entities(hass, entry)

    assert len(report.errors) == len(account_logic.ACCOUNT_ENTITY_KINDS)
    for kind in account_logic.ACCOUNT_ENTITY_KINDS:
        assert any(error.startswith(f"{kind}:") for error in report.errors)
    assert report.renamed == []
    assert hass.data[const.DOMAIN][const.DATA_ADOPTION_REPORT] is report
    # The device hand-over is independent of the entity writes and still works.
    assert report.device_moved == CANONICAL_DEVICE_ID


@pytest.mark.asyncio
async def test_adoption_is_wired_into_setup(hass, registries, monkeypatch):
    """[PLAN2-E7][PLAN2-M5] Setup calls the adoption; its failure never fails setup.

    Plan §3.3: the registry work must run inside the account setup (before the
    phase-C forward). Plan §2.4/M5: even a raising adoption is swallowed - setup
    stays ``True`` so the workspaces can never be taken down by it.
    """
    _install_client(monkeypatch)
    spy = AsyncMock()
    monkeypatch.setattr(account, "async_adopt_account_entities", spy)
    entry = _entry()

    assert await account.async_setup_account_entry(hass, entry) is True
    spy.assert_awaited_once_with(hass, entry)

    monkeypatch.setattr(
        account,
        "async_adopt_account_entities",
        AsyncMock(side_effect=RuntimeError("registry exploded")),
    )

    assert await account.async_setup_account_entry(hass, entry) is True


# ============================================================================
# Real HA registry semantics (E2E 2026-10-25 regressions)
# ============================================================================
#
# The live instance exposed that ``RegistryEntry.platform`` is the INTEGRATION
# domain (``go_gauge``) and ``RegistryEntry.domain`` the ENTITY domain
# (``sensor``/``binary_sensor``). The sweep filtered the two the other way
# round, found no candidate at all and every legacy entity was deleted by the
# following device hand-over. These tests fail against the inverted filters."

@pytest.mark.asyncio
async def test_sweep_finds_legacy_entities_with_real_ha_semantics(
    hass, registries
):
    """[E2E-2026-10-25] The sweep adopts all five entities of the real layout.

    Seeds ``platform=DOMAIN``/``domain=<entity domain>`` exactly like HA stores
    them, then proves the full chain: unique_id -> config_entry_id -> device,
    and - the part the live run lost - that no entity is removed and every
    ``entity_id`` (and therefore its history) survives the device move.
    """
    _seed_account_device(registries, owners={OWNER_ENTRY_ID})
    seeded = _seed_legacy(registries)
    entity_ids_before = {
        kind: entry.entity_id for kind, entry in seeded.items()
    }

    report = await account.async_adopt_account_entities(hass, _entry())

    assert report.errors == []
    assert report.renamed, "real-layout candidates must be found and renamed"
    for kind in account_logic.ACCOUNT_ENTITY_KINDS:
        canonical = account_logic.canonical_unique_id(kind)
        adopted = _by_unique_id(registries.entity, canonical)
        assert adopted is not None, f"{kind}: not adopted"
        assert adopted.config_entry_id == ACCOUNT_ENTRY_ID
        assert adopted.device_id == CANONICAL_DEVICE_ID
        assert adopted.entity_id == entity_ids_before[kind]
        assert adopted.disabled_by is None

    # The device hand-over must not take a single entity with it.
    assert registries.entity.remove_calls == []
    assert registries.device.remove_calls == []
    assert registries.device.devices[CANONICAL_DEVICE_ID].config_entries == {
        ACCOUNT_ENTRY_ID
    }
    assert len(registries.entity.entities) == 5


def test_canonical_blocker_uses_ha_lookup_signature(registries):
    """[E2E-2026-10-25] ``async_get_entity_id`` gets ``(domain, platform, uid)``.

    Spying on the argument order is the point: the inverted call silently
    returns ``None`` in HA and lets a rename collide with ``ValueError``.
    """
    calls: list[tuple[str, str, str]] = []

    def _spy(domain: str, platform: str, unique_id: str):
        calls.append((domain, platform, unique_id))
        assert platform == const.DOMAIN, (
            "second argument must be the INTEGRATION domain"
        )
        assert domain == "sensor", "first argument must be the ENTITY domain"
        return None

    registries.entity.async_get_entity_id = _spy
    winner = registries.entity.add(conftest.RegistryEntry(
        entity_id="sensor.ws_model_catalog",
        unique_id=f"{OWNER_ENTRY_ID}_model_catalog",
        platform=const.DOMAIN,
        domain="sensor",
    ))

    blocker = account._canonical_blocker(
        registries.entity, "sensor", "account_model_catalog", winner
    )

    assert blocker is None
    assert calls == [("sensor", const.DOMAIN, "account_model_catalog")]

    # Second scenario: a real holder of the canonical unique_id -> loser path.
    holder = registries.entity.add(conftest.RegistryEntry(
        entity_id="sensor.account_models",
        unique_id="account_model_catalog",
        platform=const.DOMAIN,
        domain="sensor",
    ))
    blocker = account._canonical_blocker(
        registries.entity, "sensor", "account_model_catalog", winner
    )
    assert blocker is holder


def test_canonical_device_uses_registry_identifier_lookup():
    """[HA 2026.8+] The primary path is ``async_get_devices(identifiers=...)``.

    The spy pins the contract: exactly one call with the identifier set
    ``{(DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)}`` and the returned entries are
    ranked deterministically (smallest ``id``). This is HA's non-deprecated
    identifier lookup - the replacement for ``async_get_device`` and for using
    the registry's ``devices`` mapping as a lookup table.
    """
    identifier = (const.DOMAIN, const.ACCOUNT_DEVICE_IDENTIFIER)
    calls: list[set[tuple[str, str]]] = []
    devices = [
        conftest.DeviceEntry(id="zzz", identifiers={identifier}),
        conftest.DeviceEntry(id="aaa", identifiers={identifier}),
    ]

    class _SpyRegistry:
        """Registry double whose only surface is the identifier lookup."""

        def async_get_devices(
            self, *, identifiers: set[tuple[str, str]] | None = None
        ) -> list[conftest.DeviceEntry]:
            calls.append(set(identifiers or set()))
            return devices

        def __getattr__(self, name: str):
            raise AssertionError(f"unexpected registry access: {name}")

    found = account._canonical_device(_SpyRegistry())

    assert found is not None
    assert found.id == "aaa", "the smallest id must win deterministically"
    assert calls == [{identifier}], "lookup must be called exactly once"

    # An empty lookup result means "no canonical device" - not an error.
    class _EmptyRegistry(_SpyRegistry):
        def async_get_devices(self, *, identifiers=None):
            return []

    assert account._canonical_device(_EmptyRegistry()) is None


def test_canonical_device_falls_back_to_registry_entries():
    """[HA < 2026.8] Without the lookup the entities are enumerated.

    Old cores only expose the ``devices`` mapping; the entries are read through
    the production helper ``_device_entries`` (2026.8 mapping generation), so
    neither ``async_get_device`` nor a deprecated mapping access is needed. Two
    identifier matches resolve deterministically (smallest ``id``).
    """
    identifier = (const.DOMAIN, const.ACCOUNT_DEVICE_IDENTIFIER)

    class _LegacyMappingRegistry:
        """HA 2026.8 surface: plain mapping, no ``async_get_devices``."""

        def __init__(self, devices: dict[str, conftest.DeviceEntry]) -> None:
            self.devices = devices

        def __getattr__(self, name: str):
            if name == "async_get_devices":
                raise AttributeError(name)  # absent on an old core
            raise AssertionError(f"deprecated/unsupported access: {name}")

    devices = {
        "zzz": conftest.DeviceEntry(id="zzz", identifiers={identifier}),
        "aaa": conftest.DeviceEntry(id="aaa", identifiers={identifier}),
        "other": conftest.DeviceEntry(
            id="other", identifiers={(const.DOMAIN, "ws")}
        ),
    }

    found = account._canonical_device(_LegacyMappingRegistry(devices))

    assert found is not None
    assert found.id == "aaa", "the smallest id must win deterministically"
    assert account._canonical_device(_LegacyMappingRegistry({})) is None
    # A registry without the expected surface stays a normal "no device".
    assert account._canonical_device(object()) is None


def test_canonical_device_falls_back_on_older_signature():
    """[HA < 2026.8] A lookup with a different signature still resolves.

    Signature drift surfaces as ``TypeError``; the identifier match then runs
    over the enumerated entries instead of returning ``None``.
    """
    identifier = (const.DOMAIN, const.ACCOUNT_DEVICE_IDENTIFIER)
    device = conftest.DeviceEntry(
        id=CANONICAL_DEVICE_ID, identifiers={identifier}
    )

    class _WrongSignatureRegistry:
        """Lookup exists but takes no keyword arguments -> ``TypeError``."""

        def __init__(self) -> None:
            self.devices = {device.id: device}

        def async_get_devices(self):  # signature probe
            raise AssertionError("must not be callable with identifiers=")

        def __getattr__(self, name: str):
            raise AssertionError(f"deprecated/unsupported access: {name}")

    assert account._canonical_device(_WrongSignatureRegistry()) is device


def test_device_registry_without_collection_reads_as_empty():
    """[HA 2026.9] A registry without a loaded ``devices`` collection is empty.

    ``DeviceRegistry.devices`` only comes into existence during
    ``_async_load()``; before that - and on foreign registry doubles - the
    attribute is simply absent. That pre-load state must read as "no devices
    known": ``_device_entries`` returns an empty list instead of raising
    ``AttributeError``, so the UNGUARDED callers (``_device_has_dependencies``,
    the survivor scan in ``_move_account_device``) can never abort an adoption
    mid-run. On a loaded registry the behaviour is unchanged.
    """

    class _PreLoadRegistry:
        """Pre-load registry: every missing attribute raises, like HA."""

        def __getattr__(self, name: str):
            raise AttributeError(name)

    class _EmptyEntityRegistry:
        """Entity registry double with no entities (no dependency)."""

        entities: dict[str, Any] = {}

    registry = _PreLoadRegistry()

    assert account._device_entries(registry) == []
    assert account._canonical_device(registry) is None
    assert (
        account._device_has_dependencies(
            _EmptyEntityRegistry(), registry, "dev-x"
        )
        is False
    )


def test_device_has_dependencies_covers_child_devices(registries):
    """[PLAN2-R5] A child device (``via_device_id``) counts as a dependency.

    Both blockers keep a duplicate device alive: entities and child devices.
    The child scan must not need the deprecated ``devices`` mapping, and a device
    with neither stays removable (``False`` + no ``async_remove_device`` call).
    """
    registries.device.add(conftest.DeviceEntry(id="dev-parent"))
    registries.device.add(
        conftest.DeviceEntry(id="dev-child", via_device_id="dev-parent")
    )

    assert account._device_has_dependencies(
        registries.entity, registries.device, "dev-parent"
    ) is True
    assert account._device_has_dependencies(
        registries.entity, registries.device, "dev-child"
    ) is False
    assert registries.device.remove_calls == []


class _StrictDevicesCollection:
    """HA 2026.9+ device collection: iteration yes, mapping access no.

    On a real HA >= 2026.9 the registry's ``devices`` attribute is a view whose
    iteration yields ``DeviceEntry`` objects (the supported enumeration) while
    every mapping access - ``.values()``, ``[...]``, ``.get()`` - reports the
    deprecation (removed in 2027.9). The double mirrors exactly that: iteration
    works, any other attribute/method access fails loudly.
    """

    def __init__(self, inner: conftest.FakeDeviceRegistry) -> None:
        self._inner = inner

    def __iter__(self):
        return iter(self._inner.devices.values())

    def __len__(self) -> int:
        return len(self._inner.devices)

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            "deprecated mapping access on the devices collection "
            f"(removed in HA 2027.9): {name}"
        )


class _StrictDeviceRegistry:
    """``FakeDeviceRegistry`` wrapper that forbids the deprecated mapping access.

    ``devices`` hands out the 2026.9 collection double (iteration only), so a
    reintroduced ``dev_reg.devices.values()`` fails loudly instead of passing
    silently. Every other attribute (``async_get``, ``async_get_devices``,
    ``async_update_device``, ...) is delegated to the inner fake.
    """

    def __init__(self, inner: conftest.FakeDeviceRegistry) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        if name == "devices":
            return _StrictDevicesCollection(self._inner)
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_production_never_reads_the_deprecated_devices_mapping(
    hass, registries
):
    """[HA 2027.9] Canonical lookup, child check and device fallback stay clean.

    The strict double models HA 2026.9: the registry's ``devices`` attribute is
    iterable (supported) but every mapping method raises ``AssertionError``. The
    direct calls therefore fail loudly on a reintroduced mapping access; in the
    end-to-end adoption the same access would surface in ``report.errors`` (the
    adoption never raises), so both are asserted. A device with a child covers
    ``_device_has_dependencies``, the fallback sub-scenario covers
    ``_move_account_device``'s survivor scan.
    """
    hass.config_entries.async_entries = MagicMock(
        return_value=[_workspace_entry(), _entry()]
    )
    _seed_account_device(registries)
    registries.device.add(
        conftest.DeviceEntry(
            id="dev-duplicate",
            name="Go Gauge (alt)",
            config_entries={OWNER_ENTRY_ID},
        )
    )
    registries.device.add(
        conftest.DeviceEntry(id="dev-child", via_device_id="dev-duplicate")
    )
    _seed_legacy(registries, device_ids={"model_catalog": "dev-duplicate"})

    strict = _StrictDeviceRegistry(registries.device)

    # 1. primary lookup path
    canonical = account._canonical_device(strict)
    assert canonical is not None
    assert canonical.id == CANONICAL_DEVICE_ID

    # 2. child-device check (enumeration through the devices collection)
    assert account._device_has_dependencies(
        registries.entity, strict, "dev-duplicate"
    ) is True

    # 3. end-to-end: the non-empty duplicate stays, nothing failed
    conftest.install_device_registry_stub(strict)  # type: ignore[arg-type]
    account.dr = sys.modules["homeassistant.helpers.device_registry"]

    report = await account.async_adopt_account_entities(hass, _entry())

    # A mapping access inside the adoption surfaces as "adoption: deprecated
    # ..." (the adoption never raises) - that would fail here.
    assert report.errors == ["device dev-duplicate: not empty - left in place"]
    assert report.merged_devices == []
    assert report.device_moved == CANONICAL_DEVICE_ID
    assert registries.device.remove_calls == []
    assert "dev-duplicate" in registries.device.devices
    assert "account_device_not_empty" in registries.issue.issues

    # 4. fallback survivor enumeration of _move_account_device (no canonical
    #    device in this registry, so the fallback branch is taken)
    inner = conftest.FakeDeviceRegistry(registries.entity)
    inner.add(
        conftest.DeviceEntry(id="dev-legacy", config_entries={OWNER_ENTRY_ID})
    )
    fallback_report = account.AdoptionReport()

    account._move_account_device(
        hass,
        _entry(),
        fallback_report,
        _StrictDeviceRegistry(inner),
        {"dev-legacy"},
    )

    assert fallback_report.device_moved == "dev-legacy"
    assert fallback_report.errors == []
    assert inner.async_get("dev-legacy").config_entries == {ACCOUNT_ENTRY_ID}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
