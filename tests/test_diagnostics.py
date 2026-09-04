#!/usr/bin/env python3
"""Regression test for the Security-Audit findings in diagnostics.py
(docs/AUDIT-2026-09-04.md):

1. The raw token prefix (first 8 chars of the bearer token) must never
   appear in the diagnostics export - only a non-reversible SHA-256
   fingerprint may be used for correlation/debugging.
2. `workspace_name` is declared in REDACT_KEYS but was emitted raw on the
   top-level dict (async_redact_data was only ever applied to `options`
   and `ws_states`) - it must now be redacted consistently with that
   declaration.

Base homeassistant.* module fakes (incl. the _Flexible ModuleType helper)
are installed centrally in tests/conftest.py, which pytest always imports
before collecting this file - see conftest.py docstring for why.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"

REDACTED = "**REDACTED**"


def _real_async_redact_data(data, to_redact):
    """Minimal stand-in for homeassistant.components.diagnostics.async_redact_data.

    The centralized _Flexible fake in conftest.py turns any attribute access
    (incl. `async_redact_data`) into a fresh, empty class - calling that as a
    function would blow up. diagnostics.py actually calls it as a function,
    so this test installs a small but behaviourally faithful replacement:
    recursively replace any value whose key is in `to_redact` with a fixed
    placeholder, same as the real HA helper does for dicts/lists.
    """
    if isinstance(data, dict):
        redacted = dict(data)
        for key, value in redacted.items():
            if key in to_redact:
                redacted[key] = REDACTED
            elif isinstance(value, (dict, list)):
                redacted[key] = _real_async_redact_data(value, to_redact)
        return redacted
    if isinstance(data, list):
        return [_real_async_redact_data(item, to_redact) for item in data]
    return data


sys.modules["homeassistant.components.diagnostics"].async_redact_data = (
    _real_async_redact_data
)


class _FakeIntegration:
    version = "1.2.3"


async def _fake_async_get_integration(hass, domain):
    return _FakeIntegration()


sys.modules["homeassistant.loader"].async_get_integration = _fake_async_get_integration


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
diagnostics = _load("go_gauge.diagnostics", str(BASE / "diagnostics.py"))


class _FakeCoordinator:
    last_update_success = True
    auto_usage = True
    auto_models = True
    usage_minutes = 10
    models_minutes = 60
    is_catalog_owner = True

    def __init__(self, data):
        self.data = data


class _FakeEntry:
    """Mimics the ConfigEntry attributes diagnostics.py reads."""

    entry_id = "test_entry"
    version = 3

    def __init__(self, token, workspace_name):
        self.data = {"token": token, "workspace_name": workspace_name}
        self.options = {}
        self.state = "loaded"


class _FakeHass:
    def __init__(self, coordinator, entry_id):
        self.data = {const.DOMAIN: {entry_id: coordinator}}


TOKEN = "sk-live-super-secret-token-value-0123456789"
WORKSPACE_NAME = "Daniel's Private Workspace"


def _run_diagnostics():
    data = {"fetched_at": "t", "workspaces": [], "models_block": None}
    coordinator = _FakeCoordinator(data)
    entry = _FakeEntry(token=TOKEN, workspace_name=WORKSPACE_NAME)
    hass = _FakeHass(coordinator, entry.entry_id)
    return asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(hass, entry)
    )


def test_token_fingerprint_is_a_non_reversible_hash_not_a_raw_prefix():
    result = _run_diagnostics()

    expected_fp = hashlib.sha256(TOKEN.encode()).hexdigest()[:8]
    assert result["token_fingerprint"] == expected_fp

    # The raw token (and its old-style first-8-chars prefix) must not leak
    # into the export anywhere, e.g. via json.dumps(result).
    dumped = repr(result)
    assert TOKEN[:8] not in dumped
    assert TOKEN not in dumped


def test_workspace_name_is_redacted_consistently_with_redact_keys():
    result = _run_diagnostics()

    assert "workspace_name" in diagnostics.REDACT_KEYS
    assert result["workspace_name"] == REDACTED
    assert WORKSPACE_NAME not in repr(result)


if __name__ == "__main__":
    test_token_fingerprint_is_a_non_reversible_hash_not_a_raw_prefix()
    test_workspace_name_is_redacted_consistently_with_redact_keys()
    print("OK")
