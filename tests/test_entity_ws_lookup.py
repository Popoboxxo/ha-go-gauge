#!/usr/bin/env python3
"""Regression test: GoGaugeEntityBase._ws must not crash on a malformed
workspace entry that lacks the "key" field.

Root cause of the audited bug (docs/AUDIT-2026-09-04.md, "M5"): `_ws()` used
an unguarded `ws["key"]` dict access. The coordinator currently guarantees a
"key" value in every "workspaces" entry, but a single degraded entry
(e.g. a stale cached state after an error, or a future API response
variant) would raise KeyError in every property evaluation that calls
`_ws()`, instead of simply not matching - like any other non-hit.
"""
import importlib.util
import sys
from pathlib import Path

# Base homeassistant.* module fakes are installed centrally in
# tests/conftest.py, which pytest always imports before collecting this
# file - see conftest.py docstring for why.

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


def _class_getitem(cls, item):
    return cls


_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)})


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


_uc.CoordinatorEntity = _CoordinatorEntity


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
entity = _load("go_gauge.entity", str(BASE / "entity.py"))


class _FakeCoordinator:
    def __init__(self, data):
        self.data = data


class _FakeEntry:
    entry_id = "test_entry"


def test_ws_skips_entry_without_key_instead_of_raising():
    """A workspace entry missing "key" must not blow up the lookup."""
    data = {
        "workspaces": [
            {"status": "ok"},  # malformed: no "key" field
            {"key": "ws1", "status": "ok"},
        ],
    }
    coordinator = _FakeCoordinator(data)
    base = entity.GoGaugeEntityBase(coordinator, _FakeEntry())

    assert base._ws("ws1") == {"key": "ws1", "status": "ok"}
    assert base._ws("does-not-exist") is None


if __name__ == "__main__":
    test_ws_skips_entry_without_key_instead_of_raising()
    print("OK")
