#!/usr/bin/env python3
"""Repository hygiene contract tests for ha-go-gauge (HA-free, stdlib only).

These tests pin the *repository surface* a HACS user actually touches before a
single line of the integration runs: the bilingual README pair, the My-Link
install button, the screenshot asset, the HACS/manifest metadata, the version
triplet (tag <-> ``manifest.version`` <-> ``CHANGELOG.md``), the community
files (contributing, security, code of conduct, issue/PR templates, CODEOWNERS,
dependabot) and the CI gate set.

Deliberately no Home Assistant import and no third-party dependency:

- the assertions are pure file/metadata checks, so they must run even when the
  integration cannot be imported at all (that is the point of a hygiene gate -
  a `ModuleNotFoundError` in one platform file must not hide a broken README),
- CI installs only ``pytest``/``pytest-asyncio``/``voluptuous``, therefore the
  YAML files are checked with plain-text/regex rules instead of a YAML parser
  (see :func:`test_issue_templates_present` and
  :func:`test_validate_workflow_covers_gates`),
- the PNG is inspected through its IHDR chunk (``struct``), not Pillow.

Every path is derived from ``REPO_ROOT``; nothing here touches the network or
an absolute path outside the repository.
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

README_EN = REPO_ROOT / "README.md"
README_DE = REPO_ROOT / "README.de.md"
HACS_FILE = REPO_ROOT / "hacs.json"
MANIFEST_FILE = REPO_ROOT / "custom_components" / "go_gauge" / "manifest.json"
CHANGELOG_FILE = REPO_ROOT / "CHANGELOG.md"
CONTRIBUTING_FILE = REPO_ROOT / "CONTRIBUTING.md"
SECURITY_FILE = REPO_ROOT / "SECURITY.md"
CODE_OF_CONDUCT_FILE = REPO_ROOT / "CODE_OF_CONDUCT.md"
PR_TEMPLATE_FILE = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
CODEOWNERS_FILE = REPO_ROOT / ".github" / "CODEOWNERS"
DEPENDABOT_FILE = REPO_ROOT / ".github" / "dependabot.yml"
ISSUE_TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "validate.yml"
DASHBOARDS_DIR = REPO_ROOT / "docs" / "dashboards"
DASHBOARDS_EN = DASHBOARDS_DIR / "README.en.md"
DASHBOARDS_DE = DASHBOARDS_DIR / "README.md"

EXPECTED_VERSION = "2.0.3"
EXPECTED_MIN_HA = "2026.8.0"
LANGUAGE_HEADER = "[English](README.md) | [Deutsch](README.de.md)"
# Same switcher scheme one level down: the dashboard docs are README.en.md /
# README.md, so the targets are relative to docs/dashboards/.
DASHBOARDS_HEADER = "[English](README.en.md) | [Deutsch](README.md)"
MY_LINK = (
    "https://my.home-assistant.io/redirect/hacs_repository/"
    "?owner=Popoboxxo&repository=ha-go-gauge&category=integration"
)
MY_LINK_BADGE = "https://my.home-assistant.io/badges/hacs_repository.svg"
SCREENSHOT_REL = "docs/images/go-gauge-konto-device.png"

# Negation vocabulary accepted for the "no default-store claim" guard.
_NEGATION_RE = re.compile(r"\b(?:not|no|never|nicht|kein\w*)\b", re.IGNORECASE)
# Hyphen- and markdown-tolerant spellings of the same claim ("default store",
# "Default-Store", "default *store*" ...).
_STORE_CLAIM_RE = re.compile(
    r"\b(?:default[\s\-*_]+(?:store|catalog(?:ue)?)"
    r"|hacs[\s\-*_]+store"
    r"|part[\s\-*_]+of[\s\-*_]+hacs"
    r"|available[\s\-*_]+in[\s\-*_]+hacs)\b",
    re.IGNORECASE,
)


def _read(path: Path) -> str:
    """Read a repository file as UTF-8 text, failing with the path in the message."""
    assert path.is_file(), f"expected file is missing: {path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def _blocks(text: str) -> list[str]:
    """Split markdown text into paragraph/list blocks (blank line separated).

    Assertions about a *statement* must look at the logical block, not at one
    physical line: these READMEs wrap at ~80 columns, so a negation and the
    phrase it negates routinely end up on adjacent lines of the same sentence.
    """
    blocks = re.split(r"\n\s*\n", text)
    return [block for block in blocks if block.strip()]


def _first_line(text: str) -> str:
    """Return the first line, stripped of trailing whitespace/CR."""
    return text.splitlines()[0].rstrip() if text else ""


# --- 1. bilingual README pair ---------------------------------------------


def test_bilingual_readme_pair() -> None:
    """[REPO-HYGIENE] English + German READMEs exist and cross-link each other.

    ``README.md`` is the English primary document (what HACS/GitHub render by
    default), ``README.de.md`` the full German counterpart. Both must carry the
    identical language switcher on line 1, otherwise a reader lands in a
    document with no way back.
    """
    english = _read(README_EN)
    german = _read(README_DE)

    for path, text in ((README_EN, english), (README_DE, german)):
        assert len(text.encode("utf-8")) > 5000, (
            f"{path.relative_to(REPO_ROOT)} is truncated "
            f"({len(text.encode('utf-8'))} bytes): expected a full README"
        )

    assert _first_line(english) == LANGUAGE_HEADER, (
        f"README.md line 1 is {_first_line(english)!r}, expected {LANGUAGE_HEADER!r}"
    )
    assert _first_line(german) == LANGUAGE_HEADER, (
        f"README.de.md line 1 is {_first_line(german)!r}, expected {LANGUAGE_HEADER!r}"
    )
    assert _first_line(english) == _first_line(german)

    assert "[Deutsch](README.de.md)" in _first_line(english)
    assert "[English](README.md)" in _first_line(german)


# --- 2. My-Link install button --------------------------------------------


def test_my_link_present_in_both_readmes() -> None:
    """[REPO-HYGIENE] Both READMEs ship the My-Link button and the badge.

    The redirect URL is the one-click install path; a typo in owner,
    repository or category makes Home Assistant open an empty HACS page, which
    no file-level validator would catch. Both the human-readable link and the
    shields badge must be present in both languages.
    """
    for path in (README_EN, README_DE):
        text = _read(path)
        name = path.relative_to(REPO_ROOT)
        assert MY_LINK in text, f"{name} is missing the My-Link redirect URL {MY_LINK}"
        assert MY_LINK_BADGE in text, f"{name} is missing the My-Link badge {MY_LINK_BADGE}"


# --- 3. screenshot asset ---------------------------------------------------


def test_screenshot_is_a_wide_png() -> None:
    """[REPO-HYGIENE] The embedded screenshot exists and is a wide, real PNG.

    The README embeds ``docs/images/go-gauge-konto-device.png`` at ``width=720``;
    a missing or 1×1 placeholder file would silently render a broken image on
    GitHub. Dimensions are read from the IHDR chunk (bytes 16-24) with
    ``struct`` so the check needs no Pillow.
    """
    readme = _read(README_EN)
    sources = re.findall(r'<img\s+src="([^"]+)"', readme)
    assert SCREENSHOT_REL in sources, (
        f"README.md does not embed {SCREENSHOT_REL!r}; found image sources: {sources}"
    )

    for path in (README_EN, README_DE):
        assert f'src="{SCREENSHOT_REL}"' in _read(path), (
            f"{path.relative_to(REPO_ROOT)} does not embed {SCREENSHOT_REL!r}"
        )

    screenshot = REPO_ROOT / SCREENSHOT_REL
    assert screenshot.is_file(), f"screenshot is missing: {SCREENSHOT_REL}"
    data = screenshot.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", (
        f"{SCREENSHOT_REL} is not a PNG (first bytes: {data[:8]!r})"
    )
    assert len(data) > 24, f"{SCREENSHOT_REL} is too small to hold an IHDR chunk"
    width, height = struct.unpack(">II", data[16:24])
    assert width > 1000, f"{SCREENSHOT_REL} is only {width}px wide: expected a real screenshot"
    assert height > 100, f"{SCREENSHOT_REL} is only {height}px high: expected a real screenshot"


# --- 4. hacs.json ----------------------------------------------------------


def test_hacs_json_metadata() -> None:
    """[REPO-HYGIENE] ``hacs.json`` has no legacy ``render_readme`` key.

    ``render_readme`` is a leftover of the HACS 1.x era and no longer
    meaningful; the file must stay minimal (``name`` + supported HA version)
    and declare the minimum HA version the integration actually needs.
    """
    hacs = json.loads(_read(HACS_FILE))

    assert "render_readme" not in hacs, (
        "hacs.json must not contain the legacy 'render_readme' key: "
        f"{sorted(hacs)}"
    )
    assert isinstance(hacs.get("name"), str) and hacs["name"].strip(), (
        f"hacs.json 'name' must be a non-empty string, got {hacs.get('name')!r}"
    )
    assert hacs.get("homeassistant") == EXPECTED_MIN_HA, (
        f"hacs.json 'homeassistant' is {hacs.get('homeassistant')!r}, "
        f"expected {EXPECTED_MIN_HA!r}"
    )


# --- 5. integration manifest ----------------------------------------------


def test_manifest_metadata() -> None:
    """[REPO-HYGIENE] ``manifest.json`` carries the HACS-required fields.

    ``codeowners``/``config_flow``/``iot_class`` are hassfest requirements, and
    ``domain`` must match the ``custom_components`` folder.

    ``categories`` is *optional*: hassfest 2026.10 rejects the key
    (``not a valid option at 'categories'``), while the HACS action only warns
    that the manifest has none. So the field is absent today, and the shape
    check below is future-proofing - when the shipped hassfest accepts the key
    it can simply be added back without touching this test.
    """
    manifest = json.loads(_read(MANIFEST_FILE))

    assert manifest.get("domain") == "go_gauge", (
        f"manifest 'domain' is {manifest.get('domain')!r}, expected 'go_gauge'"
    )
    if "categories" in manifest:
        categories = manifest["categories"]
        assert isinstance(categories, list) and 1 <= len(categories) <= 3, (
            f"manifest 'categories' must be a list of 1-3 entries, got {categories!r}"
        )
        assert all(isinstance(item, str) and item.strip() for item in categories), (
            f"manifest 'categories' entries must be non-empty strings, got {categories!r}"
        )
        assert len(set(categories)) == len(categories), (
            f"manifest 'categories' must not contain duplicates, got {categories!r}"
        )
    assert manifest.get("version") == EXPECTED_VERSION, (
        f"manifest 'version' is {manifest.get('version')!r}, expected {EXPECTED_VERSION!r}"
    )
    assert "@Popoboxxo" in (manifest.get("codeowners") or []), (
        f"manifest 'codeowners' must list @Popoboxxo, got {manifest.get('codeowners')!r}"
    )
    assert manifest.get("config_flow") is True, (
        f"manifest 'config_flow' must be the boolean true, got {manifest.get('config_flow')!r}"
    )
    assert manifest.get("iot_class"), "manifest 'iot_class' must be set (HACS metadata)"
    assert not str(manifest["version"]).startswith("v"), (
        f"manifest 'version' must be bare SemVer without a 'v' prefix, got "
        f"{manifest.get('version')!r}"
    )


# --- 6. version consistency (tag <-> manifest <-> changelog) --------------


def test_version_consistency_changelog_manifest() -> None:
    """[REPO-HYGIENE] ``CHANGELOG.md`` and ``manifest.version`` agree on 2.0.3.

    The tag counterpart (``v2.0.3``) is covered by
    ``tests/test_ha_compat.py``; here only the documented release history is
    pinned: the new block exists exactly once, the previous releases are not
    rewritten, and the manifest carries the bare SemVer.
    """
    changelog = _read(CHANGELOG_FILE)
    manifest = json.loads(_read(MANIFEST_FILE))

    headings = [
        f"## [{EXPECTED_VERSION}] — 2026-10-25",
        "## [2.0.1] — 2026-10-25",
        "## [2.0.0] — 2026-10-25",
    ]
    for heading in headings:
        assert changelog.count(heading) == 1, (
            f"CHANGELOG.md must contain the heading {heading!r} exactly once, "
            f"found {changelog.count(heading)}"
        )

    # Ordering: newest release block first, directly under the "# Changelog" title.
    positions = [changelog.index(heading) for heading in headings]
    assert positions == sorted(positions), (
        f"CHANGELOG.md release blocks are out of order: {headings} at {positions}"
    )
    assert changelog.index(headings[0]) < changelog.index(headings[1])

    assert manifest["version"] == EXPECTED_VERSION, (
        f"manifest 'version' is {manifest['version']!r}, expected {EXPECTED_VERSION!r}"
    )
    assert not manifest["version"].startswith("v"), (
        f"manifest.version must not carry the tag's 'v' prefix: {manifest['version']!r}"
    )
    assert f"## [{manifest['version']}]" in changelog, (
        f"manifest.version {manifest['version']!r} has no matching CHANGELOG heading"
    )


# --- 7. community files ----------------------------------------------------


def test_community_files_present() -> None:
    """[REPO-HYGIENE] Contributing/security/CoC/PR/CODEOWNERS/dependabot exist.

    These files are what GitHub surfaces in the UI (issue chooser, PR form,
    security tab, "Contributing" link). Empty placeholders are as bad as
    missing files, so each one must carry real content.
    """
    required = {
        CONTRIBUTING_FILE: 500,
        SECURITY_FILE: 300,
        CODE_OF_CONDUCT_FILE: 300,
        PR_TEMPLATE_FILE: 100,
        CODEOWNERS_FILE: 10,
        DEPENDABOT_FILE: 50,
    }
    for path, minimum in required.items():
        text = _read(path)
        assert len(text.strip()) >= minimum, (
            f"{path.relative_to(REPO_ROOT)} looks like an empty placeholder "
            f"({len(text.strip())} chars, expected at least {minimum})"
        )


# --- 8. issue templates ----------------------------------------------------


def test_issue_templates_present() -> None:
    """[REPO-HYGIENE] The GitHub issue forms and the chooser config are valid-ish.

    Checked with regex, not PyYAML: CI installs only pytest/pytest-asyncio/
    voluptuous, and a YAML parser would be a new dependency for a structural
    check. The top-level keys of a GitHub issue *form* must start in column 0
    (``name:``, ``description:``, ``body:``), which is exactly what a regex can
    prove; ``config.yml`` must disable blank issues and offer contact links
    (the "not in the default store" question does not belong in a bug report).
    """
    for form in ("bug_report.yml", "feature_request.yml"):
        path = ISSUE_TEMPLATE_DIR / form
        text = _read(path)
        for key in ("name:", "description:", "body:"):
            assert re.search(rf"(?m)^{re.escape(key)}\s*\S", text), (
                f"{path.relative_to(REPO_ROOT)} has no top-level {key!r} key"
            )

    config = _read(ISSUE_TEMPLATE_DIR / "config.yml")
    assert re.search(r"(?m)^blank_issues_enabled\s*:", config), (
        ".github/ISSUE_TEMPLATE/config.yml is missing 'blank_issues_enabled'"
    )
    assert re.search(r"(?m)^contact_links\s*:", config), (
        ".github/ISSUE_TEMPLATE/config.yml is missing 'contact_links'"
    )
    assert re.search(r"(?m)^\s+-\s+name\s*:\s*\S", config), (
        ".github/ISSUE_TEMPLATE/config.yml has no contact link entries"
    )


# --- 9. CODEOWNERS + dependabot -------------------------------------------


def test_codeowners_and_dependabot() -> None:
    """[REPO-HYGIENE] Default code owner is set and dependabot watches Actions.

    The repo has no runtime dependencies, so ``github-actions`` is the only
    ecosystem that can drift; a weekly interval keeps the moving
    ``hacs/action@main`` / ``hassfest@master`` pins reviewable.
    """
    codeowners = _read(CODEOWNERS_FILE)
    assert re.search(r"(?m)^\*\s+@Popoboxxo\s*$", codeowners), (
        ".github/CODEOWNERS must contain the default rule '* @Popoboxxo'"
    )

    dependabot = _read(DEPENDABOT_FILE)
    assert re.search(r'package-ecosystem:\s*"?github-actions"?', dependabot), (
        ".github/dependabot.yml must watch the 'github-actions' ecosystem"
    )
    assert re.search(r'interval:\s*"?weekly"?', dependabot), (
        ".github/dependabot.yml must use a weekly schedule interval"
    )


# --- 10. CI workflow gates -------------------------------------------------


def test_validate_workflow_covers_all_gates() -> None:
    """[REPO-HYGIENE] ``validate.yml`` runs every gate this repo relies on.

    HACS and hassfest validate against *moving* upstream actions, so the
    workflow also needs a schedule/drift trigger. And no gate may be silenced
    with an ``ignore:`` key - a skip that nobody sees is worse than a failing
    check.
    """
    workflow = _read(WORKFLOW_FILE)

    required = {
        "schedule:": "scheduled drift check",
        "cron:": "cron expression for the schedule",
        "workflow_dispatch:": "manual trigger",
        "hacs/action": "HACS validation",
        "hassfest": "hassfest validation",
        "pytest": "unit test job",
        "ruff": "lint job",
        "mypy": "type check job",
        "check_json_consistency.py": "JSON meta-file consistency check",
        "check_canon.py": "Gauge entity canon check",
    }
    missing = [f"{token!r} ({why})" for token, why in required.items() if token not in workflow]
    assert missing == [], f".github/workflows/validate.yml is missing: {missing}"

    offenders = [
        line for line in workflow.splitlines() if re.search(r"\bignore\s*:", line)
    ]
    assert offenders == [], (
        "validate.yml must not silence a gate with an 'ignore:' key: "
        f"{[line.strip() for line in offenders]}"
    )


# --- 11. dashboard docs ----------------------------------------------------


def test_dashboard_docs_bilingual_and_linked() -> None:
    """[REPO-HYGIENE] Dashboard docs are bilingual and linked from both READMEs.

    The dashboard YAMLs are unusable without their pairing instructions, so the
    language switcher must be on line 1 of both dashboard docs and each README
    must point at the matching language variant.
    """
    for path in (DASHBOARDS_DE, DASHBOARDS_EN):
        text = _read(path)
        assert _first_line(text) == DASHBOARDS_HEADER, (
            f"{path.relative_to(REPO_ROOT)} line 1 is {_first_line(text)!r}, "
            f"expected {DASHBOARDS_HEADER!r}"
        )

    english = _read(README_EN)
    german = _read(README_DE)
    assert "docs/dashboards/README.en.md" in english, (
        "README.md (English) must link the English dashboard docs "
        "(docs/dashboards/README.en.md)"
    )
    assert "docs/dashboards/README.md" in german, (
        "README.de.md (German) must link the German dashboard docs "
        "(docs/dashboards/README.md)"
    )


# --- 12. no default-store claim -------------------------------------------


def test_no_hacs_default_store_claim() -> None:
    """[REPO-HYGIENE] No README paragraph claims default-store availability.

    This repository is a HACS *custom* repository only. Every statement that
    mentions the default store/catalog must be a negation ("deliberately not
    listed"), because a positive claim would make users search the store and
    conclude the integration is broken/removed.

    The unit is the markdown *block*, not the physical line: at ~80 columns the
    negation and its object sit on adjacent lines of the same sentence. A
    positive control asserts at least one such statement exists at all, so the
    guard cannot pass on a README that simply never talks about the store.
    """
    offenders: list[str] = []
    claims = 0

    for path in (README_EN, README_DE):
        for block in _blocks(_read(path)):
            if not _STORE_CLAIM_RE.search(block):
                continue
            claims += 1
            if not _NEGATION_RE.search(block):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}: {' '.join(block.split())[:160]}"
                )

    assert offenders == [], (
        "README statements about the HACS default store/catalog must be negated "
        f"(this repo is a custom repository only): {offenders}"
    )
    assert claims >= 1, (
        "positive control failed: neither README states that the repo is not in "
        "the HACS default store - the guard would be vacuous"
    )


# --- 13. compatibility + help sections ------------------------------------


def test_readme_compatibility_and_help_sections() -> None:
    """[REPO-HYGIENE] Both READMEs state the compatibility facts and the fallback.

    A user must be able to answer three questions from either language: which
    HA version is required (``2026.8.0``), which HACS generation
    (``HACS 2.x``), when the My-Link reaches the Marketplace (``2026.11``) - and
    where to go if HACS itself is unavailable (manual copy of
    ``custom_components/go_gauge``). Troubleshooting/FAQ and a Diagnostics
    section must exist in both.
    """
    for path in (README_EN, README_DE):
        text = _read(path)
        name = path.relative_to(REPO_ROOT)

        assert EXPECTED_MIN_HA in text, f"{name} does not state the minimum HA {EXPECTED_MIN_HA}"
        assert re.search(r"HACS\s*\**\s*2", text), (
            f"{name} does not state the required HACS generation (HACS 2.x)"
        )
        assert "2026.11" in text, f"{name} does not mention the 2026.11 Marketplace switch"
        assert "custom_components/go_gauge" in text, (
            f"{name} does not document the manual install fallback path "
            "'custom_components/go_gauge'"
        )
        assert re.search(r"(?mi)^#{1,6}\s*(?:[^\n]*\b(?:troubleshooting|faq)\b)", text), (
            f"{name} has no Troubleshooting/FAQ heading"
        )
        assert re.search(r"(?mi)^#{1,6}\s*(?:diagnostics|diagnose)\b", text), (
            f"{name} has no Diagnostics/Diagnose section heading"
        )
