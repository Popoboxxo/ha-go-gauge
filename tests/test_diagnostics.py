#!/usr/bin/env python3
"""Diagnostics export split by entry kind (2.0.0, plan §2.13/§8.3/§8.5).

Two entry kinds, two exports:

* **workspace** - usage polling with the audited token handling (only a
  non-reversible SHA-256 fingerprint, ``workspace_name`` redacted
  consistently with ``REDACT_KEYS``) plus the new shared ``account`` block
  (``loaded``/``entry_id``/``auto_fetch``/``base_interval_minutes``/
  ``reachability_reason``). The pre-2.0.0 owner/model keys
  (``models_block_present``, ``is_catalog_owner``, ``auto_models``,
  ``models_minutes``, ``last_models_fetch``) are GONE - the workspace
  snapshot no longer has them (plan §8.3 "models_block-Konsumenten").
* **account** - catalog/reachability runtime: settings, backoff state,
  ``last_models_*``, the resolved ``reachability`` matrix and the last
  ``AdoptionReport``. It has no token, so neither ``token`` nor
  ``token_fingerprint`` may appear, and it no longer emits the redundant
  ``models_block_present`` flag (``last_models_ok``/``last_models_outcome``
  already answer that - plan §8.3/M3).

Security regressions from docs/AUDIT-2026-09-04.md stay pinned: the raw token
prefix must never leak and ``workspace_name`` must be redacted.

The shared HA fakes live in ``tests/conftest.py``; this file loads the
integration modules in dependency order like the other account test modules -
diagnostics resolves the reachability through ``account.account_reachability``
(the single TTL/evidence site, no second implementation here).
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

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


class _FakeIntegration:
    version = "1.2.3"


async def _fake_async_get_integration(hass, domain):
    return _FakeIntegration()


# Re-assert the shared fakes, then patch the two HA callables diagnostics binds
# at import time - BEFORE the module import below.
conftest.install_ha_stubs()
_CONFIG_ENTRY_STATE = conftest.install_config_entry_state_stub()
conftest.install_coordinator_stub()
conftest.install_timer_stub()
conftest.install_issue_registry_stub()
conftest.install_registry_stubs()

sys.modules["homeassistant.components.diagnostics"].async_redact_data = (
    _real_async_redact_data
)
sys.modules["homeassistant.loader"].async_get_integration = (
    _fake_async_get_integration
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
account_logic = _load("go_gauge.account_logic", str(BASE / "account_logic.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load("go_gauge.entity", str(BASE / "entity.py"))
account = _load("go_gauge.account", str(BASE / "account.py"))
diagnostics = _load("go_gauge.diagnostics", str(BASE / "diagnostics.py"))

TOKEN = "sk-live-super-secret-token-value-0123456789"
WORKSPACE_NAME = "Daniel's Private Workspace"
WORKSPACE_ENTRY_ID = "ws-entry"
ACCOUNT_ENTRY_ID = "account-entry"


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _FakeEntry:
    """Mimics the ConfigEntry attributes diagnostics.py reads."""

    version = 8

    def __init__(self, *, entry_id, data, options=None):
        self.entry_id = entry_id
        self.data = dict(data)
        self.options = dict(options or {})
        self.state = _CONFIG_ENTRY_STATE.LOADED


def _workspace_entry() -> _FakeEntry:
    """Workspace entry: token + workspace_name, NO entry_kind marker."""
    return _FakeEntry(
        entry_id=WORKSPACE_ENTRY_ID,
        data={"token": TOKEN, "workspace_name": WORKSPACE_NAME},
    )


def _account_entry(options=None) -> _FakeEntry:
    """Account entry: kind marker only - it never carries a token."""
    return _FakeEntry(
        entry_id=ACCOUNT_ENTRY_ID,
        data={const.CONF_ENTRY_KIND: const.ENTRY_KIND_ACCOUNT},
        options=options,
    )


class _FakeWorkspaceCoordinator:
    """Usage coordinator surface read by the workspace export."""

    last_update_success = True
    auto_usage = True
    usage_minutes = 10
    ws_name = "Work"

    def __init__(self, data):
        self.data = data


def _workspace_data(**overrides):
    """Workspace snapshot WITHOUT any models key (plan §2.7/§8.3)."""
    data = {
        "fetched_at": "t",
        "last_usage_fetch": None,
        "last_usage_ok": None,
        "last_usage_attempt": None,
        "last_usage_outcome": None,
        "auto_usage": True,
        "usage_refresh_minutes": 10,
        "workspaces": [],
    }
    data.update(overrides)
    return data


def _live(name: str):
    """Resolve a module through ``sys.modules`` at CALL time.

    Collection order is alphabetical and several test files reload
    ``go_gauge.account``/``go_gauge.coordinator``; the integration resolves its
    lazy imports through ``sys.modules`` too, so the doubles must be built from
    the module currently registered - otherwise the ``isinstance`` checks inside
    ``collect_evidence`` would reject them and the matrix would see no evidence.
    """
    return sys.modules[name]


def _account_coordinator(*, models_block=None, data=None, **attrs):
    """Bare ``AccountCoordinator`` (no HA init) carrying the export attributes."""
    account_module = _live("go_gauge.account")
    coordinator = account_module.AccountCoordinator.__new__(
        account_module.AccountCoordinator
    )
    coordinator.entry = attrs.pop("entry", _account_entry())
    coordinator.auto_fetch = attrs.pop("auto_fetch", True)
    coordinator.base_interval_minutes = attrs.pop("base_interval_minutes", 60)
    coordinator.last_models_attempt = attrs.pop("last_models_attempt", None)
    coordinator.last_models_ok = attrs.pop("last_models_ok", None)
    coordinator.last_models_outcome = attrs.pop("last_models_outcome", None)
    coordinator.last_error = attrs.pop("last_error", None)
    coordinator._failures = attrs.pop("failures", 0)
    coordinator._models_block = models_block
    coordinator.data = data if data is not None else _account_data()
    for key, value in attrs.items():
        setattr(coordinator, key, value)
    return coordinator


def _account_data(**overrides):
    """Account snapshot as produced by ``AccountCoordinator._snapshot``."""
    data = {
        "fetched_at": "t",
        "models_block": None,
        "last_models_attempt": None,
        "last_models_ok": None,
        "last_models_outcome": None,
        "last_error": None,
        "auto_fetch": True,
        "base_interval_minutes": 60,
        "fetch_interval_minutes": 60,
        "backoff_active": False,
        "backoff_failures": 0,
    }
    data.update(overrides)
    return data


class _FakeHass:
    """``hass`` double: a real ``data`` dict, keyed exactly like production."""

    def __init__(self, domain_data):
        self.data = {const.DOMAIN: dict(domain_data)}


def _run(hass, entry):
    return asyncio.run(
        diagnostics.async_get_config_entry_diagnostics(hass, entry)
    )


# ---------------------------------------------------------------------------
# Workspace branch
# ---------------------------------------------------------------------------

def test_workspace_diag_has_account_block():
    """[PLAN2-M3] Shared account block replaces the owner/models keys."""
    coordinator = _FakeWorkspaceCoordinator(_workspace_data())
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    assert result["entry_kind"] == const.ENTRY_KIND_WORKSPACE

    # Pre-2.0.0 keys are gone for good.
    assert "models_block_present" not in result
    assert "is_catalog_owner" not in result["coordinator"]
    assert "auto_models" not in result["coordinator"]
    assert "models_minutes" not in result["coordinator"]
    assert "last_models_fetch" not in result["coordinator"]

    # Usage evidence keys are the new coordinator contract.
    for key in (
        "last_update_success",
        "auto_usage",
        "usage_refresh_minutes",
        "last_usage_fetch",
        "last_usage_ok",
        "last_usage_attempt",
        "last_usage_outcome",
    ):
        assert key in result["coordinator"]

    # No account runtime loaded -> loaded False and all values None, but the
    # block itself (and its five fields) is always present.
    assert set(result["account"]) == {
        "loaded",
        "entry_id",
        "auto_fetch",
        "base_interval_minutes",
        "reachability_reason",
    }
    assert result["account"]["loaded"] is False
    assert result["account"]["entry_id"] is None
    assert result["account"]["auto_fetch"] is None
    assert result["account"]["base_interval_minutes"] is None
    # No coordinator, no evidence -> the matrix says "unknown".
    assert result["account"]["reachability_reason"] == "no_evidence"


def test_workspace_diag_account_block_loaded():
    """[PLAN2-M3][PLAN2-E2] Loaded account runtime is reflected in the export."""
    now = datetime.now(timezone.utc)
    account_coordinator = _account_coordinator(
        entry=_account_entry(),
        last_models_attempt=now,
        last_models_ok=now,
        last_models_outcome="ok",
    )
    workspace_coordinator = _FakeWorkspaceCoordinator(_workspace_data())
    entry = _workspace_entry()
    hass = _FakeHass({
        entry.entry_id: workspace_coordinator,
        const.DATA_ACCOUNT_KEY: account_coordinator,
    })

    result = _run(hass, entry)

    assert result["account"]["loaded"] is True
    assert result["account"]["entry_id"] == ACCOUNT_ENTRY_ID
    assert result["account"]["auto_fetch"] is True
    assert result["account"]["base_interval_minutes"] == 60
    assert result["account"]["reachability_reason"] == "fresh_ok"
    assert not any(
        "Konto-Entry nicht geladen" in hint
        for hint in result["interpretation_hints"]
    )


def test_workspace_diag_removes_owner_fields_but_keeps_workspaces_and_hints():
    """[PLAN2-M3] Workspace list + hints survive the owner-field removal."""
    coordinator = _FakeWorkspaceCoordinator(_workspace_data(
        workspaces=[{
            "key": "ws1",
            "status": "no_subscription",
            "note": "kein Abo",
            "windows": {"month": {"percent": 12.5, "resets_at": "r"}},
        }],
    ))
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    assert result["workspaces"][0]["key"] == "ws1"
    assert result["workspaces"][0]["windows"]["month"]["percent"] == 12.5
    assert any("KEIN aktives Abo" in hint for hint in result["interpretation_hints"])


def test_token_fingerprint_is_a_non_reversible_hash_not_a_raw_prefix():
    """[PLAN2-M3] Audited workspace token handling stays unchanged."""
    coordinator = _FakeWorkspaceCoordinator(_workspace_data())
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    expected_fp = hashlib.sha256(TOKEN.encode()).hexdigest()[:8]
    assert result["token_fingerprint"] == expected_fp

    # The raw token (and its old-style first-8-chars prefix) must not leak
    # into the export anywhere, e.g. via json.dumps(result).
    dumped = repr(result)
    assert TOKEN[:8] not in dumped
    assert TOKEN not in dumped


def test_workspace_name_is_redacted_consistently_with_redact_keys():
    """[PLAN2-M3] ``workspace_name`` redaction is unchanged."""
    coordinator = _FakeWorkspaceCoordinator(_workspace_data())
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    assert "workspace_name" in diagnostics.REDACT_KEYS
    assert result["workspace_name"] == REDACTED
    assert WORKSPACE_NAME not in repr(result)


def test_workspace_diag_error_path_keeps_branch_info():
    """[PLAN2-M3] Missing coordinator: kind/state plus the workspace hint."""
    entry = _workspace_entry()
    hass = _FakeHass({})

    result = _run(hass, entry)

    assert result["entry_kind"] == const.ENTRY_KIND_WORKSPACE
    assert result["entry_state"] == _CONFIG_ENTRY_STATE.LOADED
    assert "Coordinator nicht geladen" in result["error"]
    assert "token_fingerprint" not in result


# ---------------------------------------------------------------------------
# Account branch
# ---------------------------------------------------------------------------

def test_account_diag_has_no_token_keys_and_reports_models_state():
    """[PLAN2-E2] Account export: settings + models evidence, never a token."""
    now = datetime.now(timezone.utc)
    coordinator = _account_coordinator(
        models_block={"models": []},
        data=_account_data(
            models_block={"models": []},
            last_models_attempt=now.isoformat(),
            last_models_ok=now.isoformat(),
            last_models_outcome="ok",
        ),
    )
    entry = _account_entry(options={
        const.CONF_AUTO_UPDATE_MODELS: True,
        const.CONF_MODELS_REFRESH_MINUTES: 60,
        "authorization": "Bearer secret",
    })
    coordinator.entry = entry
    hass = _FakeHass({
        entry.entry_id: coordinator,
        const.DATA_ACCOUNT_KEY: coordinator,
    })

    result = _run(hass, entry)

    assert result["entry_kind"] == const.ENTRY_KIND_ACCOUNT
    assert result["config_entry_version"] == 8
    assert result["entry_state"] == _CONFIG_ENTRY_STATE.LOADED

    # An account entry has no token - neither a field nor a fingerprint.
    assert "token" not in result
    assert "token_fingerprint" not in result
    assert "workspace_name" not in result
    assert "secret" not in repr(result)

    assert result["options"]["authorization"] == REDACTED

    coordinator_block = result["coordinator"]
    assert coordinator_block["auto_fetch"] is True
    assert coordinator_block["base_interval_minutes"] == 60
    assert coordinator_block["fetch_interval_minutes"] == 60
    assert coordinator_block["backoff_active"] is False
    assert coordinator_block["backoff_failures"] == 0
    assert coordinator_block["last_models_attempt"] == now.isoformat()
    assert coordinator_block["last_models_ok"] == now.isoformat()
    assert coordinator_block["last_models_outcome"] == "ok"
    assert coordinator_block["last_error"] is None
    # Plan §8.3: the presence flag is gone in BOTH branches - the information
    # already lives in last_models_ok/last_models_outcome.
    assert "models_block_present" not in coordinator_block

    assert result["adoption"] is None


def test_account_diag_reachability_block_is_typed():
    """[PLAN2-E2] ``reachability`` carries reason/state/counts/TTLs correctly."""
    now = datetime.now(timezone.utc)
    coordinator = _account_coordinator(
        models_block={"models": []},
        last_models_attempt=now - timedelta(minutes=1),
        last_models_ok=now - timedelta(minutes=1),
        last_models_outcome="ok",
        base_interval_minutes=20,
    )
    entry = _account_entry()
    coordinator.entry = entry
    hass = _FakeHass({
        entry.entry_id: coordinator,
        const.DATA_ACCOUNT_KEY: coordinator,
    })

    reachability = _run(hass, entry)["reachability"]

    assert reachability["reason"] == "fresh_ok"
    assert reachability["state"] is True
    assert isinstance(reachability["state"], bool)
    assert set(reachability["counts"]) == {"ok", "neutral", "fail", "stale"}
    assert all(
        isinstance(value, int) for value in reachability["counts"].values()
    )
    assert reachability["counts"]["ok"] == 1
    # models TTL = 2 x base (20 min) -> 40 min, no workspace -> usage fallback
    assert reachability["models_ttl_s"] == 40 * 60
    assert reachability["usage_ttl_s"] == (
        const.USAGE_TTL_FACTOR * const.DEFAULT_USAGE_REFRESH_MINUTES * 60
    )


def test_account_diag_includes_adoption_report():
    """[PLAN2-E2] Last ``AdoptionReport`` is serialized via dataclasses.asdict."""
    coordinator = _account_coordinator()
    entry = _account_entry()
    coordinator.entry = entry
    report = _live("go_gauge.account").AdoptionReport(
        renamed=[("ws1_model_catalog", "account_model_catalog")],
        moved=["sensor.ws1_model_catalog"],
        device_moved="dev-account",
        merged_devices=["dev-old"],
        collisions=[("free_models", "sensor.ws1_free_models")],
        errors=["free_models:sensor.x: boom"],
        removed_settings=2,
    )
    hass = _FakeHass({
        entry.entry_id: coordinator,
        const.DATA_ACCOUNT_KEY: coordinator,
        const.DATA_ADOPTION_REPORT: report,
    })

    adoption = _run(hass, entry)["adoption"]

    assert adoption["renamed"] == [("ws1_model_catalog", "account_model_catalog")]
    assert adoption["moved"] == ["sensor.ws1_model_catalog"]
    assert adoption["device_moved"] == "dev-account"
    assert adoption["merged_devices"] == ["dev-old"]
    assert adoption["collisions"] == [("free_models", "sensor.ws1_free_models")]
    assert adoption["errors"] == ["free_models:sensor.x: boom"]
    assert adoption["removed_settings"] == 2


def test_account_diag_hints_explain_backoff_and_missing_catalog():
    """[PLAN2-E2] Interpretation hints cover "never fetched" and "backoff"."""
    coordinator = _account_coordinator(
        data=_account_data(backoff_active=True, backoff_failures=2,
                           last_error="HTTP 403"),
    )
    entry = _account_entry()
    coordinator.entry = entry
    hass = _FakeHass({
        entry.entry_id: coordinator,
        const.DATA_ACCOUNT_KEY: coordinator,
    })

    hints = _run(hass, entry)["interpretation_hints"]

    joined = " | ".join(hints)
    assert "NOCH NIE erfolgreich gefetcht" in joined
    assert "Backoff aktiv (2" in joined
    assert "HTTP 403" in joined


def test_account_diag_error_path_has_no_token_hint():
    """[PLAN2-E2] Missing account coordinator: kind/state, no token wording."""
    entry = _account_entry()
    hass = _FakeHass({})

    result = _run(hass, entry)

    assert result["entry_kind"] == const.ENTRY_KIND_ACCOUNT
    assert result["entry_state"] == _CONFIG_ENTRY_STATE.LOADED
    assert "Konto-Coordinator nicht geladen" in result["error"]
    assert "Token" not in result["error"]


if __name__ == "__main__":
    test_workspace_diag_has_account_block()
    test_workspace_diag_account_block_loaded()
    test_workspace_diag_removes_owner_fields_but_keeps_workspaces_and_hints()
    test_token_fingerprint_is_a_non_reversible_hash_not_a_raw_prefix()
    test_workspace_name_is_redacted_consistently_with_redact_keys()
    test_workspace_diag_error_path_keeps_branch_info()
    test_account_diag_has_no_token_keys_and_reports_models_state()
    test_account_diag_reachability_block_is_typed()
    test_account_diag_includes_adoption_report()
    test_account_diag_hints_explain_backoff_and_missing_catalog()
    test_account_diag_error_path_has_no_token_hint()
    print("OK")


# ---------------------------------------------------------------------------
# Burn-lookback debug block (2.1.0, plan D14)
# ---------------------------------------------------------------------------

class _BurnWorkspaceCoordinator(_FakeWorkspaceCoordinator):
    """Workspace coordinator double with the 2.1.0 burn helpers."""

    def __init__(self, data, *, lookbacks=None, burn=None, forecast=None):
        super().__init__(data)
        self.burn_lookback_minutes = (
            {"5h": 120, "week": 720, "month": 2880}
            if lookbacks is None
            else lookbacks
        )
        self._burn = dict(burn or {})
        self._forecast = dict(forecast or {})

    def burn_rate_diagnostics(self, key, window):
        return self._burn.get((key, window), {})

    def forecast_details(self, key, window):
        return self._forecast.get((key, window), {})


def _burn_workspace_data():
    """Workspace snapshot with one workspace and all three windows."""
    windows = {
        win: {"percent": 42.0, "resets_at": "2026-10-26T17:00:00+00:00"}
        for win in ("5h", "week", "month")
    }
    return _workspace_data(workspaces=[{
        "key": "ws1",
        "status": "ok",
        "note": "",
        "windows": windows,
    }])


def test_workspace_diag_has_burn_block_with_lookbacks_and_counts():
    """[REQ-020] ``burn`` explains lookback, sample counts and forecast method."""
    burn = {
        ("ws1", "5h"): {
            "lookback_minutes": 120,
            "samples": 7,
            "lookback_seconds": 7200,
            "lookback_too_short": False,
        },
        ("ws1", "week"): {
            "lookback_minutes": 720,
            "samples": 70,
            "lookback_seconds": 43200,
            "lookback_too_short": False,
        },
        ("ws1", "month"): {
            "lookback_minutes": 2880,
            "samples": 0,
            "lookback_seconds": 172800,
            "lookback_too_short": False,
        },
    }
    forecast = {
        ("ws1", "5h"): {"forecast_method": "pace", "elapsed_fraction": 0.4},
        ("ws1", "week"): {"forecast_method": "recent", "elapsed_fraction": 0.01},
        ("ws1", "month"): {"forecast_method": "unavailable", "elapsed_fraction": None},
    }
    coordinator = _BurnWorkspaceCoordinator(
        _burn_workspace_data(), burn=burn, forecast=forecast
    )
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    block = result["burn"]
    assert block["burn_lookback_minutes"] == {"5h": 120, "week": 720, "month": 2880}
    assert block["lookback_seconds"] == {"5h": 7200, "week": 43200, "month": 172800}

    workspace = block["workspaces"]["ws1"]
    assert workspace["samples"] == {"5h": 7, "week": 70, "month": 0}
    assert workspace["lookback_too_short"] == {
        "5h": False,
        "week": False,
        "month": False,
    }
    assert workspace["forecast_method"] == {
        "5h": "pace",
        "week": "recent",
        "month": "unavailable",
    }
    assert workspace["elapsed_fraction"] == {
        "5h": 0.4,
        "week": 0.01,
        "month": None,
    }


def test_workspace_diag_burn_block_uses_persisted_custom_lookbacks():
    """[REQ-020] The export reflects the live (slider) values, not the defaults."""
    coordinator = _BurnWorkspaceCoordinator(
        _burn_workspace_data(),
        lookbacks={"5h": 300, "week": 1440, "month": 5760},
    )
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    assert result["burn"]["burn_lookback_minutes"] == {
        "5h": 300,
        "week": 1440,
        "month": 5760,
    }
    assert result["burn"]["lookback_seconds"] == {
        "5h": 18000,
        "week": 86400,
        "month": 345600,
    }


def test_workspace_diag_burn_block_is_defensive_for_a_legacy_coordinator():
    """[REQ-020] A 2.0.x coordinator without the burn methods must not raise."""
    coordinator = _FakeWorkspaceCoordinator(_burn_workspace_data())
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    block = result["burn"]
    # No live lookbacks -> the documented defaults are reported.
    assert block["burn_lookback_minutes"] == {"5h": 120, "week": 720, "month": 2880}
    assert block["lookback_seconds"] == {"5h": 7200, "week": 43200, "month": 172800}
    # No helper methods -> empty per-window maps instead of invented values.
    assert block["workspaces"]["ws1"] == {
        "samples": {},
        "lookback_too_short": {},
        "forecast_method": {},
        "elapsed_fraction": {},
    }


def test_workspace_diag_burn_block_tolerates_a_garbage_lookback_value():
    """[REQ-020] A non-numeric lookback renders as None, not a crash."""
    coordinator = _BurnWorkspaceCoordinator(
        _burn_workspace_data(), lookbacks={"5h": "abc"}
    )
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    assert result["burn"]["burn_lookback_minutes"]["5h"] is None
    assert result["burn"]["lookback_seconds"]["5h"] is None
    # Untouched windows still fall back to their defaults.
    assert result["burn"]["burn_lookback_minutes"]["week"] == 720


def test_workspace_diag_burn_block_never_leaks_the_token():
    """[REQ-020] The burn debug block contains counts/methods only - no secret."""
    coordinator = _BurnWorkspaceCoordinator(_burn_workspace_data())
    entry = _workspace_entry()
    hass = _FakeHass({entry.entry_id: coordinator})

    result = _run(hass, entry)

    dumped = repr(result["burn"])
    assert TOKEN not in dumped
    assert TOKEN[:8] not in dumped
    assert "token" not in dumped.lower()
