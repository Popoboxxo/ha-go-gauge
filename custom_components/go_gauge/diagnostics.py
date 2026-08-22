"""Diagnostics for Go Gauge HA - redacted state dump (no tokens ever)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data or {}
    return {
        "entry": {
            "host": entry.data.get("host"),
            "port": entry.data.get("port"),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "fetched_at": data.get("fetched_at"),
        "model_count_live": data.get("model_count_live"),
        "workspaces": [
            {"key": ws["key"], "name": ws["name"], "status": ws["status"]}
            for ws in data.get("workspaces", [])
        ],
        # Tokens exist only on the monitor host, never pass through here;
        # redact anyway as defense in depth.
        "raw_redacted": async_redact_data(data, {"token", "tokens", "authorization"}),
    }
