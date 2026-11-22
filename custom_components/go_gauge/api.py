"""Go Gauge HA - HTTP client for the opencode.ai Go inference API.

Deliberately HA-free: this module imports no ``homeassistant`` code, so the
transport layer stays unit-testable without a Home-Assistant installation.

Endpoint note: migrated from the undocumented ``/zen/`` path to the documented
``/inference/`` endpoint with our own distinctive User-Agent instead of a
browser-header spoof, per docs/PLAN-api-endpoint-dedup-account-split.md
2.4a/2.5 (burst-test verified live 2026-11-22, see plan 2.1b).
"""
from __future__ import annotations

from typing import Any

import aiohttp

from .const import API_USER_AGENT, GoGaugeAuthError

MODELS_URL = "https://opencode.ai/inference/go/v1/models"
USAGE_URL = "https://opencode.ai/inference/go/v1/usage"

BROWSER_HEADERS: dict[str, str] = {
    "User-Agent": API_USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://opencode.ai",
}


class OpenCodeGoApiClient:
    """Thin async client for the two Go inference endpoints."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def fetch_models(self) -> list[dict[str, Any]]:
        async with self._session.get(MODELS_URL, headers=BROWSER_HEADERS,
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        models = []
        for m in data.get("data", []):
            mid = m.get("id")
            if not mid:
                continue
            text_cost = ((m.get("cost") or {}).get("text") or {})
            in_price = (text_cost.get("input") or {}).get("price")
            out_price = (text_cost.get("output") or {}).get("price")
            models.append({"id": mid, "free": in_price == 0 and out_price == 0})
        return models

    async def fetch_usage(self, token: str) -> dict[str, Any]:
        headers = {**BROWSER_HEADERS, "Authorization": f"Bearer {token}"}
        async with self._session.get(USAGE_URL, headers=headers,
                                     timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 401:
                # Token fehlt/ungueltig/abgelaufen - KEIN transienter Fehler,
                # sondern ein Konfigurationsproblem. Gezielt als GoGaugeAuthError
                # melden; der Coordinator uebersetzt das in ConfigEntryAuthFailed
                # und startet damit HAs Reauth-Flow statt still alte Daten zu
                # behalten. (403 bleibt unten UNVERAENDERT behandelt.)
                raise GoGaugeAuthError("HTTP 401 - token invalid/expired")
            if resp.status == 403:
                # 403 hat ZWEI Ursachen - sauber trennen:
                # a) EntitlementError = kein aktives Abo (gueltiger Token)
                # b) Cloudflare-Rate-Limit/Bot-Score = TRANSIENTER Fehler
                try:
                    body = await resp.json(content_type=None)
                except Exception:  # noqa: BLE001
                    body = {}
                err = (body.get("error") or {})
                if err.get("type") == "EntitlementError":
                    return {"usage": {}, "status": "no_subscription",
                            "note": err.get("message", "OpenCode Go subscription required.")}
                # Kein Entitlement-Fehler -> als Fehler melden (mit Retry sinnvoll)
                return {"usage": {}, "status": "error",
                        "note": f"HTTP 403 ({err.get('type') or 'blocked'}) - "
                                "möglicherweise Rate-Limit"}
            resp.raise_for_status()
            return await resp.json(content_type=None)
