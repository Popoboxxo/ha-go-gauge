"""Go Gauge HA - Data coordinator.

Polls the OpenCode Go Monitor (/state JSON) and normalizes it for the
entity platform. Everything is derived dynamically from the payload:
workspaces (ws1..wsN), windows, and the workspace-independent model catalog.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Cloudflare blocks plain urllib on opencode.ai; the monitor already solved that
# server-side with curl. We only talk to the monitor over LAN here.


class GoGaugeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch /state from the monitor and expose normalized data."""

    def __init__(self, hass: HomeAssistant, host: str, port: int,
                 scan_interval: int = DEFAULT_SCAN_INTERVAL) -> None:
        self._base = f"http://{host}:{port}"
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{self._base}/state", timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                resp.raise_for_status()
                raw = await resp.json(content_type=None)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Monitor nicht erreichbar ({self._base}): {err}") from err

        return self._normalize(raw)

    # -- normalization -------------------------------------------------------

    @staticmethod
    def _parse_reset(resets_at: str | None) -> datetime | None:
        """ISO string -> aware datetime (HA timestamp sensors need UTC)."""
        if not resets_at:
            return None
        try:
            dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        workspaces: list[dict[str, Any]] = []
        for key, ws in (raw.get("usage") or {}).items():
            entry = {
                "key": key,
                "name": ws.get("name") or key,
                "slot": ws.get("slot"),
                "id": ws.get("id"),
                "status": ws.get("status"),
                "windows": {},
            }
            if ws.get("status") == "ok":
                for win, blk in (ws.get("windows") or {}).items():
                    pct = blk.get("percent")
                    entry["windows"][win] = {
                        "percent": float(pct) if isinstance(pct, (int, float)) else None,
                        "usd": blk.get("usd"),
                        "limit_usd": blk.get("limit_usd"),
                        "resets_at": self._parse_reset(blk.get("resets_at")),
                        "status": blk.get("status"),
                    }
            workspaces.append(entry)

        models: list[dict[str, Any]] = []
        for m in raw.get("models") or []:
            ratio = m.get("ratio") or {}
            prices = m.get("prices") or {}
            models.append({
                "id": m.get("id"),
                "live": m.get("live"),
                "free": bool(prices.get("free")),
                "pricing_known": m.get("pricing_known", False),
                "usd_per_1m_mixed": ratio.get("usd_per_1m_mixed"),
                "month_req_per_usd": ratio.get("month_req_per_usd"),
                "in": prices.get("in"),
                "out": prices.get("out"),
                "cache_read": prices.get("cr"),
            })

        ranked = sorted(
            [m for m in models if m["usd_per_1m_mixed"] is not None],
            key=lambda m: m["usd_per_1m_mixed"],
        )
        cheapest = ranked[0]["id"] if ranked else None
        free_models = [m["id"] for m in models if m["free"]]

        return {
            "fetched_at": raw.get("fetched_at"),
            "model_count_live": raw.get("model_count_live"),
            "models_live_ok": raw.get("models_live_ok"),
            "workspaces": workspaces,
            "models": models,
            "cheapest_model": cheapest,
            "free_models": free_models,
        }
