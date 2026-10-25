[English](README.en.md) | [Deutsch](README.md)

# Go Gauge — Dashboards

Ready-made dashboard templates (YAML) for displaying usage, limits, pace and the
model catalog of a Go Gauge workspace directly in Home Assistant. All templates
are plain YAML files with no Python dependency; the integration itself is not
modified by them.

## 1. Overview

| File | Layout | Custom resources |
|---|---|---|
| [`workspace-standard.yaml`](workspace-standard.yaml) | Home Assistant built-ins only (markdown, grid, gauge, entities, history-graph) | none |
| [`workspace-mushroom.yaml`](workspace-mushroom.yaml) | Mushroom cards (`custom:mushroom-*`) | Mushroom (via HACS) |
| [`workspace-matrix.yaml`](workspace-matrix.yaml) | Multi-workspace matrix: one `decluttering-card-plus` tile per workspace in a `layout-card` grid, tiles in Mushroom look | decluttering-card-plus, layout-card, Mushroom, vertical-stack-in-card |
| [`entity-map.yaml`](entity-map.yaml) | machine-readable entity/binding map | none |

The Standard and Mushroom templates provide the same scope of information
(the matrix template shows the same window and control values per workspace
as a tile; the **usage history** is deliberately omitted there, the
**model catalog** is present as an account panel):

- header row with subscription status, API status and status/grade per window,
- per window (5h rolling / Weekly / Monthly) usage, pace, forecast,
  remaining budget, time remaining, burn rate and reset or rate limit,
- a usage history,
- a controls section (warning threshold, traffic-light red limit,
  usage refresh interval, auto-update usage, refresh (usage)),
- a catalog/account section (Models, Live Models, Cheapest Model,
  Free Models, API Reachable, global account settings, catalog refresh).

**Note on screenshots:** The example dashboards are available as YAML only.
Screenshots are deliberately not included because `entity_id`s and layouts depend
on the installation (workspace slug, device prefix, system language) — a
screenshot would suggest a binding that looks different in every installation.
The templates and [`entity-map.yaml`](entity-map.yaml) are authoritative.

## 2. Prerequisites

### Standard (`workspace-standard.yaml`)

- No additional integration and no custom resources required.
- Home Assistant **>= 2026.8.0** (minimum version of the integration as of v2.0.0:
  account entry architecture, `unique_id` normalization `account_*`).
- The cards used — `grid`, `gauge`, `entities`, `markdown` and
  `history-graph` — are built-ins and available in this version.

### Mushroom (`workspace-mushroom.yaml`)

- Install HACS (if not already present).
- In HACS, install the frontend integration **Mushroom**
  (HACS → Frontend → Mushroom), then reload Home Assistant.
- Mushroom provides the `custom:mushroom-*` resources. `card-mod` is
  explicitly **not** required.

### Matrix (`workspace-matrix.yaml`)

- Install HACS (if not already present) and install **all four** resources
  in the HACS frontend, then reload Home Assistant:
  - **decluttering-card-plus** (`custom:decluttering-card-plus`) — tile template
    `go_gauge_workspace`. Not in the HACS default catalog, so add it as a HACS
    **custom repository** (type "Dashboard", URL
    `https://github.com/tempus2016/decluttering-card-plus`) —
    alternatively, include the `decluttering-card-plus.js` resource manually,
  - **layout-card** (`custom:layout-card`) — responsive grid layout,
  - **Mushroom** (`custom:mushroom-*`) — cards of the tile,
  - **vertical-stack-in-card** (`custom:vertical-stack-in-card`) — tile container.
- Home Assistant **>= 2026.8.0** (badges as of 2024.8). The card optionally also
  registers the legacy type `custom:decluttering-card`; the template uses
  exclusively `custom:decluttering-card-plus`.
- `card-mod` is explicitly **not** required.

### Optional / advanced (NOT required)

These resources are not needed for the templates but can improve convenience:

| Resource | Benefit |
|---|---|
| `auto-entities` | Generate lists (e.g. window or model rows) automatically from filter rules instead of maintaining each entity individually. |
| `card-mod` | Finer visual adjustments (colors, spacing). Not required for the bundled templates. |
| `config-template-card` | Resolve entity lists dynamically in templates. Not required for the bundled templates. |

## 3. Installation

1. In Home Assistant, create a new dashboard or open an existing one.
2. Switch the dashboard to the **raw configuration editor**.
3. Paste the content of the desired template (`workspace-standard.yaml`,
   `workspace-mushroom.yaml` or `workspace-matrix.yaml`).
4. Replace the placeholder `WS` with the real workspace slug
   (see sections 4 and 5).
5. Save.

## 4. Placeholder and binding concept

**The `entity_id` is created once, at first registration.** Home Assistant derives it at
that moment from the then-localized name plus the device name and stores it in the entity
registry. Afterwards HA does **not** change it automatically — neither on a later rename
of the display name nor on a language switch. It can therefore differ between
installations (system language, device name, history). The stable identity is the
`unique_id`; manually renaming the `entity_id` is possible but breaks automations and
dashboards. See
[Entity names and entity IDs](../../README.md#entity-names-and-entity-ids) in the main
README.

**What is stable is the `unique_id` suffix.** Every entity carries a `unique_id`
following the pattern `{entry_id}_{workspace_key}_{window}{suffix}`
(window-specific) or `{entry_id}_{workspace_key}{suffix}` / `{entry_id}{suffix}`
(workspace level). Account entities, by contrast, carry the canonical form
`account_<kind>` (e.g. `account_model_catalog`, `account_api_reachable`); the
account settings are named `account_auto_update_models`,
`account_models_refresh_minutes` and `account_refresh`. These suffixes are the
source of truth; see
[`entity-map.yaml`](entity-map.yaml).

**The `WS` convention:** In the dashboard templates, `WS` is a placeholder
for the workspace slug *within the `entity_id`*. Before use, `WS` must be
replaced with the real slug. Never guess the slug from the friendly
name; instead determine the real `entity_id` via the `unique_id` suffix from the
entity registry (section 5 b).

Windows:

| `key` | `label` (display) | `entity_id_part` | `unique_id` window |
|---|---|---|---|
| `5h` | `5h rolling` | `5h_rolling` | `5h` |
| `week` | `Weekly` | `weekly` | `week` |
| `month` | `Monthly` | `monthly` | `month` |

## 5. AI guide (binding a dashboard to a workspace)

Step sequence for an AI or a human reliably binding a dashboard
to a real Go Gauge instance:

**a) Read the source of truth in the repo.** Which entity has which `unique_id`
suffix, name and attributes is documented in the integration code:

- `custom_components/go_gauge/sensor.py:105-165` (`usage_percent`)
- `custom_components/go_gauge/sensor.py:168-190` (`reset`)
- `custom_components/go_gauge/sensor.py:193-219` (`forecast`)
- `custom_components/go_gauge/sensor.py:222-259` (`pace`)
- `custom_components/go_gauge/sensor.py:262-284` (`remaining`)
- `custom_components/go_gauge/sensor.py:287-306` (`time_to_reset`)
- `custom_components/go_gauge/sensor.py:309-330` (`burn_rate`)
- `custom_components/go_gauge/binary_sensor.py:63-96` (`limited`)
- `custom_components/go_gauge/binary_sensor.py:99-136` (`subscription_active`)
- `custom_components/go_gauge/number.py:102-169`
  (`warn_percent`, `pace_red_percent`, `usage_refresh_minutes`)
- `custom_components/go_gauge/switch.py:56-83` (`auto_update_usage`);
  account: `switch.py:85-116` (`auto_update_models`)
- `custom_components/go_gauge/button.py:49-68` (`refresh`, usage only);
  account: `button.py:71-83` (`refresh`, catalog — same translation key,
  distinguished by the account device)
- `custom_components/go_gauge/entity.py` (device info: workspace and
  account device), `account.py` (account entry, reachability, settings)

The full list including the account entities is in
[`entity-map.yaml`](entity-map.yaml).

**b) Query the entity registry at runtime.** Via WebSocket
(`config/entity_registry/list`) or REST (`/api/states`), read the entities of
the Go Gauge config entries and match them **via the `unique_id` suffix**
(table in `entity-map.yaml`). Never guess `entity_id`s from the friendly name.

**c) Determine workspace and account.** A workspace belongs to the device
`Go Gauge {ws_name}` with identifier `go_gauge`/`<entry_id>`. The account device
has the identifier `go_gauge`/`account` and exists exactly once: it belongs to
the automatically created **account entry** (`unique_id` = `go_gauge_account`, title
`Go Gauge Konto` — "Go Gauge Account"), not to a workspace. Its entities (Models, Live
Models, Cheapest Model, Free Models, API Reachable) as well as the catalog refresh
button and the global account settings therefore exist exactly once. Never create
the account entry manually — it is created during the first workspace setup or HA
start and is recreated in the same way after deletion.

**d) Substitution.** Replace `WS` with the real workspace slug. Alternatively,
resolve it dynamically in Jinja templates by searching for the matching sensor
via the `workspace_key` and `window` attributes of the usage sensors
(e.g. with a `states.sensor` loop) — that is more robust against renamed
workspaces.

**e) Read the catalog.** Model information is available **only** via the attributes
of the catalog sensor (unique_id `account_model_catalog`; `catalog_json`,
`ranking_by_cost`, `free_models`, `cheapest_model`, `cheapest_overall`, `count`,
`live_count`, `models_updated_at`) plus the global account settings
(`account_auto_update_models`, `account_fetch_interval_minutes`,
`account_models_refresh_minutes`, `account_last_models_outcome`). There are
**no** individual entities per model. Reachability
(unique_id `account_api_reachable`) provides `reachability_reason`, `evidence`,
`models_ttl_s`, `usage_ttl_s` and `checked_at` as attributes; its state is
three-valued (ON / OFF / unknown).

## 6. `workspace-matrix.yaml` — variables & binding

`workspace-matrix.yaml` shows **multiple workspaces side by side**: a
`custom:decluttering-card-plus` template (`go_gauge_workspace`) is instantiated
once per workspace, the grid comes from `custom:layout-card`.

### Required custom resources

Install all four via HACS (decluttering-card-plus as a **custom
repository**, type "Dashboard", see section 2), then reload Home Assistant:

| Resource | Purpose |
|---|---|
| `decluttering-card-plus` (`custom:decluttering-card-plus`) | Tile template `go_gauge_workspace` incl. `[[variables]]` |
| `layout-card` | responsive grid (`grid-template-columns`) |
| Mushroom | cards of the tile (`custom:mushroom-*`) |
| `vertical-stack-in-card` | container that groups the cards of a tile |

### `[[...]]` variables

The tile is parameterized via seven variables. They are bound per
`custom:decluttering-card-plus` in the `variables:` block:

| Variable | Meaning | Example |
|---|---|---|
| `[[ws_name]]` | Display name of the tile (freely selectable) | `"App"` |
| `[[slug_a]]` | `entity_id` prefix for entities with a **trailing** fragment: `usage`, `reset`, `rate_limited`, `subscription_active`, `auto_update_usage` | `"go_gauge_ha_go_gauge_app"` |
| `[[slug_b]]` | `entity_id` prefix for entities with a **trailing** fragment: `forecast`, `pace`, `remaining`, `time_to_reset`, `burn_rate` | `"software_go_gauge_app"` |
| `[[num_slug]]` | `entity_id` prefix for `number` entities with a **leading** fragment: `<device>_go_gauge` | `"go_gauge_ha_go_gauge"` |
| `[[num_suffix]]` | Workspace suffix of the `number` entities | `"_app"` |
| `[[ampel_id]]` | **full** `entity_id` of the pace red limit number: `number.<device>_go_gauge_pace_red_limit_<workspace>` | `"number.software_go_gauge_app_go_gauge_pace_red_limit_app"` |
| `[[refresh_btn]]` | **full** `entity_id` of the **workspace** refresh button (loads usage only): `button.<...>_refresh`. The account refresh is a separate, device-prefixed button (see below). | `"button.go_gauge_ha_go_gauge_refresh_3"` |

In the tile this yields, for example, `sensor.[[slug_a]]_5h_rolling_usage`,
`sensor.[[slug_b]]_weekly_forecast` or
`number.[[num_slug]]_warning_threshold[[num_suffix]]`. Per window
(5h rolling / Weekly / Monthly) there is exactly **one** compact
`custom:mushroom-template-card`: `primary` shows usage with `%`,
`secondary` shows the values on multiple lines (`Prog`, `Pace`, `Rem`, `Reset`,
`Restzeit` — remaining time — in `h`, `Burn` in `%/h`; unknown/`unavailable`
states are rendered as `–`). The rate limit is **no longer** signaled via a separate chip,
but via icon (warning icon), red `icon_color` and the notice
"RATE-LIMITED" in `secondary` as soon as
`binary_sensor.[[slug_a]]_<window>_rate_limited` has the state `on`. `tap_action`
remains `more-info`.

The subscription/API status is split: the subscription status (`subscription_active`)
remains icon-only in the tile header, while **API reachability** (unique_id
`account_api_reachable`) is an account-wide header row **above** the grid
(see below). Below the grid is the account panel
"Modell-Katalog / Konto" ("Model Catalog / Account") with the catalog sensors
(`account_model_catalog`,
`account_models_live_count`, `account_cheapest_model`, `account_free_models`)
as well as the global account settings (`account_auto_update_models` switch,
`account_models_refresh_minutes` number) and the account refresh button
(`account_refresh`, loads **the catalog only**). These entities exist only
once because they belong to the account entry.

> **Note — account `entity_id`s are illustrative:** The account `entity_id`s
> mentioned here and in the global header row (`sensor.go_gauge_models`,
> `sensor.go_gauge_live_models`, `sensor.go_gauge_cheapest_model`,
> `sensor.go_gauge_free_models`, `binary_sensor.go_gauge_api_reachable`,
> `button.go_gauge_konto_refresh`) are **illustrative** and may differ depending on
> device prefix/system language; existing installations keep their
> `entity_id`. What is binding is the `unique_id` match from the entity registry:
> `account_model_catalog`, `account_models_live_count`, `account_cheapest_model`,
> `account_free_models`, `account_api_reachable`, `account_refresh`,
> `account_auto_update_models`, `account_models_refresh_minutes`
> (see [`entity-map.yaml`](entity-map.yaml)).

### Binding caveat

`slug_a` and `slug_b` can differ: after the v7 migration, entities that were
already registered carry a different device prefix than newly registered ones
(see [`E2E-v7-entity-id-migration-2026-09-12.md`](../E2E-v7-entity-id-migration-2026-09-12.md),
finding ND-1). Therefore **never guess the slugs from the friendly name**; instead
resolve the real `entity_id` via the `unique_id` suffix from the entity registry
([`entity-map.yaml`](entity-map.yaml), section 5 b). The
**workspace** refresh button `button.<...>_refresh` is named independently of the
workspace and collides when there are multiple workspaces (`..._2`, `..._3`, …) —
so fetch its real `entity_id` from the registry as well (unique_id suffix
`_refresh`). The **account** refresh button, by contrast, is device-prefixed
(`button.go_gauge_konto_refresh`, unique_id `account_refresh`) and is therefore
not affected by the workspace collision.

> **Slug language:** As of v1.5.0, the `entity_id` slugs are English
> (`*_usage`, `*_forecast`, `*_remaining`, `*_time_to_reset`, …;
> source: `custom_components/go_gauge/const.py`, `ENTITY_ID_MIGRATION`).
> German legacy slugs (`*_nutzung`, `*_prognose`, `*_restbudget`,
> `*_restzeit`) are **no longer** valid in dashboards.

## 7. FAQ / Known gotchas

- **Workspace button name collides with multiple workspaces.** Without a
  workspace name, the workspace refresh button is called `Go Gauge Refresh` and
  loads **usage only**; with multiple workspaces, Home Assistant changes its
  `entity_id` to `..._2` (and so on). Therefore always fetch the real `entity_id`
  via the `unique_id` suffix `<entry_id>_refresh` from the registry.
  The account refresh (`button.go_gauge_konto_refresh`, unique_id `account_refresh`,
  catalog) is **not** affected because it is attached to the account device.
- **Account settings are global.** `account_auto_update_models` (default ON) and
  `account_models_refresh_minutes` (1–1440 min, default 60) are located on the
  account device and apply to all workspaces. The former workspace controls
  `auto_update_models` / `models_refresh_minutes` no longer exist as of 2.0.0.
- **`_pace` state is an English string.** Possible values: `green`,
  `yellow`, `red` (not translated). The thresholds come from the
  number entities Warning Threshold and Pace Red Limit.
- **`_forecast` can be greater than 100.** The forecast extrapolates current
  usage linearly to the end of the window — exceeding 100 is intentional and
  shows that the pace is blowing the budget. The `gauge` card in the
  Standard template is still limited to `max: 100`; therefore display the forecast
  value additionally as an entity.
- **Without a subscription, usage sensors are `unknown` (not 0).** When no
  subscription is present, the percentage sensors deliberately return `unknown`,
  and the binary sensor `... Subscription Active` is `off`. Do not interpret
  `unknown` as 0.
- **Mushroom cards: "Entity not available".** With multiple workspaces,
  `custom:mushroom-*` cards resolve the entity via the `entity_id`; if it is
  missing, the card shows "Entity not available". In that case, enter the real
  `entity_id` (unique_id suffix match).
