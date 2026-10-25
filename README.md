[English](README.md) | [Deutsch](README.de.md)

# Go Gauge HA

**The gauge for your OpenCode Go budget — usage, limits & cost ratio right on your dashboard.**

[![Release](https://img.shields.io/github/v/release/Popoboxxo/ha-go-gauge?sort=semver&display_name=tag)](https://github.com/Popoboxxo/ha-go-gauge/releases) [![Validate](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/validate.yml) [![Release Check](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/release-check.yml/badge.svg?branch=main)](https://github.com/Popoboxxo/ha-go-gauge/actions/workflows/release-check.yml) [![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/use/repositories/) [![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8%2B-41BDF5.svg)](https://www.home-assistant.io/) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Open in HACS/Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Popoboxxo&repository=ha-go-gauge&category=integration)

A standalone [HACS](https://hacs.xyz) custom integration that talks **directly** to the
OpenCode Go API (`opencode.ai`) — no monitor, no dashboard, no extra component. Just your
workspace API token.

## Status

- This repository is a **HACS custom repository** and deliberately **not** listed in the
  HACS default store/catalog. Installation goes through a custom HACS repository (see
  [Installation](#installation)).
- Minimum versions: Home Assistant **2026.8.0**, HACS **2.x**.
- From Home Assistant **2026.11** on, the same My-Link (`hacs_repository`) opens the
  Marketplace; the link works before and after 2026.11.

## Features

- 🏷️ **One workspace instance per workspace** — a descriptive name plus one token;
  multiple workspaces mean multiple integration instances (see
  [Multiple workspaces](#multiple-workspaces))
- 🧾 **Exactly one account instance** — created automatically with the first workspace
  (`Go Gauge Konto`, `unique_id = go_gauge_account`), **never manually**: model catalog,
  reachability, the global account settings (catalog auto-refresh + interval) and a
  dedicated catalog refresh. Account data is workspace-independent and keeps running even
  when the last workspace is removed.
- 📈 **Percentage usage** per window (5h rolling / Weekly / Monthly)
- ⏰ **Concrete reset times** as timestamp entities — automation-friendly
- 🔮 **Forecast per window**: linear projection of current usage to the end of the window
  (can exceed 100 % — shows whether the pace will blow the budget)
- 🚦 **Pace traffic light per window**: `green`/`yellow`/`red` based on the forecast,
  thresholds adjustable via **Warning Threshold** (green/yellow) and **Pace Red Limit**
  (yellow/red, default 100 %) — both are number entities, adjustable live
- 🚦 **Rate-limit warning** as a binary sensor per window plus **subscription status** as
  its own binary sensor (403 "EntitlementError" = no active subscription, clearly
  distinguished from a rate limit/network error)
- 📦 **Model catalog as ONE sensor** (`sensor.go_gauge_models` on the account device):
  the complete, dynamic directory in the attributes (`catalog_json`, `ranking_by_cost`,
  …) — new models appear automatically without new entities.
- 🏆 Compact sensors (account device): Live Models · Cheapest Model · Free Models
- 🛰️ **Evidence-based reachability** — `api_reachable` (account device) is ON with at
  least one fresh success, OFF only with exclusively fresh failures, and `unknown` when
  the API answers but proves no success (e.g. 401 or no subscription). "Fresh" means
  within the TTL: catalog 2× account interval (fallback 120 min with auto-refresh off),
  usage 3× workspace interval.
- 🔘 **Two refresh buttons**: the workspace button and the account button share the
  translation key `refresh` and are distinguished only by their device — the workspace
  button loads **usage only**, the button on the "Go Gauge Konto" device loads **the model
  catalog only**. Both are independent of the auto cycles.
- ⚙️ **Separate auto-update cycles**, switchable live via dedicated switch/number entities
  (no restart/reload required) **or** via the options:

  | Cycle | Level | On/Off | Default |
  |-------|-------|--------|---------|
  | Usage | Workspace | ✅ on | every **10 min** |
  | Model catalog | Account (global) | ✅ on | every **60 min** (adjustable 1–1440) |

  Both can be disabled; then only the respective refresh button updates.
- 🔔 **Warning Threshold** (default 80 %) as a number entity — adjustable live, e.g. for
  your own automations above the built-in percentage sensor
- 🛟 **Offline-friendly setup**: the token is verified live when saving, but on network
  problems you can skip the check and still save. If the very first fetch fails at HA
  startup, setup does not abort — HA retries automatically in the background.
- 🩺 **Deep diagnostics**: the diagnostics export immediately shows cause and recommended
  action (e.g. "no active subscription" vs. "Cloudflare rate limit") without follow-up
  questions in chat.

## Screenshot

<img src="docs/images/go-gauge-konto-device.png" alt="Go Gauge Konto device" width="720">

The "Go Gauge Konto" device page in Home Assistant — the UI is shown in German.

## Compatibility

| Component | Version |
|---|---|
| Home Assistant | **2026.8.0** or newer |
| HACS | **2.x** |
| Marketplace | from Home Assistant **2026.11** via the My-Link (`hacs_repository`) — the link works before and after 2026.11 |

## Installation

### My-Link

The badge above ([Open in HACS/Marketplace](https://my.home-assistant.io/redirect/hacs_repository/?owner=Popoboxxo&repository=ha-go-gauge&category=integration))
opens the repository directly in HACS (before 2026.11) or in the Marketplace (from
2026.11).

### Manual as a custom repository

1. HACS → Integrations → ⋮ → *Custom repositories*
2. `https://github.com/Popoboxxo/ha-go-gauge` · Category: `Integration`
3. Install → restart Home Assistant

### Manual (fallback)

Copy the `custom_components/go_gauge` folder to `/config/custom_components/go_gauge` and
restart Home Assistant.

## Setup

1. *Settings → Devices & Services → Add integration* → **Go Gauge HA**
2. Assign a descriptive **name** for this workspace (e.g. `Team`, `Private`) and paste the
   OpenCode Go API **token** (keys: OpenCode UI → Workspace → Go → API Keys)
3. Options (gear icon on the device) **or** the device's switch/number entities: adjust the
   usage cycle and warning threshold as you like. The global account settings (catalog
   auto-refresh + interval) live on the account device.

> **Cloudflare note:** opencode.ai blocks non-browser agents (HTTP 403 Error 1010). The
> integration sends full browser headers with every request — built in, nothing to
> configure.

### Multiple workspaces

Each workspace instance manages exactly **one** workspace (one name + one token). For
additional workspaces, simply add the integration again with its own name and token.

In addition, the integration **automatically creates exactly one account instance**
(`Go Gauge Konto`, `unique_id = go_gauge_account`) — on the first workspace setup or on
the next Home Assistant start. It is **not** reachable via "Add integration" and must
**never** be created manually (it is produced by an import flow). Model catalog,
reachability and the global account settings are workspace-independent; they keep running
with their own account settings even when the last workspace is removed.

### Devices

One device "Go Gauge {workspace name}" per workspace instance (usage, forecast, pace, reset
times, warning threshold/pace red limit and usage refresh interval). The
workspace-independent entities (Model Catalog, Live Models, Cheapest Model, Free Models,
API Reachable) as well as the catalog refresh button and the global account settings
(Auto Update Models, models refresh interval, account options flow) live on their own
device **"Go Gauge Konto"**. That device belongs to the account entry, not to a workspace;
it exists exactly once per Home Assistant instance.

### Delete/disable the account entry

The account entry is its own integration instance and is managed automatically:

- **Delete** (Devices & Services → Go Gauge Konto → Delete): entities and the account
  device disappear. The entry is re-created automatically on the **next** workspace setup
  (reload, options change or HA restart). If an `entity_id` was previously deduplicated
  (`…_2`), the newly created entity may get a different `entity_id` — history stays in the
  recorder database, the binding then runs via the new `entity_id`.
- **Disable**: the account entities remain as registry entries and become `unavailable`;
  there is **no** automatic re-create and **no** re-enable. After re-enabling (reload the
  integration or restart HA) setup runs as usual.
- Both affect **only** the account: workspaces, their entities and the usage fetch keep
  running unchanged.

## Settings: global vs. per workspace

- **Global (account device, exactly once per HA instance):**
  - `switch.go_gauge_konto_auto_update_models` — catalog auto-refresh, default **ON**
  - `number.go_gauge_konto_models_refresh_min` — catalog interval **1–1440 min**,
    default **60**
  - additionally reachable via the account options flow (gear icon on the account device)
- **Per workspace (workspace device):**
  - `number.go_gauge_warning_threshold_<workspace>` — warning threshold in %, default
    **80** (also the green/yellow boundary of the pace traffic light)
  - `number.go_gauge_pace_red_limit_<workspace>` — pace red limit in %, default **100**
  - `number.go_gauge_usage_refresh_min_<workspace>` — usage refresh interval in minutes,
    default **10**
  - `switch.go_gauge_<workspace>_auto_update_usage` — auto-refresh usage on/off

The account settings affect all workspaces together; warning threshold, pace red limit and
usage interval apply only to their own workspace.

## Dashboards

Ready-made dashboard templates for your workspace live in
[docs/dashboards/README.en.md](docs/dashboards/README.en.md) (German version:
[docs/dashboards/README.md](docs/dashboards/README.md)).

- **Standard** ([`workspace-standard.yaml`](docs/dashboards/workspace-standard.yaml)) —
  Home Assistant built-ins only, no additional integration required
- **Mushroom** ([`workspace-mushroom.yaml`](docs/dashboards/workspace-mushroom.yaml)) —
  compact variant, requires the HACS frontend resource **Mushroom**
- **Matrix** ([`workspace-matrix.yaml`](docs/dashboards/workspace-matrix.yaml)) — several
  workspaces side by side in a responsive grid; requires the HACS resources
  decluttering-card-plus, layout-card, Mushroom and vertical-stack-in-card

All templates show usage, pace, forecast, remaining/time to reset, controls and the model
catalog.

## Entities (example: workspace "Team")

Entity names contain the workspace name assigned during setup — the actual `entity_id`
suffixes depend on it (Home Assistant slugifies the name automatically, e.g. umlauts →
base vowel). Account entities are named after the account device instead; on upgrade,
existing installations keep their previous `entity_id`. The `unique_id` is authoritative
(account entities canonically `account_*`).

| Entity | Content |
|--------|---------|
| `sensor.go_gauge_team_5h_rolling_usage` | 0 (%) — attributes: status, note, resets_at_iso |
| `sensor.go_gauge_team_weekly_usage` | 73 (%) |
| `sensor.go_gauge_team_monthly_usage` | 0 (%) — without subscription `unknown`, cause in `status`/`note` |
| `sensor.go_gauge_team_monthly_reset` | `2026-09-17T08:38:28+00:00` (timestamp) |
| `sensor.go_gauge_team_weekly_forecast` | e.g. 92.4 (%) — projection to the end of the window, can exceed 100 |
| `sensor.go_gauge_team_weekly_pace` | `green` / `yellow` / `red` — attributes: forecast_percent, green_below, red_above |
| `sensor.go_gauge_team_weekly_remaining` | 27 (%) — remaining budget = 100 − usage |
| `sensor.go_gauge_team_weekly_time_to_reset` | e.g. `62.5` (h) — time until the window reset |
| `sensor.go_gauge_team_weekly_burn_rate` | e.g. `4.2` (%/h) — consumption rate over the last 2 h |
| `binary_sensor.go_gauge_team_weekly_rate_limited` | ON = rate-limited for this window |
| `binary_sensor.go_gauge_team_subscription_active` | ON = active subscription, OFF + `note` = no subscription |
| `sensor.go_gauge_team_api_status` | `ok` / `no_subscription` / `rate_limited` / `auth_error` / `api_error` / `unknown` — **names the cause** when no data arrives. Attributes: `raw_status`, `note` (raw cause incl. URL/status code) |
| `button.go_gauge_refresh` | Workspace refresh — loads **usage only** (unique_id `<entry_id>_refresh`) |
| `number.go_gauge_warning_threshold_team` | Warning threshold in % (default 80) — also the green/yellow boundary of the pace traffic light |
| `number.go_gauge_pace_red_limit_team` | Pace red limit of the pace traffic light in % (default 100) |
| `number.go_gauge_usage_refresh_min_team` | Usage refresh interval in minutes |
| `switch.go_gauge_team_auto_update_usage` | Auto-refresh usage on/off |
| `sensor.go_gauge_models` | **Account device** — 29; attributes: `catalog_json`, `ranking_by_cost`, `account_auto_update_models`, `account_fetch_interval_minutes`, `account_models_refresh_minutes`, `account_last_models_outcome` (unique_id `account_model_catalog`) |
| `sensor.go_gauge_live_models` | **Account device** — 29 (unique_id `account_models_live_count`) |
| `sensor.go_gauge_cheapest_model` | **Account device** — e.g. `muse-spark-1.2-contributor` (unique_id `account_cheapest_model`) |
| `sensor.go_gauge_free_models` | **Account device** — `ox-alpha-free` (unique_id `account_free_models`) |
| `binary_sensor.go_gauge_api_reachable` | **Account device** — evidence/TTL-based (ON only with a fresh success, `unknown` without proof of success). Attributes: `reachability_reason`, `evidence`, `models_ttl_s`, `usage_ttl_s`, `checked_at` (unique_id `account_api_reachable`) |
| `button.go_gauge_konto_refresh` | **Account device** — loads **the model catalog only** (translation key `refresh`, `unique_id` `account_refresh`) |
| `switch.go_gauge_konto_auto_update_models` | **Account device** — global catalog auto-refresh, default **ON** (unique_id `account_auto_update_models`) |
| `number.go_gauge_konto_models_refresh_min` | **Account device** — catalog interval **1–1440 min**, default **60** (unique_id `account_models_refresh_minutes`) |

The concrete `entity_id`s of the account entities are illustrative (device prefix and
system language may differ); the `unique_id` is authoritative. Both refresh buttons use the
translation key `refresh` — the mapping happens via the device ("Go Gauge Konto" vs.
"Go Gauge <workspace>"), not via the key. Existing installations keep their `entity_id`
even after upgrading to 2.0.0.

### Upgrading from 1.x

Just update — `entity_id`, history, area and labels of the five account entities are
preserved (registry adoption; the `unique_id` is normalized to `account_*` once). Between
the first start and the platform forward these entities are briefly `unavailable` —
expected, not an error. Please check the global account settings once (default:
auto-refresh **ON**, catalog interval **60 min**); the former workspace model controls
(`auto_update_models`, `models_refresh_minutes`) are removed without replacement. Home
Assistant **2026.8.0** or newer is required.

## Example automation

```yaml
automation:
  - alias: "Go budget warning"
    trigger:
      - platform: numeric_state
        entity_id: sensor.go_gauge_team_monthly_usage
        above: 80
    action:
      - service: notify.persistent_notification
        data:
          message: "Team workspace above 80% of the monthly budget!"
```

## Troubleshooting / FAQ

**HTTP 403 / Cloudflare Error 1010**
opencode.ai blocks non-browser agents. The integration sends full browser headers with
every request — built in, nothing to configure. If the error still occurs, a proxy/CDN in
between is usually the cause.

**`no_subscription`**
The token is valid, but there is no active Go subscription (403 "EntitlementError"). The
subscription binary sensor is then OFF, the percentage sensor shows `unknown`; the cause is
in `status`/`note`.

**`rate_limited`**
The API is reachable, but the budget of the affected window is used up. The matching
`…_rate_limited` binary sensor is ON; after the window reset it works again.

**`auth_error`**
The token is invalid or expired (401). Generate a new token in the OpenCode UI and
reconfigure the integration.

**Entities briefly `unavailable` after the upgrade**
Between the first start and the platform forward the account entities are briefly
`unavailable` — expected, not an error. A restart or reload does not trigger anything
additional; setup completes on its own.

**Account entry deleted**
The account entry is re-created automatically on the next workspace setup (reload, options
change or HA restart). The workspace entities and the usage fetch keep running unchanged.

## Diagnostics & debug logging

Diagnostics export: *Settings → Devices & Services → Go Gauge HA → Download diagnostics*.
The export is split by entry kind (workspace vs. account entry) and contains the
coordinator state, the workspace status list, the reachability matrix and the last adoption
report.

- The token is removed via `async_redact_data`; only a masked fingerprint (first 8
  characters of a SHA-256 hash) remains for correlation.
- The account entry contains no token at all, hence no fingerprint either.

Debug logging for the integration (`configuration.yaml`, then restart HA):

```yaml
logger:
  default: warning
  logs:
    custom_components.go_gauge: debug
```

## Support

- Questions and ideas: [GitHub Discussions](https://github.com/Popoboxxo/ha-go-gauge/discussions)
- Bug reports: [GitHub Issues](https://github.com/Popoboxxo/ha-go-gauge/issues) — please
  include the diagnostics export (without token, see above)

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Security

The token is stored exclusively in the Home Assistant config entry storage (`.storage`) and
is never logged. Please do **not** report security vulnerabilities through public issues,
but via the repository's
[GitHub Security Advisories](https://github.com/Popoboxxo/ha-go-gauge/security/advisories);
details are in [SECURITY.md](SECURITY.md).

## License

MIT © Popoboxxo
