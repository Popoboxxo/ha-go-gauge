#!/usr/bin/env python3
"""Validate JSON meta-files and translation key parity for go_gauge.

Checks performed (no third-party dependencies):

1. Every configuration/meta JSON file parses:
   - ``custom_components/go_gauge/manifest.json``
   - ``hacs.json``
   - ``custom_components/go_gauge/strings.json``
   - every ``custom_components/go_gauge/translations/*.json``
2. Recursive key parity: the key path set of each ``translations/<lang>.json``
   must exactly match the key path set of ``strings.json``. Missing and extra
   paths are reported per file so a stale translation cannot slip through.

Exit code 0 when everything is consistent, non-zero (1) otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATION_DIR = REPO_ROOT / "custom_components" / "go_gauge"
STRINGS_FILE = INTEGRATION_DIR / "strings.json"
TRANSLATIONS_DIR = INTEGRATION_DIR / "translations"

META_FILES = [
    INTEGRATION_DIR / "manifest.json",
    REPO_ROOT / "hacs.json",
    STRINGS_FILE,
]


def _rel(path: Path) -> str:
    """Return a repo-relative path for readable messages."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _collect_key_paths(node: Any, prefix: str = "") -> set[str]:
    """Return every nested key path of a JSON document.

    Dicts contribute ``a.b.c``; lists contribute ``a[0]`` and recurse into the
    element so structural differences between a master and a translation are
    detected as well. Scalar values contribute nothing beyond their own key.
    """
    paths: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths.update(_collect_key_paths(value, path))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            path = f"{prefix}[{index}]"
            paths.add(path)
            paths.update(_collect_key_paths(value, path))
    return paths


def _load(path: Path) -> Any:
    """Parse one JSON file; raise on invalid JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    errors: list[str] = []

    if not STRINGS_FILE.is_file():
        print(f"FAIL: master translation file not found: {_rel(STRINGS_FILE)}")
        return 1

    translation_files = sorted(TRANSLATIONS_DIR.glob("*.json")) if TRANSLATIONS_DIR.is_dir() else []
    if not translation_files:
        print(f"FAIL: no translation files found in {_rel(TRANSLATIONS_DIR)}")
        return 1

    # --- 1. JSON well-formedness ------------------------------------------
    parsed: dict[Path, Any] = {}
    for path in [*META_FILES, *translation_files]:
        if not path.is_file():
            errors.append(f"{_rel(path)}: file is missing")
            continue
        try:
            parsed[path] = _load(path)
        except json.JSONDecodeError as err:
            errors.append(f"{_rel(path)}: invalid JSON ({err})")

    if errors:
        for message in errors:
            print(f"FAIL: {message}")
        return 1

    # --- 2. Recursive key parity vs. strings.json -------------------------
    master_paths = _collect_key_paths(parsed[STRINGS_FILE])
    parity_failed = False
    for path in translation_files:
        translation_paths = _collect_key_paths(parsed[path])
        missing = sorted(master_paths - translation_paths)
        extra = sorted(translation_paths - master_paths)
        if missing or extra:
            parity_failed = True
            print(f"FAIL: {_rel(path)} does not match {_rel(STRINGS_FILE)}")
            for key in missing:
                print(f"  missing key: {key}")
            for key in extra:
                print(f"  extra key:   {key}")

    if parity_failed:
        return 1

    print(
        f"OK: {len(translation_files)} translation file(s) consistent with "
        f"{_rel(STRINGS_FILE)} ({len(master_paths)} key paths)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
