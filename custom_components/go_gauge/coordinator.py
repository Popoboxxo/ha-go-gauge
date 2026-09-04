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
        # Warnschwelle als Runtime-Attribut (Number-Entity schreibt sie live)
        from .const import DEFAULT_WARN_PERCENT  # local import: no cycle at import time
        self.warn_percent = int(options.get("warn_percent", DEFAULT_WARN_PERCENT))
        # Flag: Runtime-Entities persistieren Optionen ohne Entry-Reload
        self._skip_reload = False

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

    def recalculate_interval(self) -> None:
        """Intervall nach Auto-Update-Schaltern/Minuten NEU setzen (live)."""
        intervals = []
        if self.auto_usage:
            intervals.append(self.usage_minutes * 60)
        if self.auto_models:
            intervals.append(self.models_minutes * 60)
        effective = min(intervals) if intervals else 86400
        self.update_interval = timedelta(seconds=effective)
        _LOGGER.info("Go Gauge: Update-Intervall -> %s s (usage=%s/%smin, models=%s/%smin)",
                     effective, self.auto_usage, self.usage_minutes,
                     self.auto_models, self.models_minutes)

    async def _async_update_data(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)

        # --- Usage-Zyklus -----------------------------------------------------
        # WICHTIG: Bei Fehlern NICHT die alten Daten verwerfen (kein Unbekannt!),
        # sondern letzten Stand behalten und Fehler nur als Attribut melden.
        prev_ws = {w["key"]: w for w in ((self.data or {}).get("workspaces") or [])}
        usage = list((self.data or {}).get("workspaces") or [])
        usage_due = (
            self._tokens
            and (self.auto_usage
                 or not usage  # erster Start: immer laden
                 or self.last_usage_fetch is None)
            and (self.last_usage_fetch is None
                 or now - self.last_usage_fetch >= timedelta(minutes=self.usage_minutes))
        )
        if usage_due:
            fresh = []
            ok_any = False
            # Sammelt fehlende erwartete Felder ueber alle Workspaces dieses
            # Update-Zyklus, damit nur EINE aggregierte Warnung geloggt wird
            # (kein Log-Spam pro Workspace/Fenster).
            missing_fields: set[str] = set()
            for i, token in enumerate(self._tokens, start=1):
                key = f"ws{i}"
                old = prev_ws.get(key, {})
                entry: dict[str, Any] = {"key": key, "token_slot": i,
                                         "status": "ok", "windows": {}}
                try:
                    res = await self._client.fetch_usage(token)
                    # Audit vom 2026-09-04: Ein Feldnamen-Mismatch zwischen der
                    # opencode.ai-API-Response und diesem Parser wird als
                    # moeglicher Root-Cause-Kandidat fuer leere/falsche
                    # Sensordaten vermutet - NICHT gegen die Live-API
                    # verifiziert. Diese Pruefung macht ein fehlendes
                    # erwartetes Feld ueber eine Log-Warnung SICHTBAR statt es
                    # wie bisher lautlos per dict.get() als None durchzureichen;
                    # das bestehende Fallback-Verhalten (None/alter Wert)
                    # bleibt dabei unveraendert. Unsere eigenen synthetischen
                    # 403-Antworten (status in no_subscription/error, siehe
                    # fetch_usage) haben absichtlich ein leeres "usage": {} und
                    # zaehlen daher nicht als Schema-Drift.
                    is_synthetic_error = res.get("status") in ("no_subscription", "error")
                    if not is_synthetic_error and "usage" not in res:
                        missing_fields.add("usage")
                    if not is_synthetic_error and "status" not in res:
                        missing_fields.add("status")
                    api = res.get("usage") or {}
                    for api_key, win in (("rolling", "5h"), ("weekly", "week"), ("monthly", "month")):
                        if not is_synthetic_error and api_key not in api:
                            missing_fields.add(f"usage.{api_key}")
                        blk = api.get(api_key) or {}
                        if not is_synthetic_error and api_key in api and "resetsAt" not in blk:
                            missing_fields.add(f"usage.{api_key}.resetsAt")
                        entry["windows"][win] = {
                            "percent": blk.get("percent"),
                            "status": blk.get("status"),
                            "resets_at": _parse_reset(blk.get("resetsAt")),
                        }
                    if res.get("status"):
                        entry["status"] = res["status"]
                    if res.get("note"):
                        entry["note"] = res["note"]
                    if entry["status"] == "ok":
                        ok_any = True
                except Exception as err:  # noqa: BLE001
                    # TRANSIENTER Fehler (Netz/Cloudflare): LETZTEN STAND BEHALTEN
                    entry = dict(old) if old else entry
                    entry["status"] = old.get("status", "error") if old else "error"
                    entry["note"] = f"letzter Stand vom {(old.get('fetched_at') or 'Start')}: {err}"
                    _LOGGER.warning("Go Gauge %s: Abruf fehlgeschlagen (%s) - behalte alten Stand",
                                    key, err)
                fresh.append(entry)
            if missing_fields:
                _LOGGER.warning(
                    "Go Gauge: erwartete Felder fehlen in der Usage-API-Response "
                    "(%s) - moeglicher Feldnamen-Mismatch, siehe Audit 2026-09-04; "
                    "betroffene Sensoren fallen auf None/alten Wert zurueck",
                    ", ".join(sorted(missing_fields)),
                )
            usage = fresh
            self.last_usage_fetch = now
            for w in usage:
                w["fetched_at"] = now.isoformat()
            if not ok_any and all(
                w.get("status") in ("no_subscription", "error") for w in usage
            ):
                _LOGGER.warning(
                    "Go Gauge: kein Workspace mit aktiven Nutzungsdaten "
                    "(alle no_subscription/error)")

        # --- Modell-Zyklus ----------------------------------------------------
        # NUR der Catalog-Owner ruft fetch_models() auf! Bei N Instanzen sonst
        # N-fache Requests -> Cloudflare-Rate-Limit -> "Modelle spackt".
        # Nicht-Owner: leeren Katalog melden (ihre Entities existieren eh nicht).
        models_block = (self.data or {}).get("models_block")
        if not getattr(self, "is_catalog_owner", True):
            models_due = False
            models_block = models_block or {"model_count_live": 0, "models": [],
                                            "shared": True}
        else:
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
                self.models_fetch_errors = 0
            except Exception as err:  # noqa: BLE001
                self.models_fetch_errors = getattr(self, "models_fetch_errors", 0) + 1
                if models_block is None:
                    # Noch nie erfolgreich -> Fehler nur beim ERSTEN Versuch hart,
                    # danach degradiert weiterlaufen (HA-Default-Retry bleibt aktiv).
                    if self.models_fetch_errors >= 3:
                        raise UpdateFailed(
                            f"Modell-Katalog nicht abrufbar: {err}") from err
                    _LOGGER.warning("Go Gauge: Modell-Abruf fehlgeschlagen (%s) - "
                                    "versuche weiter", err)
                else:
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
