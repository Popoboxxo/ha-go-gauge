#!/usr/bin/env python3
"""Shared Home-Assistant module fakes for the whole test suite.

Centralizes what used to be duplicated, inline `sys.modules` stub setup at
the top of each `tests/test_*.py` file (see docs/AUDIT-2026-09-04.md, P1
finding "unvollstaendige HA-Stubs in conftest"). New test files that import
`custom_components/go_gauge/*` no longer need to hand-roll their own base
module fakes - this file guarantees every `homeassistant.*` submodule
actually imported by the integration (verified against the real code, see
list below) exists in `sys.modules` before any test module is collected.

Deliberately NOT the plain "try: import homeassistant / except ImportError"
guard from the generic HACS test-trick skill: pytest.ini documents that this
repo intentionally never relies on a REAL `homeassistant` install for these
unit tests, even when one happens to be present in the dev venv (as it is
here - eager imports of its submodule tree have been known to crash on
unrelated dependency mismatches, e.g. pyOpenSSL/cryptography, on some
machines). A bare `import homeassistant` succeeding here would only prove
the top-level package is installed - it says nothing about whether the
*specific* submodules below (e.g. homeassistant.helpers.update_coordinator)
can be imported cleanly, and mixing a handful of real submodules with the
rest faked is worse than either extreme. So - matching the convention every
existing test file in this repo already follows - the fakes are installed
unconditionally.

Each faked module is a `_Flexible` `ModuleType`: any attribute access (other
than dunders) lazily returns a fresh, subclassable, empty class. A plain
`unittest.mock.MagicMock()` instance would NOT work here, because the real
integration code subclasses these HA base classes directly (e.g.
`class UsagePercentSensor(GoGaugeEntityBase, SensorEntity)`), and Python
cannot use a Mock *instance* as a base class - it must be an actual class
object. Individual test files still customize specific attributes (e.g.
`DataUpdateCoordinator.__class_getitem__`, `CoordinatorEntity.available`,
enum-like `SensorStateClass`/`SensorDeviceClass` members) on top of these
base fakes for their own scenario - that part is intentionally NOT
centralized here, since it differs per test and isn't generically reusable.

`voluptuous` is a real pip package with no `homeassistant` dependency (see
project rule python-conventions / integration-development) and must NEVER be
faked - it is left alone here so the real install (already a project
dependency) is used as-is.
"""
from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock


class _Flexible(types.ModuleType):
    """Fake module: any non-dunder attribute access yields a fresh class.

    This makes the fake usable both as a plain namespace (`mod.SOME_CONST`)
    AND as a source of subclassable base classes (`class X(mod.SomeEntity)`),
    which a bare `MagicMock()` cannot provide.
    """

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


# Full list of homeassistant.* dotted module paths actually imported across
# custom_components/go_gauge/*.py (verified 2026-09-05 via
# `grep -rn "^(from|import) homeassistant" custom_components/go_gauge`):
#
#   __init__.py       -> core, config_entries
#   config_flow.py     -> homeassistant (config_entries), core, data_entry_flow
#   coordinator.py      -> core, helpers.aiohttp_client, helpers.update_coordinator
#   entity.py            -> config_entries, core, helpers.update_coordinator
#   sensor.py             -> components.sensor, config_entries, core,
#                            helpers.entity_platform
#   binary_sensor.py       -> components.binary_sensor, config_entries, core,
#                            helpers.entity_platform
#   button.py                -> components.button, config_entries, core,
#                            helpers.entity_platform
#   number.py                 -> components.number, config_entries, core,
#                            helpers.entity_platform
#   switch.py                  -> components.switch, config_entries, core,
#                            helpers.entity_platform
#   diagnostics.py               -> components.diagnostics, config_entries,
#                            loader
#
# Plus a few defensive additions (not currently imported, but named in the
# audit / the generic skill baseline) so a future test file that touches
# them doesn't hit a fresh ImportError: helpers.entity, helpers.storage,
# helpers.service, helpers.config_validation, exceptions, const, util,
# util.dt. helpers.entity_registry is imported (function-locally) by the
# v6 entity-name migration in __init__.py - see
# tests/test_entity_name_migration.py.
_FAKE_HA_MODULES = [
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.data_entry_flow",
    "homeassistant.const",
    "homeassistant.exceptions",
    "homeassistant.loader",
    "homeassistant.helpers",
    "homeassistant.helpers.entity",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.entity_registry",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.issue_registry",
    "homeassistant.helpers.event",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.service",
    "homeassistant.helpers.config_validation",
    "homeassistant.util",
    "homeassistant.util.dt",
    "homeassistant.components",
    "homeassistant.components.sensor",
    "homeassistant.components.binary_sensor",
    "homeassistant.components.button",
    "homeassistant.components.number",
    "homeassistant.components.switch",
    "homeassistant.components.diagnostics",
]

class _FakeConfigFlow:
    """Minimal stand-in for ``homeassistant.config_entries.ConfigFlow``.

    The integration declares ``class GoGaugeConfigFlow(ConfigFlow,
    domain=DOMAIN)``. Python forwards that ``domain=`` class keyword to the
    base class' ``__init_subclass__``; real Home Assistant defines one that
    consumes it, but a generic ``_Flexible`` class does not, so the class
    statement would raise ``TypeError``. This base accepts and discards
    ``domain`` (plus any further class kwargs) exactly like the real
    machinery, and carries the ``VERSION`` attribute the flow overrides.
    """

    VERSION: int = 1

    def __init_subclass__(
        cls, *, domain: str | None = None, **kwargs: object
    ) -> None:
        """Swallow the ``domain=`` class keyword, mirroring real HA."""
        super().__init_subclass__(**kwargs)


def _identity_callback(func: object) -> object:
    """Stand-in for the ``@homeassistant.core.callback`` decorator (identity).

    The real symbol is a pass-through marker decorator, so returning the
    wrapped callable unchanged is sufficient for the offline tests.
    """
    return func


class _ClientTimeout:
    """Minimal stand-in for ``aiohttp.ClientTimeout``."""

    def __init__(self, total: float | None = None) -> None:
        self.total = total


def install_ha_stubs() -> None:
    """(Re)install the shared homeassistant/aiohttp fakes - idempotently.

    Called once below at import time. It is ALSO re-callable so a test module
    that must import the real config_flow (see tests/test_config_flow.py) can
    guarantee the config-flow-specific stubs are in place immediately before
    the import: other test modules run their own inline fake setup at
    collection time and REPLACE these sys.modules entries with their own local
    `_Flexible` fakes, which drops the `ConfigFlow`/`callback` stubs. Since
    collection order is alphabetical, some of those modules load before
    test_config_flow, so its loader must not depend on collection order.

    Uses `setdefault` for the base modules so a re-call never throws away a
    module object the integration code already imported against.
    """
    for name in _FAKE_HA_MODULES:
        sys.modules.setdefault(name, _Flexible(name))

    # Real submodule resolution must not kick in for anything below this fake
    # root - otherwise `from homeassistant.not_listed_above import X` would
    # silently succeed against a REAL (but only half-faked) package tree.
    sys.modules["homeassistant"].__path__ = []

    # Attribute-style parent imports: `config_flow.py` uniquely does
    # `from homeassistant import config_entries` (an ATTRIBUTE import). Python
    # runs `getattr(homeassistant, "config_entries")` FIRST and only falls
    # back to the sys.modules submodule when that raises. On a `_Flexible`
    # package `getattr` never raises - it hands back a throwaway
    # `type("config_entries", (), {})`, so `config_entries.ConfigFlow` then
    # fails with "type object 'config_entries' has no attribute 'ConfigFlow'".
    # Wiring each faked submodule as a real attribute on its parent package
    # makes the attribute lookup resolve to the faked MODULE. Direct submodule
    # imports (`from homeassistant.config_entries import ConfigEntry`) are
    # unaffected.
    for name in _FAKE_HA_MODULES:
        parent_name, _, leaf = name.rpartition(".")
        if parent_name:
            setattr(sys.modules[parent_name], leaf, sys.modules[name])

    # A real class object (not a `_Flexible` throwaway) so
    # `class X(ConfigFlow, domain=...)` works, and an identity `callback`.
    sys.modules["homeassistant.config_entries"].ConfigFlow = _FakeConfigFlow
    sys.modules["homeassistant.core"].callback = _identity_callback

    # Flow context sources are plain strings in real HA. `account.py` passes
    # `context={"source": SOURCE_IMPORT}` to the import flow; a `_Flexible`
    # throwaway class would work as a dict value but makes log/debug output and
    # assertions read wrong, so pin the real value.
    sys.modules["homeassistant.config_entries"].SOURCE_IMPORT = "import"

    # Real ``BaseException`` subclasses (not `_Flexible` throwaways): the
    # integration raises/catches ``ConfigEntryAuthFailed`` (reauth) and
    # ``ConfigEntryNotReady``. A plain `type(name, (), {})` cannot be used in an
    # ``except`` clause ("catching classes that do not inherit from
    # BaseException is not allowed") and breaks isinstance-based tests, so the
    # faked module must carry actual exception types - mirroring the reference
    # integration's conftest.
    _exceptions = sys.modules["homeassistant.exceptions"]
    _exceptions.ConfigEntryAuthFailed = type(
        "ConfigEntryAuthFailed", (Exception,), {}
    )
    _exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})

    # aiohttp: `config_flow.py` / `coordinator.py` call
    # `aiohttp.ClientTimeout(...)` and reference `aiohttp.ClientSession`. These
    # offline unit tests never perform real HTTP (sessions are always mocked),
    # so aiohttp is faked centrally here - this used to be hand-rolled at the
    # top of every test module. `ClientTimeout` needs a real constructor
    # accepting `total=`; any other attribute falls through to `_Flexible`.
    aiohttp_mod = sys.modules.get("aiohttp")
    if aiohttp_mod is None or not hasattr(aiohttp_mod, "ClientTimeout"):
        aiohttp_mod = _Flexible("aiohttp")
        sys.modules["aiohttp"] = aiohttp_mod
    aiohttp_mod.ClientTimeout = _ClientTimeout


install_ha_stubs()


# --- ConfigEntry test doubles (RC-3: real-instance fidelity) ----------------
# Home Assistant >= 2026.9 forbids direct assignment of the attributes it
# manages through ``ConfigEntries.async_update_entry`` (``version`` among them):
# ``ConfigEntry.__setattr__`` raises
# ``AttributeError: version cannot be changed directly, use async_update_entry
# instead``. The permissive ``MagicMock`` entries used throughout the unit tests
# silently accepted ``entry.version = 6`` and therefore could not catch that the
# v7 migration crashed on the real instance. These doubles close that gap.
_MANAGED_CONFIG_ENTRY_ATTRS = frozenset({
    "version",
    "unique_id",
    "data",
    "options",
    "title",
    "minor_version",
    "discovery_keys",
    "pref_disable_new_entities",
    "pref_disable_polling",
})


class StrictConfigEntry:
    """``ConfigEntry`` double mirroring the HA >=2026.9 attribute guard.

    Direct assignment of any :data:`_MANAGED_CONFIG_ENTRY_ATTRS` (notably
    ``version``) raises the same ``AttributeError`` as the real
    ``ConfigEntry.__setattr__``. Only :func:`make_applying_config_entries` /
    :func:`install_applying_config_entries` may write those values - via
    ``object.__setattr__``, exactly like HA's internal ``_async_setter``. A
    migration that still assigns ``entry.version = N`` therefore fails here,
    while it passes against a permissive ``MagicMock``.
    """

    def __init__(
        self,
        *,
        version: int = 1,
        unique_id: str | None = None,
        data: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        entry_id: str = "entry-abc",
        title: str = "Go Gauge",
    ) -> None:
        object.__setattr__(self, "entry_id", entry_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "unique_id", unique_id)
        object.__setattr__(self, "data", dict(data) if data is not None else {})
        object.__setattr__(
            self, "options", dict(options) if options is not None else {}
        )

    def __setattr__(self, key: str, value: Any) -> None:
        """Reject direct writes to managed attrs, mirroring real HA."""
        if key in _MANAGED_CONFIG_ENTRY_ATTRS:
            raise AttributeError(
                f"{key} cannot be changed directly, use async_update_entry instead"
            )
        object.__setattr__(self, key, value)


def _apply_async_update_entry(entry: Any, **changes: Any) -> bool:
    """Apply ``async_update_entry`` keyword changes like the real HA core."""
    for key, value in changes.items():
        # object.__setattr__ mirrors HA's internal _async_setter, which is the
        # only path allowed to bypass ConfigEntry.__setattr__.
        object.__setattr__(entry, key, value)
    return True


def make_applying_config_entries() -> MagicMock:
    """Return a ``hass.config_entries`` mock that really applies updates.

    ``async_update_entry`` stays a ``MagicMock`` (so ``.call_args`` /
    ``assert_called_with`` keep working), but its ``side_effect`` writes each
    keyword argument onto the passed entry via ``object.__setattr__`` - exactly
    what real HA does. Without this, ``assert entry.version == 7`` would fail
    after the fix because a plain MagicMock would swallow the update.
    """
    config_entries = MagicMock()
    config_entries.async_update_entry = MagicMock(
        side_effect=_apply_async_update_entry
    )
    return config_entries


def install_applying_config_entries(hass: Any) -> MagicMock:
    """Attach an applying ``config_entries`` mock to ``hass`` and return it."""
    hass.config_entries = make_applying_config_entries()
    return hass.config_entries


# --- Registry / device / issue / coordinator / timer doubles ----------------
# Plan: docs/PLAN-account-entry-2.0.0.md §8.2 (implementation order §10 step 3).
#
# The account-entry adoption (plan §2.4, §4.1-§4.7) *writes* the live HA
# registries: it rewrites unique_id / config_entry_id / device_id per entity,
# moves a device to another config entry, removes empty duplicate devices,
# disables collision losers, deletes legacy setting entities and raises repair
# issues. `_Flexible` cannot model any of that: `__getattr__` hands out a fresh
# throwaway class per access, so `registry.async_get` would be a different
# object on every read - call counts, state and idempotency assertions are
# impossible by construction. These are therefore real, stateful doubles
# (plan §8.1 level 2: "echte Doppel für Entity-/Device-/Issue-Registry").
#
# Inertness contract (plan §10: "Suite weiter grün, neue Fakes ungenutzt"):
# nothing here is used by the current tests and nothing replaces a symbol a
# current test installs or patches. Only *class* attributes are installed
# globally (so `from ... import RegistryEntry` resolves to a stable object);
# the callable registry surface stays on the Fake*Registry instances, and the
# module-level callables of `issue_registry` / `event` are thin dispatchers to
# the currently installed instance - see the rebinding note below.

# `_Flexible` replaced these modules in earlier-collected test files (collection
# order is alphabetical, see install_ha_stubs docstring), so every installer
# below is (re)callable and applies its attributes to whatever module object is
# currently registered instead of creating a second one.


def _current_module(name: str) -> types.ModuleType:
    """Return the faked module currently registered for `name` (create if absent)."""
    return sys.modules.setdefault(name, _Flexible(name))


# Cross-registry write journal (plan §8.3 "Adoptionsreihenfolge Entities-vor-
# Device"): per-registry call lists can prove "how often", but NOT "in which
# order relative to the other registry". The adoption must move all entities
# off a legacy device BEFORE moving/removing that device, so the ordering
# assertion needs one shared, chronological log. Tests call `reset_write_log()`
# first and then compare the kind sequence.
WRITE_LOG: list[tuple[str, str]] = []


def reset_write_log() -> None:
    """Clear the shared write journal (call at the start of an ordering test)."""
    WRITE_LOG.clear()


def _log_write(kind: str, key: str) -> None:
    """Append one write to the shared journal."""
    WRITE_LOG.append((kind, key))



class RegistryEntryDisabler:
    """``RegistryEntryDisabler`` double (``homeassistant.helpers.entity_registry``).

    Plain class attributes carrying the real string values (real HA uses a
    StrEnum): tests compare both `is` against the constant (collision loser
    must be disabled by the USER, plan §4.3) and `==` against the stored string.
    """

    USER = "user"
    INTEGRATION = "integration"
    CONFIG_ENTRY = "config_entry"


@dataclass(eq=False)
class RegistryEntry:
    """``RegistryEntry`` double carrying the fields the adoption sweep touches.

    ``entity_id``/``unique_id``/``domain``/``platform`` identify a candidate
    (plan §4.1); ``config_entry_id``/``device_id``/``disabled_by`` are its
    migration targets (plan §4.5 steps 1-3, §4.3 loser handling). ``id`` is
    included because the deterministic winner sort breaks ties on it (§4.2).
    ``entity_id`` may be ``None`` - real HA keeps disabled-by-config entries
    without an entity_id, and the sweep must skip them (plan §8.4).

    ``eq=False`` keeps the default identity hash so entries stay usable in
    sets/lists of candidates (real HA's RegistryEntry is a frozen dataclass and
    therefore hashable too). All attributes remain mutable on purpose: tests
    build the "before" registry state by assigning to them.
    """

    entity_id: str | None = None
    unique_id: str | None = None
    platform: str = ""
    domain: str = ""
    config_entry_id: str | None = None
    device_id: str | None = None
    disabled_by: str | None = None
    original_name: str | None = None
    id: str | None = None

    @property
    def disabled(self) -> bool:
        """Mirror ``RegistryEntry.disabled`` (any ``disabled_by`` value means off)."""
        return self.disabled_by is not None


class FakeEntityRegistry:
    """Stateful ``EntityRegistry`` double for the adoption/idempotency tests.

    Mirrors the async surface the integration actually uses (plan §4.5 table)
    and records every write, so "second pass writes nothing" is asserted on
    call counts instead of a state diff (plan §8.3). Writes additionally land
    in the shared :data:`WRITE_LOG` for the entity-before-device ordering
    assertion.

    `raise_on_update` forces the §4.6 failure path: a write that raises must
    end up in `report.errors` and never abort the account setup. A rejected
    write is NOT recorded in `update_calls`, so counters stay "successful
    writes only" - otherwise a ValueError simulation would look like a write.
    """

    def __init__(self) -> None:
        # Keyed by entity_id, exactly like the real registry store. `None` is a
        # valid key: disabled-by-config entries have no entity_id.
        self.entities: dict[str | None, RegistryEntry] = {}
        self.update_calls: list[tuple[str, dict[str, Any]]] = []
        self.remove_calls: list[str] = []
        self.migrate_calls: list[str] = []
        self.migrate_changes: list[tuple[str, dict[str, Any]]] = []
        self.raise_on_update: BaseException | type[BaseException] | None = None

    # -- helpers (not part of the HA surface, test convenience) --------------

    def add(self, entry: RegistryEntry) -> RegistryEntry:
        """Register `entry` under its current entity_id and return it."""
        self.entities[entry.entity_id] = entry
        return entry

    def _find_by_unique_id(
        self,
        domain: str,
        platform: str,
        unique_id: str | None,
        *,
        ignore_key: str | None = None,
    ) -> RegistryEntry | None:
        """Return the entry owning (domain, platform, unique_id) - self excluded."""
        for key, entry in self.entities.items():
            if key == ignore_key:
                continue
            if entry.unique_id != unique_id:
                continue
            if entry.domain == domain and entry.platform == platform:
                return entry
        return None

    # -- HA surface ---------------------------------------------------------

    def async_get(self, entity_id: str) -> RegistryEntry | None:
        """Return the entry registered under `entity_id` (``None`` when absent)."""
        return self.entities.get(entity_id)

    def async_get_entity_id(
        self, domain: str, platform: str, unique_id: str
    ) -> str | None:
        """Resolve (domain, platform, unique_id) to entity_id.

        This is the pre-check of plan §4.6: a canonical unique_id that already
        resolves means the migration must take the loser path instead of
        calling ``async_update_entity(new_unique_id=...)``.
        """
        entry = self._find_by_unique_id(domain, platform, unique_id)
        return entry.entity_id if entry is not None else None

    def async_update_entity(self, entity_id: str, **changes: Any) -> RegistryEntry:
        """Apply registry changes, mirroring ``EntityRegistry.async_update_entity``.

        ``new_unique_id`` writes ``unique_id`` and raises ``ValueError`` when
        another entry of the same domain+platform already owns it - real HA
        scopes unique_id uniqueness per platform and domain. The entry's own
        key never blocks itself (self-rename is legal). ``new_entity_id``
        re-keys the store like the real registry does. An unknown `entity_id`
        raises ``KeyError`` (the real registry does too).
        """
        if self.raise_on_update is not None:
            raise self.raise_on_update
        entry = self.entities[entity_id]
        remaining = dict(changes)
        new_unique_id = remaining.pop("new_unique_id", ...)
        if new_unique_id is not ... and new_unique_id is not None:
            duplicate = self._find_by_unique_id(
                entry.domain, entry.platform, new_unique_id, ignore_key=entity_id
            )
            if duplicate is not None:
                raise ValueError(
                    f"Entity with unique_id {new_unique_id} "
                    f"({duplicate.entity_id}) already exists"
                )
        self.update_calls.append((entity_id, dict(changes)))
        _log_write("entity_update", entity_id)
        if new_unique_id is not ...:
            entry.unique_id = new_unique_id
        new_entity_id = remaining.pop("new_entity_id", ...)
        if new_entity_id is not ... and new_entity_id is not None:
            del self.entities[entity_id]
            entry.entity_id = new_entity_id
            self.entities[new_entity_id] = entry
        for key, value in remaining.items():
            setattr(entry, key, value)
        return entry

    def async_remove(self, entity_id: str) -> None:
        """Drop the entry from the store (legacy-setting removal, plan §4.7)."""
        self.remove_calls.append(entity_id)
        _log_write("entity_remove", entity_id)
        self.entities.pop(entity_id, None)

    def async_migrate_entries(
        self, config_entry_id: str, callback: Any
    ) -> dict[str, RegistryEntry]:
        """Migrate entries of one config entry, mirroring HA's bulk helper.

        The callback gets every entry whose ``config_entry_id`` matches and
        may return ``None`` (leave it) or a change dict applied through
        ``async_update_entity`` (``new_entity_id`` / ``new_unique_id`` ...).
        Returns the changed entries keyed by their (post-migration) entity_id,
        like the real registry; `migrate_changes` additionally keeps the
        requested change dicts in call order for assertions.
        """
        self.migrate_calls.append(config_entry_id)
        changed: dict[str, RegistryEntry] = {}
        for entry in list(self.entities.values()):
            if entry.config_entry_id != config_entry_id or entry.entity_id is None:
                continue
            entity_id = entry.entity_id
            changes = callback(entry)
            if not changes:
                continue
            self.migrate_changes.append((entity_id, dict(changes)))
            self.async_update_entity(entity_id, **changes)
            changed[entry.entity_id] = entry
        return changed


@dataclass(eq=False)
class DeviceEntry:
    """``DeviceEntry`` double for the device-move / duplicate-merge paths.

    ``config_entries`` is the ownership set the device migrate targets (§4.5
    step 5 replaces it wholesale), ``identifiers`` is what ``async_get_device``
    matches, and ``via_device_id`` marks child devices - a duplicate device
    with children or entities must NOT be removed (plan §8.3). ``connections``
    exists because ``async_get_device(connections=...)`` needs something to
    match against, mirroring the real entry. ``eq=False`` keeps identity
    hashing, like the frozen real dataclass.
    """

    id: str = ""
    name: str | None = None
    identifiers: set[tuple[str, str]] = field(default_factory=set)
    connections: set[tuple[str, str]] = field(default_factory=set)
    config_entries: set[str] = field(default_factory=set)
    via_device_id: str | None = None
    disabled_by: str | None = None


class FakeDeviceRegistry:
    """Stateful ``DeviceRegistry`` double (device move, merge, emptiness check).

    Writes are recorded in `update_calls` / `remove_calls` and in the shared
    :data:`WRITE_LOG` (cross-registry order). `raise_on_update` forces the
    ``TypeError`` of an HA version that does not support
    ``new_config_entry_id`` - the guard of plan §11.1 R1 must turn that into a
    repair issue while setup still returns True.

    The entity lookup ("is this device empty?") is delegated to an injectable
    entity registry, because the real one owns that relation: an empty
    duplicate device may be removed, a device with entities may not.
    """

    def __init__(self, entity_registry: FakeEntityRegistry | None = None) -> None:
        self.devices: dict[str, DeviceEntry] = {}
        self.entity_registry = entity_registry
        self.update_calls: list[tuple[str, dict[str, Any]]] = []
        self.remove_calls: list[str] = []
        self.raise_on_update: BaseException | type[BaseException] | None = None

    # -- helpers (not part of the HA surface, test convenience) --------------

    def add(self, device: DeviceEntry) -> DeviceEntry:
        """Register `device` under its id and return it."""
        self.devices[device.id] = device
        return device

    # -- HA surface ---------------------------------------------------------

    def async_get(self, device_id: str) -> DeviceEntry | None:
        """Return the device with `device_id` (``None`` when absent)."""
        return self.devices.get(device_id)

    def async_get_device(
        self,
        identifiers: set[tuple[str, str]] | None = None,
        connections: set[tuple[str, str]] | None = None,
    ) -> DeviceEntry | None:
        """Find a device by identifier/connection subset, like the real lookup.

        Kept positional-or-keyword on purpose: the integration calls it with
        keyword arguments, but real HA accepts both, and a stricter fake would
        hide a mismatching call site.
        """
        if identifiers:
            wanted = set(identifiers)
            for device in self.devices.values():
                if wanted <= device.identifiers:
                    return device
        if connections:
            wanted = set(connections)
            for device in self.devices.values():
                if wanted <= device.connections:
                    return device
        return None

    def async_update_device(
        self,
        device_id: str,
        *,
        new_config_entry_id: str | None = None,
        add_config_entry_id: str | None = None,
        remove_config_entry_id: str | None = None,
        **kwargs: Any,
    ) -> DeviceEntry | None:
        """Apply device changes, mirroring ``DeviceRegistry.async_update_device``.

        ``new_config_entry_id`` REPLACES the whole ownership set (the move of
        plan §4.5 step 5) - never merge it with the old set here, otherwise an
        old workspace entry would keep "owning" the device after adoption.
        ``add_``/``remove_config_entry_id`` add/discard one entry; dropping the
        last owner deletes the orphaned device exactly like real HA. An unknown
        `device_id` returns ``None`` (the real registry does, too).
        """
        if self.raise_on_update is not None:
            raise self.raise_on_update
        device = self.devices.get(device_id)
        if device is None:
            return None
        recorded: dict[str, Any] = dict(kwargs)
        for key, value in (
            ("new_config_entry_id", new_config_entry_id),
            ("add_config_entry_id", add_config_entry_id),
            ("remove_config_entry_id", remove_config_entry_id),
        ):
            if value is not None:
                recorded[key] = value
        self.update_calls.append((device_id, recorded))
        _log_write("device_update", device_id)
        if new_config_entry_id is not None:
            device.config_entries = {new_config_entry_id}
        if add_config_entry_id is not None:
            device.config_entries.add(add_config_entry_id)
        if remove_config_entry_id is not None:
            device.config_entries.discard(remove_config_entry_id)
            if not device.config_entries:
                self.async_remove_device(device_id)
                return None
        for key, value in kwargs.items():
            setattr(device, key, value)
        return device

    def async_remove_device(self, device_id: str) -> None:
        """Remove the device (empty duplicate merge, plan §4.5 step 4)."""
        self.remove_calls.append(device_id)
        _log_write("device_remove", device_id)
        self.devices.pop(device_id, None)

    def async_entries_for_device(
        self, device_id: str, include_disabled_entities: bool = False
    ) -> list[RegistryEntry]:
        """Return the entities still pointing at `device_id`.

        Disabled entities are hidden unless requested - a device whose only
        remaining entity is the disabled collision loser still counts as
        non-empty for the real HA duplicate check, so tests must be able to
        flip that flag.
        """
        if self.entity_registry is None:
            return []
        return [
            entry
            for entry in self.entity_registry.entities.values()
            if entry.device_id == device_id
            and (include_disabled_entities or not entry.disabled)
        ]


class IssueSeverity:
    """``IssueSeverity`` double (``homeassistant.helpers.issue_registry``).

    Plain class attributes with the real string values (real HA uses a
    StrEnum); the plan passes ``IssueSeverity.WARNING`` for the collision
    issues (§4.4), so both identity and value comparisons work.
    """

    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class FakeIssueRegistry:
    """Stateful repair-issue double (``homeassistant.helpers.issue_registry``).

    ``async_create_issue`` OVERWRITES an existing issue_id instead of adding a
    second one - that is the idempotency the plan relies on (stable issue ids,
    §4.4). ``deleted`` records every delete call, including no-ops for ids that
    never existed, so issue cleanup in ``async_remove_account_entry`` can be
    asserted without pre-seeding the registry.

    Wire it into the faked module with :func:`install_issue_registry_stub`.
    """

    def __init__(self) -> None:
        self.issues: dict[str, dict[str, Any]] = {}
        self.create_calls: list[str] = []
        self.deleted: list[str] = []

    def async_create_issue(
        self, hass: Any, domain: str, issue_id: str, **kwargs: Any
    ) -> None:
        """Create or update the repair issue `issue_id` (keyed by issue_id)."""
        self.create_calls.append(issue_id)
        self.issues[issue_id] = {"domain": domain, **kwargs}

    def async_delete_issue(self, hass: Any, domain: str, issue_id: str) -> None:
        """Delete the repair issue `issue_id`; a missing id is a recorded no-op."""
        self.deleted.append(issue_id)
        self.issues.pop(issue_id, None)


# The module-level `issue_registry`/`event` callables are single, never-replaced
# dispatcher functions. Why: `account.py` imports them at module import time
# (`from homeassistant.helpers.issue_registry import async_create_issue`), so a
# per-test re-assignment of the module attribute would NOT reach the already
# bound name. The dispatchers forward to whichever instance/fixture a test
# installed last (`install_issue_registry_stub` / `install_timer_stub`), which
# keeps per-test isolation without re-importing the integration module.
_ACTIVE_ISSUE_REGISTRY: FakeIssueRegistry | None = None
_ACTIVE_TIMER_MANAGER: FakeTimerManager | None = None


def _dispatch_create_issue(hass: Any, domain: str, issue_id: str, **kwargs: Any) -> None:
    """Forward to the active FakeIssueRegistry (mirrors the module function)."""
    assert _ACTIVE_ISSUE_REGISTRY is not None, "install_issue_registry_stub() first"
    _ACTIVE_ISSUE_REGISTRY.async_create_issue(hass, domain, issue_id, **kwargs)


def _dispatch_delete_issue(hass: Any, domain: str, issue_id: str) -> None:
    """Forward to the active FakeIssueRegistry (mirrors the module function)."""
    assert _ACTIVE_ISSUE_REGISTRY is not None, "install_issue_registry_stub() first"
    _ACTIVE_ISSUE_REGISTRY.async_delete_issue(hass, domain, issue_id)


def _dispatch_track_time_interval(
    hass: Any, action: Any, interval: timedelta
) -> Any:
    """Forward to the active FakeTimerManager (mirrors the module function)."""
    assert _ACTIVE_TIMER_MANAGER is not None, "install_timer_stub() first"
    return _ACTIVE_TIMER_MANAGER.async_track_time_interval(hass, action, interval)


def install_issue_registry_stub() -> FakeIssueRegistry:
    """Install a FRESH FakeIssueRegistry and return it (per-test isolation).

    Issue ids are global per domain, so a shared instance would leak issues
    between tests; every test that asserts repair issues calls this first.
    """
    global _ACTIVE_ISSUE_REGISTRY
    registry = FakeIssueRegistry()
    _ACTIVE_ISSUE_REGISTRY = registry
    module = _current_module("homeassistant.helpers.issue_registry")
    module.IssueSeverity = IssueSeverity
    module.async_create_issue = _dispatch_create_issue
    module.async_delete_issue = _dispatch_delete_issue
    return registry


class FakeTimerManager:
    """``async_track_time_interval`` double for the fetch clock (plan §6.2).

    The account coordinator has TWO clocks: the coordinator's
    ``update_interval`` (publish tick, no HTTP) and a separate interval timer
    for the actual fetch. `calls` records the scheduled interval of every arm,
    so "rescheduled with the new minutes" is assertable, and the returned
    unsubscribe callback counts `unsub` invocations for "timer cancelled".
    """

    def __init__(self) -> None:
        self.calls: list[timedelta] = []
        self.actions: list[Any] = []
        self.unsub_calls = 0

    def async_track_time_interval(
        self, hass: Any, action: Any, interval: timedelta
    ) -> Any:
        """Record one scheduling and return a counting unsubscribe callback."""
        self.calls.append(interval)
        self.actions.append(action)

        def _unsub() -> None:
            self.unsub_calls += 1

        return _unsub


def install_timer_stub() -> FakeTimerManager:
    """Install a FRESH FakeTimerManager and return it.

    Safe to call at any time: the module keeps the stable
    :func:`_dispatch_track_time_interval` function, so modules that imported
    the name before this call still route into the newest manager.
    """
    global _ACTIVE_TIMER_MANAGER
    manager = FakeTimerManager()
    _ACTIVE_TIMER_MANAGER = manager
    module = _current_module("homeassistant.helpers.event")
    module.async_track_time_interval = _dispatch_track_time_interval
    module.FakeTimerManager = FakeTimerManager
    return manager


class FakeDataUpdateCoordinator:
    """``DataUpdateCoordinator`` double with observable scheduling/lifecycle.

    Mirrors what the account coordinator depends on (plan §2.4, §6.2, §8.3):
    a WRITABLE ``update_interval`` (HA's setter stores the value but does not
    re-arm an already scheduled refresh - the integration must call
    ``_schedule_refresh()`` itself, the RC-2 regression), ``async_refresh`` /
    ``async_update_listeners`` / ``async_set_updated_data`` with call counters,
    and an ``async_config_entry_first_refresh`` that must never be used by the
    account coordinator. ``__class_getitem__`` keeps
    ``DataUpdateCoordinator[dict[str, Any]]`` subclassable.

    ``async_refresh`` deliberately does not re-raise: a background publish tick
    must not abort a test on an exception the integration is supposed to record
    (``last_update_success`` / ``last_exception``).
    """

    def __class_getitem__(cls, item: Any) -> type[FakeDataUpdateCoordinator]:
        """Keep the generic subscript syntax working (``Coordinator[dict]``)."""
        return cls

    def __init__(
        self,
        hass: Any,
        logger: Any = None,
        *,
        name: str | None = None,
        update_interval: timedelta | None = None,
        **kwargs: Any,
    ) -> None:
        """Accept any extra HA kwargs; only interval/name are modelled."""
        self.hass = hass
        self.logger = logger
        self.name = name
        self.init_kwargs = kwargs
        self.data: Any = None
        self.last_update_success = True
        self.last_exception: BaseException | None = None
        self._update_interval: timedelta | None = None
        self.interval_history: list[timedelta | None] = []
        self._schedule_calls = 0
        self.refresh_calls = 0
        self.update_listener_calls = 0
        self.set_updated_data_calls = 0
        self.first_refresh_calls = 0
        self.update_interval = update_interval

    @property
    def update_interval(self) -> timedelta | None:
        """Current interval; assignments are additionally kept as history."""
        return self._update_interval

    @update_interval.setter
    def update_interval(self, value: timedelta | None) -> None:
        """Store the interval (HA's setter - rescheduling is the caller's job)."""
        self._update_interval = value
        self.interval_history.append(value)

    @property
    def schedule_refresh_calls(self) -> int:
        """Number of ``_schedule_refresh`` calls (one per timer re-arm)."""
        return self._schedule_calls

    def _schedule_refresh(self) -> None:
        """Count a re-arm request (the real method re-arms HA's timer)."""
        self._schedule_calls += 1

    async def async_refresh(self) -> Any:
        """Run ``_async_update_data`` once and record success/failure."""
        self.refresh_calls += 1
        update = getattr(self, "_async_update_data", None)
        if update is None:
            return self.data
        try:
            self.data = await update()
        except Exception as err:  # noqa: BLE001 - tests assert, they do not log
            self.last_exception = err
            self.last_update_success = False
            return None
        self.last_update_success = True
        return self.data

    def async_update_listeners(self) -> None:
        """Count a listener notification (entities re-read the coordinator)."""
        self.update_listener_calls += 1

    def async_set_updated_data(self, data: Any) -> None:
        """Publish `data` directly and notify listeners, like the real method."""
        self.set_updated_data_calls += 1
        self.data = data
        self.last_update_success = True
        self.async_update_listeners()

    async def async_config_entry_first_refresh(self) -> None:
        """Count the call; the account coordinator must never reach it."""
        self.first_refresh_calls += 1


def install_coordinator_stub() -> type[FakeDataUpdateCoordinator]:
    """Install the coordinator double (and a real ``UpdateFailed``) and return it.

    ``UpdateFailed`` is a real ``Exception`` subclass here: the fake
    ``_Flexible`` module would hand out a throwaway class, and ``raise`` on a
    non-exception class fails with ``TypeError`` instead of exercising the
    integration's error path (coordinator.py raises it).
    """
    module = _current_module("homeassistant.helpers.update_coordinator")
    module.DataUpdateCoordinator = FakeDataUpdateCoordinator
    module.UpdateFailed = type("UpdateFailed", (Exception,), {})
    return FakeDataUpdateCoordinator


def install_registry_stubs() -> None:
    """Install the registry classes on their faked modules (re-callable).

    Only classes are installed. The callable surface (``async_get`` etc.) stays
    on the Fake*Registry instances so no global function is silently shadowed
    for tests that patch those module attributes themselves. Use
    :func:`install_entity_registry_stub` / :func:`install_device_registry_stub`
    to route the module-level lookups into a specific fake.
    """
    entity_module = _current_module("homeassistant.helpers.entity_registry")
    entity_module.RegistryEntry = RegistryEntry
    entity_module.RegistryEntryDisabler = RegistryEntryDisabler
    device_module = _current_module("homeassistant.helpers.device_registry")
    device_module.DeviceEntry = DeviceEntry


def install_entity_registry_stub(
    registry: FakeEntityRegistry | None = None,
) -> FakeEntityRegistry:
    """Route ``entity_registry.async_get(hass)`` into `registry` (fresh if None).

    The integration does ``er.async_get(hass)`` at runtime, so wiring the
    module-level lookup is enough - no per-entry patching needed.
    """
    install_registry_stubs()
    registry = registry if registry is not None else FakeEntityRegistry()
    _current_module("homeassistant.helpers.entity_registry").async_get = (
        lambda hass, registry=registry: registry
    )
    return registry


def install_device_registry_stub(
    registry: FakeDeviceRegistry | None = None,
) -> FakeDeviceRegistry:
    """Route ``device_registry.async_get(hass)`` into `registry` (fresh if None)."""
    install_registry_stubs()
    registry = registry if registry is not None else FakeDeviceRegistry()
    _current_module("homeassistant.helpers.device_registry").async_get = (
        lambda hass, registry=registry: registry
    )
    return registry


class _ConfigEntryState:
    """``ConfigEntryState`` double (``homeassistant.config_entries``).

    Carries the real string values, including ``MIGRATION_ERROR`` and
    ``FAILED_UNLOAD``: the plan explicitly asserts that a registry error must
    not turn into ``MIGRATION_ERROR`` (plan §11.1 R6), which needs the member
    to exist. Plain attributes (not a StrEnum) keep `==` against the stored
    string working.
    """

    NOT_LOADED = "not_loaded"
    SETUP_ERROR = "setup_error"
    MIGRATION_ERROR = "migration_error"
    SETUP_RETRY = "setup_retry"
    FAILED_UNLOAD = "failed_unload"
    LOADED = "loaded"
    LOADING = "loading"
    UNLOADING = "unloading"


def install_config_entry_state_stub() -> type:
    """(Re)install the ``ConfigEntryState`` double on the current fake module.

    ``account.py`` binds ``ConfigEntryState`` once at import time and compares
    an entry's ``state`` against ``ConfigEntryState.LOADED``. A bare
    ``_Flexible`` module would hand out a *fresh* throwaway class on every
    attribute access, so that comparison could never be true. Test modules that
    import ``go_gauge.account`` (directly or through ``__init__``) therefore call
    this helper immediately before the import - another earlier-collected
    module may have replaced the module object in ``sys.modules`` meanwhile.
    Re-callable and idempotent, like the other installers above.

    Returns the (stable) class so tests can use ``...().LOADED`` instead of
    reaching into ``sys.modules``.
    """
    module = _current_module("homeassistant.config_entries")
    module.ConfigEntryState = _ConfigEntryState
    return _ConfigEntryState


# Global installation of the class-only stubs (plan §10 step 3: new fakes stay
# unused for now). Called once here, re-callable by test modules that replaced a
# `homeassistant.helpers.*` module with their own `_Flexible` instance.
install_registry_stubs()
install_coordinator_stub()
install_timer_stub()
install_issue_registry_stub()
install_config_entry_state_stub()
