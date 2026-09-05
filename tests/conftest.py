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
# util.dt.
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
