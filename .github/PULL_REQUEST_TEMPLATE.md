## Summary

<!-- One or two sentences: what does this PR change, and why? -->

## Type of change

- [ ] Bugfix (non-breaking change that fixes an issue)
- [ ] Feature (non-breaking change that adds functionality)
- [ ] Refactor (no functional change)
- [ ] Documentation
- [ ] CI / chore
- [ ] Breaking change (existing behaviour, entity IDs or config entries change)

## Related issue

<!-- e.g. Fixes #123 -->

## Checklist

- [ ] Tests added or updated, and green: `pytest -q`
- [ ] Lint clean: `ruff check .`
- [ ] Types clean: `mypy`
- [ ] JSON meta-files and translation key parity: `python3 scripts/check_json_consistency.py`
- [ ] Canon check passes with 0 errors / 0 warnings: `python3 external/ha-gauge-standard/scripts/check_canon.py .`
- [ ] Documentation updated where user-visible behaviour changed (`README.md` + `README.de.md`, `docs/dashboards/` incl. `README.en.md`, translations)
- [ ] `CHANGELOG.md` entry added under `Unreleased` / the target version
- [ ] No secrets, API tokens, `.storage` dumps or unredacted diagnostics in the diff

## Breaking changes

<!--
If you ticked "Breaking change" above, describe the migration path here:
what breaks for existing installations, which entities or unique_ids are affected
(unique_id must never change without a migration handler) and what users have to do.
Otherwise write "None".
-->

None

## Notes for reviewers

<!-- Optional: design decisions, follow-ups, known limitations. -->

---

### Conventions reminder

- No direct pushes to `main` — use a feature branch: `feat/`, `fix/`, `chore/` or `docs/`.
- Conventional Commits, English, imperative mood, max 72 characters in the subject line.
- `unique_id` values of existing entities are never changed; a manifest version bump
  needs a registered migration handler.
