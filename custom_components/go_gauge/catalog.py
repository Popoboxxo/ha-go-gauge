"""Go Gauge HA - workspace-independent model catalog logic.

Deliberately HA-free: this module imports no ``homeassistant`` code, so pricing,
cost-benefit ranking and catalog aggregation stay unit-testable without a
Home-Assistant installation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .const import PRICING


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
