#!/usr/bin/env python3
"""Verify that a release tag matches the integration manifest version.

HACS derives the remote version from the tag of the latest GitHub *release*,
while Home Assistant reports ``manifest.json:version``. They must be
character-identical apart from the tag's leading ``v`` (HACS rule):

    tag ``v1.5.0``  <->  manifest ``"version": "1.5.0"``

Environment:
- ``TAG_NAME``: the release tag (``github.event.release.tag_name``) or, for a
  manual ``workflow_dispatch`` run, the selected ref (``github.ref_name``).
- ``EVENT_NAME`` (optional): ``github.event_name``. When it is ``release`` the
  comparison is ALWAYS enforced. On other events (e.g. ``workflow_dispatch``
  against a branch) the script only fails when the ref looks like a version
  tag ``vX.Y.Z...``; a branch ref prints a skip note and exits 0.

Exit code 0 when in sync (or intentionally skipped), non-zero on mismatch.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_FILE = REPO_ROOT / "custom_components" / "go_gauge" / "manifest.json"

# Tag shapes that clearly are version tags: v1.5.0, v1.3.0b0, v2.0.0-rc1.
_VERSION_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+")


def main() -> int:
    tag = (os.environ.get("TAG_NAME") or "").strip()
    event = (os.environ.get("EVENT_NAME") or "").strip()

    if not tag:
        print("SKIP: TAG_NAME is not set - nothing to compare (local run?).")
        return 0

    # workflow_dispatch can target a branch: there is no tag to compare against,
    # so only a clearly version-shaped ref is enforced. Release events always
    # enforce the comparison - even for an unexpected tag format.
    if event != "release" and not _VERSION_TAG_RE.match(tag):
        print(
            f"SKIP: ref '{tag}' is not a version tag (event '{event or 'unknown'}') "
            "- manual dispatch on a branch, nothing to compare."
        )
        return 0

    if not MANIFEST_FILE.is_file():
        print(f"FAIL: manifest not found: {MANIFEST_FILE}")
        return 1

    try:
        manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        print(f"FAIL: {MANIFEST_FILE} is not valid JSON: {err}")
        return 1

    manifest_version = str(manifest.get("version", "")).strip()
    tag_version = tag[1:] if tag.startswith("v") else tag

    if not manifest_version:
        print(f"FAIL: no 'version' field in {MANIFEST_FILE}")
        return 1

    if manifest_version != tag_version:
        print(
            "FAIL: tag/manifest version mismatch - HACS would report a stale "
            "version.\n"
            f"  tag:      {tag}  (compared as '{tag_version}')\n"
            f"  manifest: {manifest_version}"
        )
        return 1

    print(f"OK: tag '{tag}' matches manifest version '{manifest_version}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
