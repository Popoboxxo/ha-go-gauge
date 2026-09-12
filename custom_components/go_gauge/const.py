"""Constants for Go Gauge HA."""
from __future__ import annotations

import hashlib
from typing import Any

DOMAIN = "go_gauge"


def token_unique_id(token: str) -> str:
    """Return the ConfigEntry ``unique_id`` derived from an API token.

    Uses a SHA-256 hash (first 16 hex chars) instead of a plaintext token
    fragment, so no part of the secret is ever persisted in HA storage or
    leaked via diagnostics / support exports (see AUDIT-2026-09-04).

    Single source of truth shared by ``config_flow`` (entry creation) and
    ``__init__`` (migration) so both derive identical IDs without duplicating
    the formula. The same token always yields the same id (HA duplicate
    detection); note this is case-sensitive, unlike the pre-v5 formula.
    """
    digest = hashlib.sha256(token.encode()).hexdigest()[:16]
    return f"{DOMAIN}_{digest}"

MANUFACTURER = "Popoboxxo"
MODEL = "OpenCode Go"

CONF_WARN_PERCENT = "warn_percent"
CONF_PACE_RED_PERCENT = "pace_red_percent"
CONF_WORKSPACE_NAME = "workspace_name"
CONF_AUTO_UPDATE_USAGE = "auto_update_usage"
CONF_USAGE_REFRESH_MINUTES = "usage_refresh_minutes"
CONF_AUTO_UPDATE_MODELS = "auto_update_models"
CONF_MODELS_REFRESH_MINUTES = "models_refresh_minutes"

DEFAULT_WARN_PERCENT = 80
DEFAULT_PACE_RED_PERCENT = 100
DEFAULT_SCAN_INTERVAL = 600  # seconds (legacy)
DEFAULT_USAGE_REFRESH_MINUTES = 10
DEFAULT_MODELS_REFRESH_MINUTES = 60

WINDOW_LABELS = {"5h": "5h rolling", "week": "Weekly", "month": "Monthly"}

# Nominal window length in seconds, used to project current pace onto the
# rest of the window. "month" is an approximation (30 days) - die
# opencode.ai-API liefert kein exaktes Kalendermonat-Fenster, nur resetsAt.
WINDOW_SECONDS = {"5h": 5 * 3600, "week": 7 * 24 * 3600, "month": 30 * 24 * 3600}

# --- Entity naming ---------------------------------------------------------
# Canonical English name fragments. They are the target values of the v6
# migration table below, which maps the legacy German fragments onto these very
# constants. Since v7 the display names come from ``translation_key`` lookups
# (strings.json / translations/*.json) instead of hardcoded ``_attr_name``
# values - these constants stay as the single source of truth for the name
# migration and keep v6 and v7 output aligned.
ENTITY_NAME_USAGE = "Usage"
ENTITY_NAME_MODELS = "Models"
ENTITY_NAME_AUTO_UPDATE = "Auto Update"
ENTITY_NAME_FORECAST = "Forecast"
ENTITY_NAME_REMAINING = "Remaining"
ENTITY_NAME_TIME_TO_RESET = "Time to Reset"
ENTITY_NAME_CHEAPEST_MODEL = "Cheapest Model"
ENTITY_NAME_LIVE_MODELS = "Live Models"
ENTITY_NAME_FREE_MODELS = "Free Models"
ENTITY_NAME_WARNING_THRESHOLD = "Warning Threshold"
ENTITY_NAME_PACE_RED_LIMIT = "Pace Red Limit"
ENTITY_NAME_SUBSCRIPTION_ACTIVE = "Subscription Active"
ENTITY_NAME_API_REACHABLE = "API Reachable"
ENTITY_NAME_REFRESH = "Refresh"

# v6: German -> English entity-name migration (see __init__.async_migrate_entry).
# Applied as ORDERED ``str.replace`` pairs; the order is semantically required -
# compound names ("Nutzung Auto-Update") must be replaced before the general
# fragment they contain ("Nutzung"), otherwise the compound would be corrupted.
ENTITY_NAME_MIGRATION: tuple[tuple[str, str], ...] = (
    ("Nutzung Auto-Update", f"{ENTITY_NAME_AUTO_UPDATE} {ENTITY_NAME_USAGE}"),
    ("Modelle Auto-Update", f"{ENTITY_NAME_AUTO_UPDATE} {ENTITY_NAME_MODELS}"),
    ("Nutzung Refresh (Minuten)", f"{ENTITY_NAME_USAGE} Refresh (min)"),
    ("Modelle Refresh (Minuten)", f"{ENTITY_NAME_MODELS} Refresh (min)"),
    ("Günstigstes Modell", ENTITY_NAME_CHEAPEST_MODEL),
    ("Live-Modelle", ENTITY_NAME_LIVE_MODELS),
    ("Free-Modelle", ENTITY_NAME_FREE_MODELS),
    ("Warnschwelle", ENTITY_NAME_WARNING_THRESHOLD),
    ("Ampel Rot-Grenze", ENTITY_NAME_PACE_RED_LIMIT),
    ("Abo aktiv", ENTITY_NAME_SUBSCRIPTION_ACTIVE),
    ("API erreichbar", ENTITY_NAME_API_REACHABLE),
    ("Restbudget", ENTITY_NAME_REMAINING),
    ("Restzeit", ENTITY_NAME_TIME_TO_RESET),
    ("Prognose", ENTITY_NAME_FORECAST),
    ("Nutzung", ENTITY_NAME_USAGE),
    ("Modelle", ENTITY_NAME_MODELS),
    ("Aktualisieren", ENTITY_NAME_REFRESH),
)


def migrate_entity_name(original_name: str | None) -> str | None:
    """Translate a legacy German entity name into the canonical English name.

    Returns ``None`` when there is nothing to migrate - ``None``/empty input or
    an already-English (unchanged) name - so callers can tell "changed" from
    "unchanged". Idempotent: feeding the migrated output back returns ``None``.
    """
    if not original_name:
        return None
    migrated = original_name
    for legacy, english in ENTITY_NAME_MIGRATION:
        migrated = migrated.replace(legacy, english)
    return migrated if migrated != original_name else None


# v7: German -> English entity_id slug migration (see
# __init__._async_migrate_entity_ids). Applied as ORDERED pairs to the
# entity-specific segment of the object_id (everything after the first dot) -
# never to the whole object_id. The order is semantically required: compound
# phrases ("nutzung_auto_update", "nutzung_refresh_minuten") must be matched
# before the bare base token they contain ("nutzung", "modelle"), otherwise the
# compound would be corrupted.
#
# The slugified form (spaces -> "_", umlauts transliterated, parentheses
# dropped) is exactly what HA's ``slugify`` produced when the legacy entities
# were first registered. The concrete position of the entity-specific segment
# depends on the platform:
#
#   number.*        -> ``[<device>_]go_gauge_<fragment>_<workspace>`` (leads;
#                      HA 2026.9 prepends the device-name slug ``go_gauge_ha_``)
#   sensor.*        -> ``[<device>_]go_gauge_<workspace>_..._<fragment>``
#                      (trails; catalog entities are workspace-independent)
#   switch.*        -> ``[<device>_]go_gauge_<workspace>_<fragment>``
#   binary_sensor.* -> ``[<device>_]go_gauge_<workspace>_<fragment>`` or exact
#   button.*        -> ``[<device>_]go_gauge_<fragment>``              (exact)
#
# Anchoring on those positions keeps the workspace/device prefix byte-identical:
# a naïve ``str.replace`` over the whole object_id would also rewrite a workspace
# slug that happens to contain a German mapping token (e.g. a workspace named
# "Modelle" or "Nutzung Auto-Update"). The optional ``<device>_`` component is
# tolerated (not reconstructed) so real 2026.9 slugs such as
# ``number.go_gauge_ha_go_gauge_warnschwelle_e2e`` still migrate. See
# ``migrate_entity_id`` below.
ENTITY_ID_MIGRATION: tuple[tuple[str, str], ...] = (
    ("nutzung_auto_update", "auto_update_usage"),
    ("modelle_auto_update", "auto_update_models"),
    ("nutzung_refresh_minuten", "usage_refresh_min"),
    ("modelle_refresh_minuten", "models_refresh_min"),
    ("gunstigstes_modell", "cheapest_model"),
    ("live_modelle", "live_models"),
    ("free_modelle", "free_models"),
    ("warnschwelle", "warning_threshold"),
    ("ampel_rot_grenze", "pace_red_limit"),
    ("abo_aktiv", "subscription_active"),
    ("api_erreichbar", "api_reachable"),
    ("restbudget", "remaining"),
    ("restzeit", "time_to_reset"),
    ("prognose", "forecast"),
    ("nutzung", "usage"),
    ("modelle", "models"),
    ("aktualisieren", "refresh"),
)


def migrate_entity_id(entity_id: str | None) -> str | None:
    """Translate a legacy German ``entity_id`` into its mapped English slug.

    Splits the domain off and rewrites only the entity-specific segment of the
    object_id, rejoining afterwards - so the domain and the workspace/device
    prefix are never touched. ``number.*`` entities carry the fragment *before*
    the workspace (``[<device>_]go_gauge_<fragment>_<ws>``; the device-name
    slug observed on HA 2026.9 is ``go_gauge_ha_``), all other platforms either
    match the fragment as an exact object_id (workspace-independent catalog /
    button entities) or as a trailing ``_<fragment>`` (window/switch/
    subscription entities). Because the match is anchored, a workspace slug that
    itself contains a German mapping token (e.g. "Modelle") is preserved
    verbatim.

    Returns ``None`` when there is nothing to migrate: ``None``/empty input, an
    input without a domain separator, an object_id without the ``go_gauge_``
    prefix, or an already-English slug. That lets callers distinguish "changed"
    from "unchanged".

    Idempotent: feeding the migrated output back returns ``None``. Deterministic
    and HA-free so it is unit-testable without a Home Assistant install.
    ``unique_id`` is never involved - only the ``entity_id`` slug is rewritten.
    """
    if not entity_id or "." not in entity_id:
        return None
    domain, _, object_id = entity_id.partition(".")
    migrated = _migrate_object_id(domain, object_id)
    if migrated is None or migrated == object_id:
        return None
    return f"{domain}.{migrated}"


def _migrate_object_id(domain: str, object_id: str) -> str | None:
    """Rewrite only the entity-specific segment of one object_id.

    Which anchor applies is decided by the platform (see the
    ``ENTITY_ID_MIGRATION`` comment above). At most ONE pair is applied (the
    first matching one in table order) and the function returns immediately, so
    a workspace slug that repeats a mapping token can never be rewritten a
    second time.

    HA de-duplicates a colliding entity_id by appending ``_<n>``; that numeric
    suffix is split off first and re-appended verbatim, so ``..._nutzung_2``
    still migrates without the workspace ever being touched.
    """
    if not object_id.startswith("go_gauge_"):
        return None
    base, dedup_suffix = _split_dedup_suffix(object_id)
    for legacy, english in ENTITY_ID_MIGRATION:
        migrated = _apply_anchor(domain, base, legacy, english)
        if migrated is not None:
            return migrated + dedup_suffix
    return None


def _split_dedup_suffix(object_id: str) -> tuple[str, str]:
    """Split HA's trailing ``_<n>`` de-duplication suffix off an object_id."""
    base, sep, tail = object_id.rpartition("_")
    if sep and tail.isdigit():
        return base, f"_{tail}"
    return object_id, ""


def _apply_anchor(
    domain: str, object_id: str, legacy: str, english: str
) -> str | None:
    """Apply one mapping pair at the platform-specific anchor, or return None."""
    if domain == "number":
        # Entity-name slug is ``go_gauge_<fragment>`` optionally followed by
        # ``_<workspace>``. Since HA 2026.9 the *registered* entity_id also
        # carries the device-name slug in front (observed on the real instance:
        # ``number.go_gauge_ha_go_gauge_warnschwelle_e2e``). Anchor on the
        # fragment's own ``go_gauge_`` prefix as a full token, so both the
        # device prefix (``go_gauge_ha_``) and the trailing workspace stay
        # byte-identical - the m3 guarantee. Searching (instead of the former
        # startswith-only match) tolerates that prefix without reconstructing
        # it; the first valid token wins, so the workspace is never rewritten.
        marker = f"go_gauge_{legacy}"
        start = 0
        while (index := object_id.find(marker, start)) != -1:
            end = index + len(marker)
            if end == len(object_id) or object_id[end] == "_":
                return (
                    object_id[:index]
                    + f"go_gauge_{english}"
                    + object_id[end:]
                )
            start = index + 1
        return None
    # Workspace-independent catalog/button entities match exactly ...
    if object_id == f"go_gauge_{legacy}":
        return f"go_gauge_{english}"
    # ... window/switch/subscription entities match as a trailing token.
    suffix = f"_{legacy}"
    if object_id.endswith(suffix):
        return f"{object_id[:-len(suffix)]}_{english}"
    return None


# Full browser UA - Cloudflare blocks non-browser agents (Error 1010)
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Static pricing table (USD per 1M tokens). The Zen Go API does not expose
# prices reliably; keep in sync with https://opencode.ai/docs/go/#usage-limits
# req = [per 5h, per week, per month] estimated requests on Go budget.
PRICING: dict[str, dict[str, Any]] = {
    "grok-4.5": {"in": 2.00, "out": 6.00, "cr": 0.30, "cw": None, "usage": 15, "req": [120, 300, 600]},
    "gpt-5.6-luna": {"in": (0.20, 0.40), "out": (1.20, 1.80), "cr": (0.02, 0.04), "cw": (0.25, 0.50), "usage": 15, "req": [2050, 5100, 10250]},
    "glm-5.3": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None, "usage": 15, "req": [220, 540, 1080]},
    "glm-5.2": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None, "usage": 60, "req": [880, 2150, 4300]},
    "glm-5.1": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None, "usage": 60, "req": [880, 2150, 4300]},
    "glm-5": {"in": 1.40, "out": 4.40, "cr": 0.26, "cw": None, "usage": 60, "req": [None, None, None]},
    "kimi-k3": {"in": 3.00, "out": 15.00, "cr": 0.30, "cw": None, "usage": 15, "req": [110, 250, 490]},
    "kimi-k2.7-code": {"in": 0.95, "out": 4.00, "cr": 0.19, "cw": None, "usage": 60, "req": [1350, 3380, 6750]},
    "kimi-k2.6": {"in": 0.95, "out": 4.00, "cr": 0.16, "cw": None, "usage": 60, "req": [1150, 2880, 5750]},
    "deepseek-v4-pro": {"in": (0.66, 1.32), "out": (1.98, 3.96), "cr": (0.022, 0.044), "cw": None, "usage": 15, "req": [1050, 2600, 5200]},
    "deepseek-v4-flash": {"in": (0.22, 0.44), "out": (0.66, 1.32), "cr": (0.007, 0.014), "cw": None, "usage": 30, "req": [7600, 18900, 37800]},
    "deepseek-v4-flash-vision-exp": {"in": (0.22, 0.44), "out": (0.66, 1.32), "cr": (0.007, 0.014), "cw": None, "usage": 15, "req": [3800, 9450, 18900]},
    "mimo-vii.5": {"in": 0.14, "out": 0.28, "cr": 0.0028, "cw": None, "usage": 60, "req": [30100, 75200, 150400]},
    "mimo-vii.5-pro": {"in": 0.435, "out": 0.87, "cr": 0.003625, "cw": None, "usage": 15, "req": [3250, 8150, 16300]},
    "minimax-m3": {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": None, "usage": 60, "req": [3200, 8000, 16000]},
    "minimax-m2.7": {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": 0.375, "usage": 60, "req": [3400, 8500, 17000]},
    "minimax-m2.5": {"in": 0.30, "out": 1.20, "cr": 0.06, "cw": 0.375, "usage": 60, "req": [None, None, None]},
    "muse-spark-1.2-contributor": {"in": 0.10, "out": 0.20, "cr": 0.002, "cw": None, "usage": 60, "req": [45300, 113300, 226600]},
    "qwen3.8-max": {"in": 2.00, "out": 6.00, "cr": 0.25, "cw": 2.50, "usage": 15, "req": [160, 400, 810]},
    "qwen3.7-max": {"in": 2.50, "out": 7.50, "cr": 0.50, "cw": 3.125, "usage": 60, "req": [340, 840, 1690]},
    "qwen3.7-plus": {"in": (0.40, 1.20), "out": (1.60, 4.80), "cr": (0.04, 0.12), "cw": (0.50, 1.50), "usage": 60, "req": [4300, 10800, 21600]},
    "qwen3.6-plus": {"in": (0.50, 2.00), "out": (3.00, 6.00), "cr": (0.05, 0.20), "cw": (0.625, 2.50), "usage": 60, "req": [3300, 8200, 16300]},
    "hy3": {"in": 0.14, "out": 0.58, "cr": 0.035, "cw": None, "usage": 60, "req": [4300, 10750, 21500]},
    "ox-alpha-free": {"in": 0.0, "out": 0.0, "cr": 0.0, "cw": None, "usage": 0, "req": [None, None, None], "free": True},
}
