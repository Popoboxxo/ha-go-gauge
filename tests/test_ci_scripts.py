"""Tests for the CI helper scripts.

Covers ``scripts/check_json_consistency.py`` (JSON well-formedness +
recursive translation key parity) and ``scripts/check_version_sync.py``
(release tag <-> manifest version, HACS rule). The scripts are loaded by
path so they stay runnable as standalone CI steps without packaging.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_script(name: str, filename: str):
    """Import a scripts/ module by file path (does not run main())."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


json_consistency = _load_script("check_json_consistency", "check_json_consistency.py")
version_sync = _load_script("check_version_sync", "check_version_sync.py")


# --- check_json_consistency ------------------------------------------------


def test_repo_translations_are_consistent():
    """The checked-in strings.json / translations/*.json must be in sync."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "check_json_consistency.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK:" in result.stdout


def test_collect_key_paths_recurses_dicts_and_lists():
    paths = json_consistency._collect_key_paths({"a": {"b": 1}, "c": [{"d": 2}]})
    assert paths == {"a", "a.b", "c", "c[0]", "c[0].d"}


def _write_consistency_fixture(tmp_path: Path, translation: dict) -> dict:
    integration = tmp_path / "custom_components" / "go_gauge"
    translations = integration / "translations"
    translations.mkdir(parents=True)
    strings = {"config": {"step": {"user": {"title": "T", "data": {"x": "X"}}}}}
    (integration / "manifest.json").write_text(json.dumps({"version": "1.5.0"}))
    (integration / "strings.json").write_text(json.dumps(strings))
    (translations / "en.json").write_text(json.dumps(translation))
    return {
        "STRINGS_FILE": integration / "strings.json",
        "TRANSLATIONS_DIR": translations,
        "META_FILES": [integration / "manifest.json", integration / "strings.json"],
    }


def test_missing_translation_key_fails(tmp_path, monkeypatch, capsys):
    paths = _write_consistency_fixture(tmp_path, {"config": {"step": {"user": {"title": "T"}}}})
    for attr, value in paths.items():
        monkeypatch.setattr(json_consistency, attr, value)

    assert json_consistency.main() == 1
    out = capsys.readouterr().out
    assert "missing key: config.step.user.data" in out


def test_extra_translation_key_fails(tmp_path, monkeypatch, capsys):
    extra = {"config": {"step": {"user": {"title": "T", "data": {"x": "X", "extra": "E"}}}}}
    paths = _write_consistency_fixture(tmp_path, extra)
    for attr, value in paths.items():
        monkeypatch.setattr(json_consistency, attr, value)

    assert json_consistency.main() == 1
    assert "extra key:   config.step.user.data.extra" in capsys.readouterr().out


def test_invalid_json_fails(tmp_path, monkeypatch, capsys):
    paths = _write_consistency_fixture(tmp_path, {"config": {}})
    (paths["TRANSLATIONS_DIR"] / "en.json").write_text("{ not json")
    for attr, value in paths.items():
        monkeypatch.setattr(json_consistency, attr, value)

    assert json_consistency.main() == 1
    assert "invalid JSON" in capsys.readouterr().out


# --- check_version_sync ----------------------------------------------------


def _manifest(tmp_path: Path, version: str) -> Path:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"version": version}))
    return manifest


def test_version_sync_release_tag_matches(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(version_sync, "MANIFEST_FILE", _manifest(tmp_path, "1.5.0"))
    monkeypatch.setenv("TAG_NAME", "v1.5.0")
    monkeypatch.setenv("EVENT_NAME", "release")

    assert version_sync.main() == 0
    assert "OK:" in capsys.readouterr().out


def test_version_sync_release_tag_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(version_sync, "MANIFEST_FILE", _manifest(tmp_path, "1.5.0"))
    monkeypatch.setenv("TAG_NAME", "v1.6.0")
    monkeypatch.setenv("EVENT_NAME", "release")

    assert version_sync.main() == 1
    assert "mismatch" in capsys.readouterr().out


def test_version_sync_branch_ref_is_skipped(monkeypatch, capsys):
    monkeypatch.setenv("TAG_NAME", "ci/some-branch")
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")

    assert version_sync.main() == 0
    assert "SKIP:" in capsys.readouterr().out


def test_version_sync_release_event_always_compares(tmp_path, monkeypatch, capsys):
    """A release event is enforced even for a non-``vX.Y.Z`` tag."""
    monkeypatch.setattr(version_sync, "MANIFEST_FILE", _manifest(tmp_path, "1.5.0"))
    monkeypatch.setenv("TAG_NAME", "release-1.6.0")
    monkeypatch.setenv("EVENT_NAME", "release")

    assert version_sync.main() == 1
    assert "mismatch" in capsys.readouterr().out


def test_version_sync_missing_tag_is_skipped(monkeypatch, capsys):
    monkeypatch.delenv("TAG_NAME", raising=False)
    monkeypatch.setenv("EVENT_NAME", "workflow_dispatch")

    assert version_sync.main() == 0
    assert "SKIP:" in capsys.readouterr().out
