"""Diagnostics for Go Gauge HA - redacted dump with REAL debug info.

Daniel-Debugging-Forderung (v0.6): Die Diagnose muss SOFORT zeigen,
warum keine Daten ankommen - ohne Chat-Rueckfragen. Enthält:
- Integration-Version + ConfigEntry-Version
- Token-Fingerprint (erste 8 Zeichen, maskiert) zur Zuordnung
- Letzter Abruf je Zyklus + Fehler
- Workspace-Status mit note
- Entity-Registry-Zuordnung
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

REDACT_KEYS = {"token", "tokens", "authorization", "workspace_name"}


async def async_get_config_entry_diagnostics(
    hass, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics that actually help debugging."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {
            "error": "Coordinator nicht geladen - Setup ist fehlgeschlagen! "
                     "Log pruefen: 'Go Gauge' filtern.",
            "entry_state": str(entry.state),
        }

    integration = await async_get_integration(hass, DOMAIN)

    data = coordinator.data or {}
    token = str(entry.data.get("token") or "")
    token_fp = (token[:8] + "…") if token else "KEIN TOKEN HINTERLEGT!"

    ws_states = [
        {
            "key": ws.get("key"),
            "status": ws.get("status"),
            "note": (ws.get("note") or "")[:120],
            "fetched_at": ws.get("fetched_at"),
            "windows": {
                win: {
                    "percent": ((ws.get("windows") or {}).get(win) or {}).get("percent"),
                    "reset": str(((ws.get("windows") or {}).get(win) or {}).get("resets_at")),
                }
                for win in ("5h", "week", "month")
            },
        }
        for ws in data.get("workspaces", [])
    ]

    # Interpretations-Hilfe direkt mitschicken:
    hints = []
    if not data.get("fetched_at"):
        hints.append("NOCH NIE erfolgreich abgerufen. Entweder Netz/Cloudflare "
                     "beim Start oder Entry im SETUP_ERROR. HA neustarten und "
                     "Log auf 'Go Gauge' pruefen.")
    for ws in ws_states:
        if ws["status"] == "no_subscription":
            hints.append(f"{ws['key']}: Token gueltig, aber Workspace hat KEIN "
                         "aktives Abo -> in der OpenCode-UI Abo reaktivieren "
                         "oder andere Tokens verwenden.")
        if ws["status"] == "error":
            hints.append(f"{ws['key']}: Abruffehler ({ws['note']}). Bei 'HTTP 403' "
                         "ohne EntitlementError = Cloudflare-Rate-Limit, "
                         "Intervall erhoehen.")

    return {
        "integration_version": integration.version,
        "config_entry_version": entry.version,
        "entry_state": str(entry.state),
        "token_fingerprint": token_fp,
        "workspace_name": entry.data.get("workspace_name"),
        "options": async_redact_data(dict(entry.options), REDACT_KEYS),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "auto_usage": coordinator.auto_usage,
            "auto_models": coordinator.auto_models,
            "usage_refresh_minutes": coordinator.usage_minutes,
            "models_refresh_minutes": coordinator.models_minutes,
            "is_catalog_owner": getattr(coordinator, "is_catalog_owner", "?"),
            "last_usage_fetch": data.get("last_usage_fetch"),
            "last_models_fetch": data.get("last_models_fetch"),
        },
        "workspaces": async_redact_data(ws_states, REDACT_KEYS),
        "models_block_present": data.get("models_block") is not None,
        "interpretation_hints": hints,
    }
