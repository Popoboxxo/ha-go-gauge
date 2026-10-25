# Contributing to Go Gauge HA

Thanks for your interest in improving Go Gauge HA. This document describes how the
repository is set up, which checks a change has to pass, and how releases are made.

Go Gauge HA is a Home Assistant custom integration distributed as a **HACS custom
repository**. It talks directly to the OpenCode Go API (`opencode.ai`) and has no
runtime dependency besides Home Assistant itself.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Prerequisites

- Python **3.12+** (CI runs 3.13; `pyproject.toml` targets `py312` because the minimum
  supported Home Assistant version is 2026.8.0)
- Git, and the ability to clone with submodules (`external/ha-gauge-standard`)
- No Home Assistant installation is required — see below

## Development setup

Do **not** install `homeassistant` into the development environment. The unit tests
ship their own Home Assistant stubs (see
[How the Home Assistant stubs work](#how-the-home-assistant-stubs-work)), and a real
Home Assistant install would make the test run depend on unrelated dependency
versions instead of on this integration.

```bash
git clone --recurse-submodules https://github.com/Popoboxxo/ha-go-gauge.git
cd ha-go-gauge

python3 -m venv .venv
. .venv/bin/activate

pip install "pytest>=9,<10" "pytest-asyncio>=1,<2" "voluptuous>=0.14,<1" \
            "ruff>=0.16,<0.17" "mypy>=2,<3"
```

If you cloned without `--recurse-submodules`, fetch the canon submodule once:

```bash
git submodule update --init --recursive
```

`voluptuous` is a plain pip package with no Home Assistant dependency and is therefore
installed for real — it is never stubbed.

## How the Home Assistant stubs work

`tests/conftest.py` installs fake `homeassistant.*` modules into `sys.modules` before
any test module imports the integration. Each fake module is a permissive
`ModuleType` whose attribute access yields a fresh, subclassable empty class; that is
required because the integration subclasses Home Assistant base classes such as
`SensorEntity` or `DataUpdateCoordinator`, and a `MagicMock` instance cannot be used
as a base class.

Consequences for contributors:

- Every `homeassistant.*` submodule the integration imports must appear in the
  `_FAKE_HA_MODULES` list in `tests/conftest.py`. A new import that is missing there
  fails with `ModuleNotFoundError` at collection time — add it to the list.
- `pytest.ini` sets `addopts = -p no:homeassistant` so a globally installed
  `pytest-homeassistant-custom-component` plugin cannot take over the run.
- Individual test files customise specific fake attributes for their own scenario
  (for example `CoordinatorEntity.available` or enum-like `SensorDeviceClass`
  members); that per-scenario setup intentionally stays in the test file, not in
  `conftest.py`.

## Running the checks

Run the same gates CI runs (`.github/workflows/validate.yml`):

```bash
pytest -q
ruff check .
mypy
python3 scripts/check_json_consistency.py
python3 external/ha-gauge-standard/scripts/check_canon.py .
```

- `pytest -q` runs the whole unit suite. It is fast and needs no Home Assistant
  install.
- `ruff check .` uses the `pyproject.toml` configuration (line length 100, rules
  `E, F, I, W`, target `py312`). `external/` and `.agent-meta` are excluded.
- `mypy` is config-driven (`files = ["custom_components/go_gauge"]`,
  `ignore_missing_imports = true`) and must be run without arguments.
- `scripts/check_json_consistency.py` validates `manifest.json`, `hacs.json`,
  `strings.json` and every `translations/*.json`, and enforces recursive key parity
  between `strings.json` and each translation file.
- `external/ha-gauge-standard/scripts/check_canon.py .` checks the entity surface
  against the Gauge entity canon. It must exit 0 with **0 errors / 0 warnings**.

Optional local sanity check (not a separate CI job):

```bash
python3 -m compileall -q custom_components tests
```

The `release-check` workflow additionally verifies that the release tag matches
`manifest.json` version. You do not need to run
`scripts/check_version_sync.py` locally unless you are preparing a release.

## Code style

- PEP 8, enforced by `ruff` (line length 100).
- Type hints everywhere; `mypy` covers `custom_components/go_gauge`.
- Docstrings for every public class and function.
- Everything the integration does with Home Assistant is `async` — no blocking I/O in
  the event loop, no synchronous HTTP calls.
- `snake_case` for modules and functions, `PascalCase` for classes.
- Avoid positional arguments where two arguments of the same type could be swapped;
  pass them as keywords instead.

## Branch and commit conventions

- Never commit or push directly to `main`. Use a feature branch:
  `feat/<topic>`, `fix/<topic>`, `chore/<topic>` or `docs/<topic>`.
- Commits follow [Conventional Commits](https://www.conventionalcommits.org/):
  `<type>: <description>`, with `feat`, `fix`, `docs`, `chore`, `refactor`, `test` or
  `ci` as type.
- Messages are written in **English**, in **imperative mood**, and the subject line is
  at most **72 characters**.
- Keep commits focused; one logical change per commit.

A pull request is expected to pass every gate above before review. The PR template
lists them as a checklist.

## Documentation and translations

- `README.md` is the primary user-facing documentation (English). `README.de.md` is its
  German translation. Whenever user-visible behaviour, entities or options change, both
  files are updated in the same pull request, including the language switcher line at
  the top of each file.
- Translated counterparts that exist next to a document must be updated in the same
  pull request — for example `docs/dashboards/README.md` (German) and
  `docs/dashboards/README.en.md` (English).
- `custom_components/go_gauge/strings.json` is the **master** for all user-facing
  strings. `translations/de.json` and `translations/en.json` are derived from it and
  must keep exactly the same key paths. `scripts/check_json_consistency.py` fails the
  build on any missing or extra key.
- Add a `CHANGELOG.md` entry for every user-visible change.

## Release process

Releases are cut by the maintainer:

1. Bump `"version"` in `custom_components/go_gauge/manifest.json`. The version is
   bare SemVer **without** the `v` prefix, e.g. `2.0.2`.
2. Only bump the version together with a registered migration handler if the change
   requires it (`async_migrate_entry`). Home Assistant raises
   `Migration handler not found` when the entry version moves without one.
3. Commit, then create the tag `vX.Y.Z` (tag and manifest must match character for
   character apart from the leading `v`; `v2.0.2` ↔ `"version": "2.0.2"`).
4. Publish a **real GitHub release** for that tag. HACS distributes only published
   releases — a tag alone is not enough, and HACS would keep offering the old version.
5. The `release-check` workflow verifies tag/version sync on every published release.
   Beta releases use `vX.Y.ZbN` and must be flagged as pre-release on GitHub.

Home Assistant reports the `manifest.json` version, HACS the release tag — keeping them
in sync is mandatory, otherwise updates are not detected.

## Issues and pull requests

- Bug reports and feature requests use the issue forms in `.github/ISSUE_TEMPLATE/`.
  Blank issues are disabled; use GitHub Discussions for questions.
- Never paste API tokens, `.storage` contents or unredacted diagnostics into an issue.
  The diagnostics export redacts the token, but check the output once before posting.
- Security problems do **not** belong in a public issue — see [SECURITY.md](SECURITY.md).
- Keep pull requests small and focused; describe the motivation in the Summary section
  and fill in the checklist.
- Breaking changes need a dedicated section in the PR description with a migration
  note for existing installations.

## License

By contributing you agree that your contribution is licensed under the MIT license of
this repository (see [LICENSE](LICENSE)).
