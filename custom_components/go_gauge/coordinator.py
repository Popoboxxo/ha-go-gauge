"""Go Gauge HA - Data coordinator talking DIRECTLY to opencode.ai.

Two independent refresh cycles (both can be switched off in options):
- Usage  (per workspace tokens): default every 10 min
- Models (public catalog):       default every 60 min

Cloudflare note: plain urllib/requests get blocked (HTTP 403 Error 1010);
aiohttp WITH full browser headers succeeds (verified live 2026-08-22).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_AUTO_UPDATE_MODELS,
    CONF_AUTO_UPDATE_USAGE,
    CONF_MODELS_REFRESH_MINUTES,
    CONF_USAGE_REFRESH_MINUTES,
    DEFAULT_MODELS_REFRESH_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USAGE_REFRESH_MINUTES,
    DOMAIN,
    PRICING,
    USER_AGENT,
)

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
    - free models: usd=0.0, req_per_usd=None (ranked first)
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

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

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
            if resp.status == 403:
                # Kein aktives Abo fuer diesen Workspace (EntitlementError) -
                # gueltiger Token, aber ohne Subscription. Kein harter Fehler.
                try:
                    body = await resp.json(content_type=None)
                except Exception:  # noqa: BLE001
                    body = {}
                err = (body.get("error") or {})
                return {"usage": {}, "status": "no_subscription",
                        "note": err.get("message", "OpenCode Go subscription required.")}
            resp.raise_for_status()
            return await resp.json(content_type=None)


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


def build_models_block(live_ids: list[str] | None) -> dict[str, Any]:
    """Workspace-independent model catalog (one JSON blob for one sensor)."""
    known = set(PRICING.keys())
    models = []
    for mid in sorted(set(live_ids or []) | known):
        p = PRICING.get(mid)
        models.append({
            "id": mid,
            "live": mid in (live_ids or []) if live_ids is not None else None,
            "free": bool(p and p.get("free")),
            "pricing_known": p is not None,
            **(efficiency(p) or {}),
        })
    ranked = sorted(
        [m for m in models if m.get("usd_per_1m_mixed") is not None],
        key=lambda m: m["usd_per_1m_mixed"],
    )
    paid_ranked = [m for m in ranked if not m["free"]]
    return {
        "model_count_live": len(live_ids or []),
        "models": models,
        "cheapest_model": paid_ranked[0]["id"] if paid_ranked else None,
        "cheapest_overall": ranked[0]["id"] if ranked else None,
        "cheapest_ratio": (paid_ranked[0].get("usd_per_1m_mixed") if paid_ranked else None),
        "free_models": [m["id"] for m in models if m["free"]],
        "models_updated_at": datetime.now(timezone.utc).isoformat(),
    }


class GoGaugeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Two-cycle coordinator: usage (fast) + models (slow), each toggleable.

    auto_update off => no scheduled polling at all; only the refresh button
    (or a manual service call) triggers an update.
    """

    def __init__(self, hass: HomeAssistant, tokens: list[str],
                 options: dict[str, Any] | None = None) -> None:
        self.hass = hass
        self._tokens = tokens
        self._client = OpenCodeGoApiClient(async_get_clientsession(hass))
        options = options or {}

        self.auto_usage = options.get(CONF_AUTO_UPDATE_USAGE, True)
        self.usage_minutes = int(
            options.get(CONF_USAGE_REFRESH_MINUTES, DEFAULT_USAGE_REFRESH_MINUTES))
        self.auto_models = options.get(CONF_AUTO_UPDATE_MODELS, True)
        self.models_minutes = int(
            options.get(CONF_MODELS_REFRESH_MINUTES, DEFAULT_MODELS_REFRESH_MINUTES))

        # Gesamtintervall: das schnellere aktive Intervall; wenn alles aus ->
        # sehr langer Intervall (nur Button aktualisiert dann wirklich).
        intervals = []
        if self.auto_usage:
            intervals.append(self.usage_minutes * 60)
        if self.auto_models:
            intervals.append(self.models_minutes * 60)
        effective = min(intervals) if intervals else 86400  # 24h Fallback

        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=effective or DEFAULT_SCAN_INTERVAL),
        )
        # Zeitstempel der letzten echten Abrufe je Zyklus
        self.last_models_fetch: datetime | None = None
        self.last_usage_fetch: datetime | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)

        # --- Usage-Zyklus -----------------------------------------------------
        usage = self.data.get("workspaces", []) if self.data else []
        usage_due = (
            self.auto_usage
            and (self.last_usage_fetch is None
                 or now - self.last_usage_fetch >= timedelta(minutes=self.usage_minutes))
        )
        if usage_due and self._tokens:
            session = async_get_clientsession(self.hass)
            fresh = []
            ok_any = False
            for i, token in enumerate(self._tokens, start=1):
                entry: dict[str, Any] = {"key": f"ws{i}", "token_slot": i,
                                         "status": "ok", "windows": {}}
                try:
                    res = await self._client.fetch_usage(token)
                    api = res.get("usage") or {}
                    for api_key, win in (("rolling", "5h"), ("weekly", "week"), ("monthly", "month")):
                        blk = api.get(api_key) or {}
                        entry["windows"][win] = {
                            "percent": blk.get("percent"),
                            "status": blk.get("status"),
                            "resets_at": _parse_reset(blk.get("resetsAt")),
                        }
                    # no_subscription / error vom Client durchreichen
                    if res.get("status"):
                        entry["status"] = res["status"]
                    if res.get("note"):
                        entry["note"] = res["note"]
                    if entry["status"] == "ok":
                        ok_any = True
                except Exception as err:  # noqa: BLE001
                    entry["status"] = "error"
                    entry["note"] = str(err)
                fresh.append(entry)
            usage = fresh
            self.last_usage_fetch = now
            if not ok_any:
                # Kein Workspace liefert Daten (alle no_subscription/error) ->
                # kein harter UpdateFail: Sensoren zeigen den Zustand transparent.
                _LOGGER.warning(
                    "Go Gauge: kein Workspace mit aktiven Nutzungsdaten "
                    "(alle no_subscription/error)")

        # --- Modell-Zyklus ----------------------------------------------------
        models_block = (self.data or {}).get("models_block")
        models_due = (
            self.auto_models
            and (models_block is None
                 or now - (self.last_models_fetch or now) >= timedelta(minutes=self.models_minutes))
        )
        if models_block is None and not self.auto_models:
            # Erster Start mit ausgeschaltetem Auto-Models: einmal laden
            models_due = True
        if models_due:
            try:
                live_ids = await self._client.fetch_models()
                models_block = build_models_block(live_ids)
                self.last_models_fetch = now
            except Exception as err:  # noqa: BLE001
                if models_block is None:
                    raise UpdateFailed(f"Modell-Katalog nicht abrufbar: {err}") from err
                _LOGGER.warning("Modell-Refresh fehlgeschlagen, nutze alten Stand: %s", err)

        return {
            "fetched_at": now.isoformat(),
            "last_usage_fetch": self.last_usage_fetch.isoformat() if self.last_usage_fetch else None,
            "last_models_fetch": self.last_models_fetch.isoformat() if self.last_models_fetch else None,
            "auto_usage": self.auto_usage,
            "auto_models": self.auto_models,
            "usage_refresh_minutes": self.usage_minutes,
            "models_refresh_minutes": self.models_minutes,
            "workspaces": usage,
            "models_block": models_block,
        }
