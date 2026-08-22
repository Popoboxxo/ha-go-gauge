"""Go Gauge HA - API client talking DIRECTLY to opencode.ai.

No intermediate monitor/dashboard needed. The OpenCode Zen Go API is
Cloudflare-protected: plain urllib/requests get blocked (HTTP 403 Error 1010),
but aiohttp WITH full browser headers succeeds (verified 2026-08-22 against
live endpoints, models + usage both HTTP 200).

Secrets rule (Daniel): tokens come ONLY from HA storage (config entry), never
logged, never in diagnostics output.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, PRICING, USER_AGENT

_LOGGER = logging.getLogger(__name__)

MODELS_URL = "https://opencode.ai/zen/go/v1/models"
USAGE_URL = "https://opencode.ai/zen/go/v1/usage"

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://opencode.ai",
}


def _pnum(v: Any) -> float | None:
    """Peak/off-peak tuple -> mean; scalar -> itself."""
    if isinstance(v, (tuple, list)) and v:
        vals = [x for x in v if isinstance(x, (int, float))]
        return sum(vals) / len(vals) if vals else None
    return v if isinstance(v, (int, float)) else None


def efficiency(prices: dict[str, Any] | None) -> dict[str, Any] | None:
    """Cost-benefit ratio per model.

    - usd_per_1m_mixed = 80% input + 20% output
    - month_req_per_usd = official monthly request estimate / 60 USD budget
    - free models: ratio with usd=0.0 and req_per_usd=None (ranked first!)
    """
    if prices is None:
        return None
    if prices.get("free"):
        return {"usd_per_1m_mixed": 0.0, "month_req_per_usd": None, "free": True}
    try:
        mixed = 0.8 * _pnum(prices["in"]) + 0.2 * _pnum(prices["out"])
    except (KeyError, TypeError):
        mixed = None
    req_m = (prices.get("req") or [None, None, None])[2]
    rpd = (req_m / 60.0) if isinstance(req_m, (int, float)) and req_m else None
    return {
        "usd_per_1m_mixed": round(mixed, 3) if mixed is not None else None,
        "month_req_per_usd": round(rpd, 1) if rpd else None,
        "free": False,
    }


class OpenCodeGoApiClient:
    """Thin async client for the two Zen Go endpoints."""

    def __init__(self, session: aiohttp.ClientSession, tokens: list[str]) -> None:
        self._session = session
        self._tokens = tokens

    async def fetch_models(self) -> list[str]:
        async with self._session.get(MODELS_URL, headers=BROWSER_HEADERS,
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        return [m.get("id") for m in data.get("data", []) if m.get("id")]

    async def fetch_usage(self, token: str) -> dict[str, Any]:
        headers = {**BROWSER_HEADERS, "Authorization": f"Bearer {token}"}
        async with self._session.get(USAGE_URL, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def fetch_all_usage(self) -> dict[str, dict[str, Any]]:
        """One usage call per workspace token (limits are PER WORKSPACE)."""
        out: dict[str, dict[str, Any]] = {}
        for i, token in enumerate(self._tokens, start=1):
            key = f"ws{i}"
            try:
                out[key] = {"token_slot": i, **await self.fetch_usage(token)}
            except Exception as err:  # noqa: BLE001
                out[key] = {"token_slot": i, "status": "error", "note": str(err)}
        return out


def _parse_reset(resets_at: str | None) -> datetime | None:
    if not resets_at:
        return None
    try:
        dt = datetime.fromisoformat(resets_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


class GoGaugeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls opencode.ai directly (models public + usage per token)."""

    def __init__(self, hass: HomeAssistant, tokens: list[str],
                 scan_interval: int = DEFAULT_SCAN_INTERVAL) -> None:
        self._client = OpenCodeGoApiClient(async_get_clientsession(hass), tokens)
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=__import__("datetime").timedelta(seconds=scan_interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            live_ids = await self._client.fetch_models()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"OpenCode Go API nicht erreichbar: {err}") from err

        known = set(PRICING.keys())
        models = []
        for mid in sorted(set(live_ids or []) | known):
            p = PRICING.get(mid)
            models.append({
                "id": mid,
                "live": mid in (live_ids or []),
                "free": bool(p and p.get("free")),
                "pricing_known": p is not None,
                "ratio": efficiency(p),
            })

        usage: dict[str, Any] = {}
        for key, res in (await self._client.fetch_all_usage()).items():
            entry: dict[str, Any] = {"key": key, "token_slot": res.get("token_slot"),
                                     "status": res.get("status", "ok"), "windows": {}}
            api_usage = res.get("usage") or {}
            for api_key, win in (("rolling", "5h"), ("weekly", "week"), ("monthly", "month")):
                blk = api_usage.get(api_key) or {}
                entry["windows"][win] = {
                    "percent": blk.get("percent"),
                    "status": blk.get("status"),
                    "resets_at": _parse_reset(blk.get("resetsAt")),
                }
            usage[key] = entry

        ranked = sorted(
            [m for m in models if m["ratio"] and m["ratio"]["usd_per_1m_mixed"] is not None],
            key=lambda m: m["ratio"]["usd_per_1m_mixed"],
        )
        free_models = [m["id"] for m in models if m["free"]]
        cheapest_paid = next(
            (m["id"] for m in ranked if not m["free"]), None)

        return {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "model_count_live": len(live_ids or []),
            "models_live_ok": True,
            "workspaces": list(usage.values()),
            "models": models,
            "cheapest_model": cheapest_paid,
            "cheapest_overall": ranked[0]["id"] if ranked else None,
            "free_models": free_models,
        }
