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
    WINDOW_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

MODELS_URL = "https://opencode.ai/zen/go/v1/models"
USAGE_URL = "https://opencode.ai/zen/go/v1/usage"

# Burn-rate lookback window: only the last 2h of usage samples feed the
# %/h slope. A full hour-plus of history keeps the slope stable across the
# short 5h rolling window while still ignoring long-past consumption.
BURN_RATE_LOOKBACK_SECONDS = 2 * 3600
# Minimum time span between the oldest and newest sample for a meaningful
# slope - below 5min the quotient amplifies poll jitter into noise.
BURN_RATE_MIN_SPAN_SECONDS = 300

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
        in_price = _pnum(prices["in"])
        out_price = _pnum(prices["out"])
        mixed: float | None = None
        if in_price is not None and out_price is not None:
            mixed = 0.8 * in_price + 0.2 * out_price
    except (KeyError, TypeError):
        mixed = None
    req_m = (prices.get("req") or [None, None, None])[2]
    rpd = (req_m / 60.0) if isinstance(req_m, (int, float)) and req_m else None
    return {
        "usd_per_1m_mixed": round(mixed, 3) if mixed is not None else None,
        "month_req_per_usd": round(rpd, 1) if rpd else None,
        "free": False,
    }


def window_elapsed_fraction(win: str, resets_at: datetime | None,
                             now: datetime | None = None) -> float | None:
    """Fraction of the window elapsed so far, 0 < f <= 1; None if unknown.

    None right after a reset (elapsed <= 0) - dividing by a near-zero
    fraction would blow the forecast up into meaningless numbers.
    """
    if resets_at is None:
        return None
    total = WINDOW_SECONDS.get(win)
    if not total:
        return None
    now = now or datetime.now(timezone.utc)
    remaining = (resets_at - now).total_seconds()
    elapsed = total - remaining
    if elapsed <= 0:
        return None
    return min(elapsed / total, 1.0)


def forecast_percent(ws: dict[str, Any] | None, win: str,
                      now: datetime | None = None) -> float | None:
    """Linear pace projection: current percent extrapolated to window end."""
    if not ws or ws.get("status") in ("no_subscription", "error"):
        return None
    blk = (ws.get("windows") or {}).get(win) or {}
    pct = blk.get("percent")
    if not isinstance(pct, (int, float)):
        return None
    frac = window_elapsed_fraction(win, blk.get("resets_at"), now)
    if not frac:
        return None
    return round(pct / frac, 1)


def pace_status(forecast_pct: float, green_below: float, red_above: float) -> str:
    """green/yellow/red vs. the two configurable thresholds.

    Clamp red_above >= green_below defensively - a misconfigured Number
    entity (red below green) just collapses the yellow band instead of
    inverting green/red.
    """
    red_above = max(red_above, green_below)
    if forecast_pct < green_below:
        return "green"
    if forecast_pct > red_above:
        return "red"
    return "yellow"


def remaining_percent(ws: dict[str, Any] | None, win: str) -> float | None:
    """Restbudget of one workspace window: 100 - used percent.

    None for a missing workspace, a no_subscription/error workspace and a
    non-numeric percent - same "None instead of a fake number" pattern as
    UsagePercentSensor (a MEASUREMENT/%-sensor must never carry a string).
    """
    if not ws or ws.get("status") in ("no_subscription", "error"):
        return None
    blk = (ws.get("windows") or {}).get(win) or {}
    pct = blk.get("percent")
    if not isinstance(pct, (int, float)):
        return None
    return 100.0 - float(pct)


def seconds_until_reset(ws: dict[str, Any] | None, win: str,
                        now: datetime | None = None) -> float | None:
    """Seconds until the window resets, clamped at 0; None if unknown.

    A stale resets_at in the past yields 0.0, never a negative duration.
    """
    if not ws:
        return None
    blk = (ws.get("windows") or {}).get(win)
    if not blk:
        return None
    resets_at = blk.get("resets_at")
    if not isinstance(resets_at, datetime):
        return None
    now = now or datetime.now(timezone.utc)
    return max((resets_at - now).total_seconds(), 0.0)


def burn_rate_per_hour(samples: list[tuple[datetime, float]] | None,
                       now: datetime | None = None) -> float | None:
    """Consumption slope in %/h over the recent sample history.

    - Only samples within BURN_RATE_LOOKBACK_SECONDS of `now` are used.
    - Needs at least two samples spanning BURN_RATE_MIN_SPAN_SECONDS.
    - A negative slope means a window reset happened between samples and is
      not a "rate" - returns None (the history recorder clears on reset, so
      this is only a defensive fallback).
    """
    if not samples or len(samples) < 2:
        return None
    now = now or datetime.now(timezone.utc)
    recent = [
        (ts, pct) for ts, pct in samples
        if (now - ts).total_seconds() <= BURN_RATE_LOOKBACK_SECONDS
    ]
    if len(recent) < 2:
        return None
    first_ts, first_pct = recent[0]
    last_ts, last_pct = recent[-1]
    span = (last_ts - first_ts).total_seconds()
    if span < BURN_RATE_MIN_SPAN_SECONDS:
        return None
    rate = (last_pct - first_pct) / (span / 3600.0)
    if rate < 0:
        return None
    return round(rate, 2)


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
    """Workspace-independent model catalog (one JSON blob for one sensor).

    Fix 2026-09-05 (User-Report): Free/cheapest/ranking werden NUR ueber
    live-gelistete Modelle berechnet. Ein statisch in PRICING gepflegtes
    Modell, das die API nicht mehr listet (z. B. ox-alpha-free), bleibt
    zwar im Katalog-Listing sichtbar (live: false), darf aber Free-Modelle/
    Günstigstes nicht mehr belegen. Bei live_ids=None (noch nie erfolgreich
    geladen) ist kein Live-Filter moeglich -> vorheriges Verhalten.
    """
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
    usable = [m for m in models if m.get("live") is not False]
    ranked = sorted(
        [m for m in usable if m.get("usd_per_1m_mixed") is not None],
        key=lambda m: m["usd_per_1m_mixed"],
    )
    paid_ranked = [m for m in ranked if not m["free"]]
    return {
        "model_count_live": len(live_ids or []),
        "models": models,
        "cheapest_model": paid_ranked[0]["id"] if paid_ranked else None,
        "cheapest_overall": ranked[0]["id"] if ranked else None,
        "cheapest_ratio": (paid_ranked[0].get("usd_per_1m_mixed") if paid_ranked else None),
        "free_models": [m["id"] for m in usable if m["free"]],
        "models_updated_at": datetime.now(timezone.utc).isoformat(),
    }


class GoGaugeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Two-cycle coordinator: usage (fast) + models (slow), each toggleable.

    auto_update off => no scheduled polling at all; only the refresh button
    (or a manual service call) triggers an update.
    """

    # Set by the config-entry setup after construction (workspace display name
    # and catalog-ownership flag). Declared here so platform entities that read
    # them type-check against GoGaugeCoordinator instead of the HA base type.
    ws_name: str
    is_catalog_owner: bool

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
        # Warnschwelle + Ampel-Rot-Grenze als Runtime-Attribute (Number-Entities
        # schreiben sie live)
        from .const import (  # local import: no cycle at import time
            CONF_PACE_RED_PERCENT,
            DEFAULT_PACE_RED_PERCENT,
            DEFAULT_WARN_PERCENT,
        )
        self.warn_percent = int(options.get("warn_percent", DEFAULT_WARN_PERCENT))
        self.pace_red_percent = int(
            options.get(CONF_PACE_RED_PERCENT, DEFAULT_PACE_RED_PERCENT))
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
        # Usage-Verlauf je Workspace-Fenster ("ws1:5h") -> [(ts, percent)],
        # Basis der Burn-Rate-Berechnung (on-read, kein Reset-Job).
        self._usage_samples: dict[str, list[tuple[datetime, float]]] = {}

    def recalculate_interval(self) -> None:
        """Intervall nach Auto-Update-Schaltern/Minuten NEU setzen (live).

        Der Setter von ``update_interval`` speichert nur den neuen Wert; ein
        bereits laufender Refresh-Timer laeuft sonst mit dem ALTEN Intervall
        weiter. ``_schedule_refresh()`` meldet den scharfen Timer ab und plant
        den naechsten Abruf mit dem neuen Intervall.
        """
        intervals = []
        if self.auto_usage:
            intervals.append(self.usage_minutes * 60)
        if self.auto_models:
            intervals.append(self.models_minutes * 60)
        effective = min(intervals) if intervals else 86400
        self.update_interval = timedelta(seconds=effective)
        self._schedule_refresh()
        _LOGGER.info("Go Gauge: Update-Intervall -> %s s (usage=%s/%smin, models=%s/%smin)",
                     effective, self.auto_usage, self.usage_minutes,
                     self.auto_models, self.models_minutes)

    def _record_usage_sample(self, key: str, win: str, percent: float,
                             now: datetime) -> None:
        """Append a (timestamp, percent) sample for the burn-rate sensor.

        Window-reset detection: a percent DROP for the same window means the
        window rolled over, so the pre-reset history is cleared first instead
        of leaving a bogus negative slope behind. The history is pruned to
        BURN_RATE_LOOKBACK_SECONDS and capped at 64 entries (newest kept) to
        bound memory over long uptimes.

        Defensive `getattr`: lightweight test doubles bind `_async_update_data`
        without running GoGaugeCoordinator.__init__, so `_usage_samples` may
        not exist yet.
        """
        store = getattr(self, "_usage_samples", None)
        if store is None:
            store = {}
            self._usage_samples = store
        samples = store.setdefault(f"{key}:{win}", [])
        if samples and percent < samples[-1][1]:
            samples.clear()
        samples.append((now, percent))
        cutoff = now - timedelta(seconds=BURN_RATE_LOOKBACK_SECONDS)
        samples[:] = [s for s in samples if s[0] >= cutoff]
        del samples[:-64]

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
                    #
                    # KEIN "status" als Pflichtfeld: die reale Erfolgsantwort
                    # ist {"usage": {...}} ohne Top-Level-status; der Parser
                    # defaultet bereits auf "ok" (siehe oben), und status wird
                    # hier nur optional uebernommen. Eine Pflichtpruefung wuerde
                    # bei JEDEM erfolgreichen Abruf einen Falsch-Positiv-Alarm
                    # erzeugen (Audit 2026-09-13, RC-4).
                    is_synthetic_error = res.get("status") in ("no_subscription", "error")
                    if not is_synthetic_error and "usage" not in res:
                        missing_fields.add("usage")
                    api = res.get("usage") or {}
                    for api_key, win in (
                        ("rolling", "5h"),
                        ("weekly", "week"),
                        ("monthly", "month"),
                    ):
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
                        # Nur ein ERFOLGREICHER Abruf erzeugt einen Sample -
                        # im Except-Pfad (alter Stand beibehalten) NICHT.
                        for win, blk in entry["windows"].items():
                            pct = blk.get("percent")
                            if isinstance(pct, (int, float)):
                                self._record_usage_sample(key, win, float(pct), now)
                except Exception as err:  # noqa: BLE001
                    # TRANSIENTER Fehler (Netz/Cloudflare): LETZTEN STAND BEHALTEN
                    entry = dict(old) if old else entry
                    entry["status"] = old.get("status", "error") if old else "error"
                    # Hinweis: KEIN Zeitstempel hier - "fetched_at" wird erst
                    # unten (nach diesem Try/Except-Block) pro Workspace
                    # gesetzt, ist an dieser Stelle also fuer "old" nicht
                    # zuverlaessig verfuegbar (Audit 2026-09-04).
                    entry["note"] = (
                        f"Abruf fehlgeschlagen, letzter bekannter Stand "
                        f"beibehalten: {err}"
                    )
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
                     or now - (self.last_models_fetch or now)
                     >= timedelta(minutes=self.models_minutes))
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
            "last_usage_fetch": (
                self.last_usage_fetch.isoformat() if self.last_usage_fetch else None
            ),
            "last_models_fetch": (
                self.last_models_fetch.isoformat() if self.last_models_fetch else None
            ),
            "auto_usage": self.auto_usage,
            "auto_models": self.auto_models,
            "usage_refresh_minutes": self.usage_minutes,
            "models_refresh_minutes": self.models_minutes,
            "workspaces": usage,
            "models_block": models_block,
        }
