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
# Canonical English name fragments. Entity ``_attr_name`` values are built from
# these (sensor/binary_sensor/number/switch/button) and the v6 migration table
# below maps the legacy German fragments onto the very same constants, so new
# installs and migrated installs always yield identical names.
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
