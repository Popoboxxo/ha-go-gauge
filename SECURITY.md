# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 2.x     | Yes       |
| < 2.0   | No        |

Only the latest 2.x release receives security fixes. Older versions share the same
code paths but are no longer maintained — please reproduce an issue on the latest
release before reporting it.

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Report it privately through GitHub Security Advisories:

<https://github.com/Popoboxxo/ha-go-gauge/security/advisories/new>

That channel keeps the report confidential until a fix is available and lets us
coordinate disclosure with you.

> Maintainer note: private vulnerability reporting has to be enabled under
> *Settings -> Code security* for this link to accept external reports.

### What to include

- The affected Go Gauge HA version (`manifest.json` version, as shown in HACS)
- Your Home Assistant version (for example 2026.8.0)
- A description of the issue and its impact
- Reproduction steps, or a minimal proof of concept
- Relevant log lines, with tokens and account identifiers removed

Expect to be asked for a redacted diagnostics export if it helps narrow the issue
down. Never send an unredacted one.

## Tokens and diagnostics

- The OpenCode API token is stored in the Home Assistant config entry data
  (`.storage/core.config_entries`). It is never written to entity attributes, never
  logged by the integration, and never part of the repository.
- The diagnostics export passes config-entry data and options through
  `async_redact_data` with a fixed redaction key set (`token`, `tokens`,
  `authorization`, `workspace_name`). A workspace entry additionally carries an
  8-character SHA-256 **fingerprint** of the token — a non-reversible identifier that
  cannot be used to authenticate.
- If you believe a token leaked, revoke it in your OpenCode workspace and re-run the
  integration's re-authentication flow. Revocation is the only reliable remediation;
  rotating the token invalidates anything that was copied.
- Do not paste tokens, `.storage` files, full Home Assistant backups or unredacted
  diagnostics into issues, discussions, pull requests or chat logs.

## Response expectations

This is a maintainer-run open-source project, so responses are best effort:

- **Acknowledgement** of a report: usually within about 7 days.
- **Assessment**: after triage we confirm severity and whether the report is in scope.
- **Fix timeline**: depends on severity and complexity — critical issues are
  prioritised, low-severity issues may be batched into the next regular release.
- **Coordinated disclosure**: we agree on a disclosure date with you before details
  become public, and credit you in the advisory unless you prefer otherwise.

## Scope

In scope:

- The integration code in `custom_components/go_gauge/` (token handling, request
  construction, diagnostics redaction, config flow, entity state handling)
- The repository tooling in `scripts/` and `.github/workflows/`

Out of scope:

- Vulnerabilities in the upstream OpenCode API or the `opencode.ai` service itself —
  report those to the upstream project
- Vulnerabilities in Home Assistant Core
- Misconfiguration on your own instance, for example a token committed to your own
  configuration repository or an instance exposed without authentication
- Reports that require an already compromised host, browser extension or network
  attacker with the ability to intercept TLS
