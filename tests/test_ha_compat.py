#!/usr/bin/env python3
"""Home-Assistant compatibility contract tests (declared minimum HA 2026.8.0).

These tests pin the interface facts the integration relies on but cannot prove
with a plain unit test of its own logic.

Phase A - config-entry version contract:

- ``GoGaugeConfigFlow.VERSION`` is the single target version for *both* entry
  kinds (account and workspace entries start on the same version),
- ``async_migrate_entry`` accepts entries up to that version and uses exactly
  that version as its migration target.

Phase C - the 2.0.x release contract (plan ``docs/PLAN-account-entry-2.0.0.md``
§8.4, §9.1, §11.1 R1, §11.3 V2; current release ``2.0.3``):

- release metadata: ``manifest.version`` is the bare SemVer of the current
  release (``2.0.3``) and the declared minimum HA version ``2026.8.0`` is
  identical in ``hacs.json``, the README badge and
  ``docs/dashboards/README.md`` (§9.1),
- the deprecated device-transfer kwargs ``add_config_entry_id=`` /
  ``remove_config_entry_id=`` are gone from the integration *source* - only
  ``new_config_entry_id=`` (available from the declared minimum version,
  §11.3 V2) may be used (§11.1 R1),
- ``AccountCoordinator`` stays entry-*unbound*: no ``config_entry=`` kwarg at
  ``super().__init__``, no ``self.config_entry`` attribute and no
  ``async_config_entry_first_refresh`` call (a catalog fetch failure must never
  be able to fail the account setup, §6.3),
- the release rule itself: ``scripts/check_version_sync.py`` accepts the tag of
  the current release (``v2.0.3``) against the real manifest and rejects a stale
  tag (HACS rule).

Both source scans parse the module with :mod:`ast` instead of grepping text:
docstrings and comments (which legitimately *mention* ``config_entry=`` and
``async_config_entry_first_refresh`` while explaining why they are avoided)
are string literals / non-AST content and therefore cannot produce a false
positive. That is the documented heuristic for these guards.

``test_config_flow_version_is_eight``-style checks for ``VERSION == 8`` live in
``tests/test_config_flow.py`` / ``tests/test_account_flow.py``; only the
migration-target half is asserted here (see ``test_config_entry_version``) to
avoid duplicating them.

The shared ``homeassistant.*`` fakes live in ``tests/conftest.py``; this module
only loads the real integration modules (same pattern as the other test files).
"""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import re
import sys
from pathlib import Path

try:  # pytest prepends the tests dir to sys.path, so `import conftest` works
    import conftest
except ModuleNotFoundError:  # pragma: no cover - tests dir imported as package
    from tests import conftest  # type: ignore[no-redef]

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"
REPO_ROOT = Path(__file__).resolve().parent.parent
HACS_FILE = REPO_ROOT / "hacs.json"
MANIFEST_FILE = BASE / "manifest.json"
README_FILE = REPO_ROOT / "README.md"
DASHBOARD_README_FILE = REPO_ROOT / "docs" / "dashboards" / "README.md"
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load(name: str, path: str):
    """Dynamically import a module from file, bypassing sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Earlier-collected modules replace parts of the homeassistant.* fake tree with
# their own `_Flexible` instances - re-assert the shared stubs first.
conftest.install_ha_stubs()

# Coordinator-specific extension needed to import go_gauge.coordinator (see
# tests/test_init.py for the same, deliberately non-centralized pattern).
_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {
        "__class_getitem__": classmethod(lambda cls, item: cls),
        "__init__": lambda self, hass, logger, name=None, update_interval=None: None,
    },
)
_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})

const = _load("go_gauge.const", str(BASE / "const.py"))
_load("go_gauge.api", str(BASE / "api.py"))
_load("go_gauge.catalog", str(BASE / "catalog.py"))
_load("go_gauge.coordinator", str(BASE / "coordinator.py"))
# account.py binds ConfigEntryState at import time; a later-collected
# module may have swapped the fake module object, so re-install the stable
# double right before the import.
conftest.install_config_entry_state_stub()
_load("go_gauge.account", str(BASE / "account.py"))
init_module = _load("go_gauge.__init__", str(BASE / "__init__.py"))
config_flow = _load("go_gauge.config_flow", str(BASE / "config_flow.py"))

TARGET_VERSION = 8


def test_config_entry_version() -> None:
    """[PLAN2-A1] Config-flow version and migration target are both 8."""
    assert config_flow.GoGaugeConfigFlow.VERSION == TARGET_VERSION

    source = inspect.getsource(init_module.async_migrate_entry)
    # Guard: newer entries are rejected instead of "down-migrated".
    assert f"if entry.version > {TARGET_VERSION}" in source
    # Target: the last migration step advances the version cursor to 8.
    assert f"new_version = {TARGET_VERSION}" in source
    # The v8 step is a pure data marker - no registry/network call.
    assert "async_migrate_entries" not in source.split("if new_version >= 7:")[-1]


def test_entry_kind_constants_are_consistent() -> None:
    """[PLAN2-A1] Kind constants classify legacy data as workspace."""
    assert const.ENTRY_KIND_ACCOUNT == "account"
    assert const.ENTRY_KIND_WORKSPACE == "workspace"
    assert const.CONF_ENTRY_KIND == "entry_kind"
    # Pre-2.0.0 entries carry no marker at all -> workspace (default).
    assert const.conf_entry_kind({}) == const.ENTRY_KIND_WORKSPACE
    assert const.conf_entry_kind({const.CONF_ENTRY_KIND: "account"}) == "account"


# --- 2.0.x release metadata (plan §9.1) ------------------------------------


def test_manifest_version_is_2_0_3() -> None:
    """[PLAN2-M4] The released manifest version is the bare SemVer ``2.0.3``.

    The tag counterpart (``v2.0.3``) is covered by
    :func:`test_version_sync_tag_matches_manifest`; no ``v`` prefix may leak
    into ``manifest.version`` (HACS/hassfest reject it).
    """
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    assert manifest["version"] == "2.0.3"


def test_declared_min_version_consistent() -> None:
    """[PLAN2-M4] ``hacs.json`` minimum version matches README + dashboard docs.

    Plan §9.1 pins ``hacs.json.homeassistant`` to ``2026.8.0`` (device transfer
    via ``new_config_entry_id`` and the per-entry identifier indexing are not
    available before it). The same number must be advertised everywhere a user
    looks, otherwise a 2024.6 install looks supported while adoption breaks.
    """
    hacs = json.loads(HACS_FILE.read_text(encoding="utf-8"))
    assert hacs["homeassistant"] == "2026.8.0"

    readme = README_FILE.read_text(encoding="utf-8")
    # The shields.io badge is URL-encoded (``2026.8%2B`` == ``2026.8+``);
    # accept both spellings so a plain-text mention also satisfies the guard.
    assert "2026.8%2B" in readme or "2026.8+" in readme

    dashboard_readme = DASHBOARD_README_FILE.read_text(encoding="utf-8")
    assert "2026.8.0" in dashboard_readme


# --- source guards (plan §11.1 R1, §6.3) -----------------------------------


def _parsed_sources() -> dict[str, ast.Module]:
    """Parse every integration module once (AST, not text - see module docstring)."""
    return {
        path.name: ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(BASE.glob("*.py"))
    }


def test_no_deprecated_device_transfer_kwargs() -> None:
    """[PLAN2-M4][PLAN2-R1] Only ``new_config_entry_id=`` may transfer a device.

    ``add_config_entry_id`` / ``remove_config_entry_id`` are the deprecated
    (and, on older cores, differently behaving) device-transfer kwargs; the
    adoption moves a device exclusively via ``new_config_entry_id=`` and guards
    that call at runtime (``account._entry_transfer_supported``, plan §11.1 R1).

    The scan is AST-based: keyword arguments of real calls are inspected, so a
    docstring/comment that merely *names* the deprecated kwargs (as
    ``account.py`` does when explaining the guard) is not a finding.
    """
    deprecated = {"add_config_entry_id", "remove_config_entry_id"}
    offenders: list[str] = []
    transfer_calls: list[str] = []

    for filename, tree in _parsed_sources().items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg in deprecated:
                    offenders.append(f"{filename}:{keyword.value.lineno} {keyword.arg}=")
                elif keyword.arg == "new_config_entry_id":
                    transfer_calls.append(f"{filename}:{keyword.value.lineno}")

    assert offenders == []
    # Positive control: the scan must actually see the allowed transfer call,
    # otherwise an empty/dysfunctional scan would pass vacuously.
    assert transfer_calls, "expected at least one new_config_entry_id= call site"


def test_no_deprecated_device_registry_lookup() -> None:
    """[HA 2027.8/2027.9] No deprecated device-registry access in the source.

    Two registry access styles are deprecated and stop working in HA 2027.9:

    - using the registry's ``devices`` collection as a MAPPING (subscript,
      membership, ``.values()``, ``.get()``); enumerating it (iteration) stays
      supported,
    - the ``async_get_device`` lookup - replaced by ``async_get_devices``
      (identifier-wide) and ``async_get_device_by_identifier`` (entry-scoped).

    ``account.py`` reads the collection through ``account._device_entries``,
    which deliberately uses ``getattr(..., "devices")`` instead of an attribute
    access, so this guard must not forbid the attribute name itself - it forbids
    the attribute *node* (``.devices``), which is what a mapping access would
    look like in source.

    The scan is AST-based, like the other source guards here: ``account.py``
    explains both deprecations in docstrings, and a raw text grep would flag
    those explanations. A regex belt for the exact access shapes the HA warning
    names is asserted as well, plus a positive control so a broken scan cannot
    pass vacuously.
    """
    mapping_reads: list[str] = []
    deprecated_lookups: list[str] = []

    for filename, tree in _parsed_sources().items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if node.attr == "devices":
                mapping_reads.append(f"{filename}:{node.lineno} .devices")
            elif node.attr == "async_get_device":
                deprecated_lookups.append(f"{filename}:{node.lineno} async_get_device")

    assert mapping_reads == [], (
        "the devices collection must not be accessed as an attribute/mapping "
        f"(HA 2027.9): {mapping_reads}"
    )
    assert deprecated_lookups == [], (
        f"async_get_device is deprecated (HA 2027.8): {deprecated_lookups}"
    )

    account_source = (BASE / "account.py").read_text(encoding="utf-8")
    assert not re.search(r"\bdev_reg\.devices\b", account_source)
    assert ".devices.values(" not in account_source
    # Positive control: the supported lookup must be present, otherwise the
    # negative assertions above could pass on a source that resolves nothing.
    assert "async_get_devices(" in account_source


def test_account_coordinator_has_no_config_entry() -> None:
    """[PLAN2-M4] ``AccountCoordinator`` is not bound to the config entry.

    Plan §6.3: the account coordinator must not be created with HA's
    ``config_entry=`` kwarg, must not expose ``self.config_entry`` and must
    never call ``async_config_entry_first_refresh`` - otherwise a failed
    catalog fetch (or a 401) could fail/reauth the account entry, which carries
    no token.

    AST-based (see module docstring): ``account.py`` explains all three
    prohibitions in docstrings, so a raw text grep would be a false positive by
    construction.
    """
    tree = ast.parse((BASE / "account.py").read_text(encoding="utf-8"))
    coordinator = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "AccountCoordinator"
        ),
        None,
    )
    assert coordinator is not None, "AccountCoordinator class not found in account.py"

    config_entry_kwargs: list[str] = []
    self_config_entry: list[str] = []
    first_refresh_calls: list[str] = []

    for node in ast.walk(coordinator):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "config_entry":
                    config_entry_kwargs.append(f"line {node.lineno}")
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "async_config_entry_first_refresh":
                first_refresh_calls.append(f"line {node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr == "config_entry":
            if isinstance(node.value, ast.Name) and node.value.id == "self":
                self_config_entry.append(f"line {node.lineno}")

    assert config_entry_kwargs == []
    assert self_config_entry == []
    assert first_refresh_calls == []


# --- release rule (HACS: tag <-> manifest) ---------------------------------


def test_version_sync_tag_matches_manifest(monkeypatch) -> None:
    """[PLAN2-M4] Real manifest + tag ``v2.0.3`` pass ``check_version_sync``.

    Forward test of the release rule (plan §8.4/§8.5) against the *real*
    ``custom_components/go_gauge/manifest.json``: the release tag must match
    the manifest version exactly, and a stale tag must be rejected instead of
    silently shipping a mismatched HACS version. No network involved - the
    script only reads the manifest and the two environment variables.
    """
    spec = importlib.util.spec_from_file_location(
        "check_version_sync_forward", SCRIPTS_DIR / "check_version_sync.py"
    )
    assert spec is not None and spec.loader is not None
    version_sync = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(version_sync)

    monkeypatch.setattr(version_sync, "MANIFEST_FILE", MANIFEST_FILE)
    monkeypatch.setenv("EVENT_NAME", "release")

    monkeypatch.setenv("TAG_NAME", "v2.0.3")
    assert version_sync.main() == 0

    # Counter-check: a stale tag must fail, otherwise the guard proves nothing.
    monkeypatch.setenv("TAG_NAME", "v1.9.9")
    assert version_sync.main() == 1
