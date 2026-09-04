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

for _name in _FAKE_HA_MODULES:
    sys.modules[_name] = _Flexible(_name)

# Real submodule resolution must not kick in for anything below this fake
# root - otherwise `from homeassistant.somewhere_not_listed_above import X`
# would silently succeed against a REAL (but only half-faked) package tree.
sys.modules["homeassistant"].__path__ = []
