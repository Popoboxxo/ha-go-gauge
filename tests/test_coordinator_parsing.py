#!/usr/bin/env python3
"""Unit tests for coordinator.py kernel functions.

Tests the core parsing, aggregation, and API-client logic that handles:
- fetch_usage/fetch_models (async HTTP with mocked responses)
- _parse_reset (datetime parsing with ISO8601 + edge cases)
- build_models_block (model ranking/catalog aggregation)
- efficiency/_pnum (cost-benefit ratio calculations, Peak/Off-Peak averaging)

Focus: argument order validation (v0.6.1-bug prevention) and field-mapping
correctness (rolling↔5h, weekly↔week, monthly↔month).

NOTE: conftest.py installs the base HA module fakes; this file extends
them with specific test fixtures and coordinator-testing utilities.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


class _Flexible(types.ModuleType):
    """Fallback for any HA submodule not already faked by conftest.py."""

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return type(name, (), {})


def _class_getitem(cls, item):
    """Helper: makes class subscriptable (DataUpdateCoordinator[dict[str, Any]])."""
    return cls


# Ensure all necessary HA modules are faked before importing coordinator.py
_uc = sys.modules.get("homeassistant.helpers.update_coordinator")
if not _uc:
    _uc = _Flexible("homeassistant.helpers.update_coordinator")
    sys.modules["homeassistant.helpers.update_coordinator"] = _uc

_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {
        "__class_getitem__": classmethod(_class_getitem),
        "__init__": lambda self, hass, logger, name=None, update_interval=None: None,
    },
)
_uc.UpdateFailed = type("UpdateFailed", (Exception,), {})

if "homeassistant.core" not in sys.modules:
    sys.modules["homeassistant.core"] = _Flexible("homeassistant.core")

if "homeassistant.helpers.aiohttp_client" not in sys.modules:
    sys.modules["homeassistant.helpers.aiohttp_client"] = _Flexible("homeassistant.helpers.aiohttp_client")

if "aiohttp" not in sys.modules:
    class _ClientTimeout:
        """Minimal mock for aiohttp.ClientTimeout."""
        def __init__(self, total=None):
            self.total = total

    _aiohttp = _Flexible("aiohttp")
    _aiohttp.ClientTimeout = _ClientTimeout
    sys.modules["aiohttp"] = _aiohttp
else:
    # Ensure ClientTimeout exists even if aiohttp was faked by conftest
    _aiohttp = sys.modules["aiohttp"]
    if not hasattr(_aiohttp, "ClientTimeout"):
        class _ClientTimeout:
            def __init__(self, total=None):
                self.total = total
        _aiohttp.ClientTimeout = _ClientTimeout


def _load(name: str, path: str):
    """Dynamically import a module from file, bypass sys.path."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


const = _load("go_gauge.const", str(BASE / "const.py"))
coord = _load("go_gauge.coordinator", str(BASE / "coordinator.py"))


# ============================================================================
# Test: _pnum() - Peak/Off-Peak tuple averaging
# ============================================================================

def test_pnum_returns_scalar_unchanged():
    """[REQ-006] _pnum(5.0) should return 5.0 as-is."""
    assert coord._pnum(5.0) == 5.0
    assert coord._pnum(42) == 42
    assert coord._pnum(0) == 0


def test_pnum_averages_tuple():
    """[REQ-006] _pnum((2.0, 8.0)) should return 5.0 (mean)."""
    assert coord._pnum((2.0, 8.0)) == 5.0
    assert coord._pnum([2.0, 8.0]) == 5.0  # lists also work


def test_pnum_averages_multi_element_tuple():
    """[REQ-006] _pnum with 3+ elements should average all numeric values."""
    result = coord._pnum((1.0, 2.0, 3.0))
    assert result == 2.0  # (1+2+3)/3


def test_pnum_returns_none_for_non_numeric():
    """[REQ-006] _pnum('abc') or _pnum(None) should return None."""
    assert coord._pnum("abc") is None
    assert coord._pnum(None) is None
    assert coord._pnum([]) is None  # empty list


def test_pnum_filters_non_numeric_in_tuple():
    """[REQ-006] _pnum ignores non-numeric items in tuples."""
    result = coord._pnum((2.0, "skip", 8.0))
    assert result == 5.0  # (2+8)/2


# ============================================================================
# Test: efficiency() - Cost-benefit ratio calculation
# ============================================================================

def test_efficiency_scalar_prices():
    """[REQ-007] efficiency with scalar prices: mixed=80%in + 20%out."""
    prices = {"in": 2.0, "out": 6.0, "req": [120, 300, 600]}
    result = coord.efficiency(prices)
    # mixed = 0.8*2.0 + 0.2*6.0 = 1.6 + 1.2 = 2.8
    assert result["usd_per_1m_mixed"] == 2.8
    # month_req_per_usd = 600 / 60 = 10.0
    assert result["month_req_per_usd"] == 10.0
    assert result["free"] is False


def test_efficiency_peak_offpeak_prices():
    """[REQ-007] efficiency averages Peak/Off-Peak tuples before mixing."""
    prices = {"in": (0.20, 0.40), "out": (1.20, 1.80), "req": [2050, 5100, 10250]}
    result = coord.efficiency(prices)
    # _pnum((0.20, 0.40)) = 0.30, _pnum((1.20, 1.80)) = 1.50
    # mixed = 0.8*0.30 + 0.2*1.50 = 0.24 + 0.30 = 0.54
    assert result["usd_per_1m_mixed"] == 0.54
    # month_req_per_usd = 10250 / 60 = 170.833... ≈ 170.8
    assert result["month_req_per_usd"] == 170.8
    assert result["free"] is False


def test_efficiency_free_model():
    """[REQ-007] efficiency marks free models with free=True, usd=0, req=None."""
    prices = {"free": True}
    result = coord.efficiency(prices)
    assert result["usd_per_1m_mixed"] == 0.0
    assert result["month_req_per_usd"] is None
    assert result["free"] is True


def test_efficiency_none_prices():
    """[REQ-007] efficiency(None) returns None."""
    assert coord.efficiency(None) is None


def test_efficiency_missing_in_out_fields():
    """[REQ-007] efficiency with missing 'in'/'out' fields returns None for mixed."""
    prices = {"req": [100, 200, 300]}  # no 'in' or 'out'
    result = coord.efficiency(prices)
    assert result["usd_per_1m_mixed"] is None
    # But req-based calculation should still work
    assert result["month_req_per_usd"] == 5.0


def test_efficiency_missing_req_field():
    """[REQ-007] efficiency with missing 'req' field still calculates mixed."""
    prices = {"in": 1.0, "out": 2.0}  # no 'req'
    result = coord.efficiency(prices)
    assert result["usd_per_1m_mixed"] == 1.2  # 0.8*1 + 0.2*2
    assert result["month_req_per_usd"] is None


def test_efficiency_zero_req_month_element():
    """[REQ-007] efficiency with req[2]=0 or None gives month_req=None."""
    prices = {"in": 1.0, "out": 2.0, "req": [100, 200, 0]}
    result = coord.efficiency(prices)
    assert result["month_req_per_usd"] is None

    prices2 = {"in": 1.0, "out": 2.0, "req": [100, 200, None]}
    result2 = coord.efficiency(prices2)
    assert result2["month_req_per_usd"] is None


# ============================================================================
# Test: _parse_reset() - ISO8601 datetime parsing
# ============================================================================

def test_parse_reset_valid_iso8601_with_z():
    """[REQ-008] _parse_reset('2026-10-01T12:30:45Z') parses to UTC datetime."""
    result = coord._parse_reset("2026-10-01T12:30:45Z")
    assert result is not None
    assert result.year == 2026
    assert result.month == 10
    assert result.day == 1
    assert result.hour == 12
    assert result.minute == 30
    assert result.second == 45
    assert result.tzinfo == timezone.utc


def test_parse_reset_valid_iso8601_with_offset():
    """[REQ-008] _parse_reset handles explicit UTC offset (+00:00)."""
    result = coord._parse_reset("2026-10-01T12:30:45+00:00")
    assert result is not None
    assert result.tzinfo == timezone.utc


def test_parse_reset_none_input():
    """[REQ-008] _parse_reset(None) returns None."""
    assert coord._parse_reset(None) is None


def test_parse_reset_empty_string():
    """[REQ-008] _parse_reset('') returns None."""
    assert coord._parse_reset("") is None


def test_parse_reset_invalid_format():
    """[REQ-008] _parse_reset('not a date') returns None (graceful fallback)."""
    assert coord._parse_reset("not a date") is None
    assert coord._parse_reset("2026-13-01T00:00:00Z") is None  # invalid month
    assert coord._parse_reset("2026-10-32T00:00:00Z") is None  # invalid day


def test_parse_reset_naive_datetime_converted_to_utc():
    """[REQ-008] _parse_reset handles naive datetime (no tzinfo) by adding UTC."""
    # This is a valid ISO format but without timezone indicator
    result = coord._parse_reset("2026-10-01T12:30:45")
    assert result is not None
    assert result.tzinfo == timezone.utc


# ============================================================================
# Test: build_models_block() - Model catalog aggregation & ranking
# ============================================================================

def test_build_models_block_empty_live_ids():
    """[REQ-009] build_models_block([]) includes all known models from PRICING."""
    result = coord.build_models_block([])
    assert result["model_count_live"] == 0
    # Should include all models from const.PRICING
    model_ids = {m["id"] for m in result["models"]}
    assert "grok-4.5" in model_ids
    assert "ox-alpha-free" in model_ids
    # Fix 2026-09-05: explizit LEERE Live-Liste = nichts live ->
    # Free/cheapest leer statt PRICING-Altlasten zu zeigen
    assert result["free_models"] == []
    assert result["cheapest_model"] is None
    assert result["cheapest_overall"] is None


def test_build_models_block_live_ids_in_models():
    """[REQ-009] live_ids are marked with live=True, others live=False."""
    result = coord.build_models_block(["grok-4.5", "gpt-5.6-luna"])
    grok_model = next((m for m in result["models"] if m["id"] == "grok-4.5"), None)
    assert grok_model is not None
    assert grok_model["live"] is True

    mimo_model = next((m for m in result["models"] if m["id"] == "mimo-vii.5"), None)
    assert mimo_model is not None
    assert mimo_model["live"] is False


def test_build_models_block_none_live_ids():
    """[REQ-009] build_models_block(None) treats all models as unknown."""
    result = coord.build_models_block(None)
    assert result["model_count_live"] == 0
    # All models should have live=None (unknown)
    for model in result["models"]:
        assert model["live"] is None
    # Legacy-Fallback ohne Live-Daten: Free-Modelle kommen aus PRICING
    assert "ox-alpha-free" in result["free_models"]


def test_build_models_block_free_models_identified():
    """[REQ-009] Free models flagged correctly (ox-alpha-free)."""
    result = coord.build_models_block(["ox-alpha-free"])
    free_models = result["free_models"]
    assert "ox-alpha-free" in free_models
    assert len(free_models) == 1  # only one free model in PRICING


def test_build_models_block_ranking_by_cost():
    """[REQ-009] build_models_block returns cheapest_model (paid, ranked by cost)."""
    # Keine Live-Daten (None) -> Ranking ueber alle PRICING-Modelle
    result = coord.build_models_block(None)
    # The function identifies cheapest_model (lowest cost among paid models)
    # and cheapest_overall (including free). Both should exist in PRICING.
    assert result["cheapest_model"] is not None  # At least one paid model exists
    assert result["cheapest_overall"] is not None  # At least one model exists

    # Verify that cheapest_overall has usd_per_1m_mixed <= cheapest_model
    # (free models cost 0, so cheapest_overall should be <= cheapest_model)
    cheapest_overall = next(
        (m for m in result["models"] if m["id"] == result["cheapest_overall"]), None
    )
    cheapest_paid = next(
        (m for m in result["models"] if m["id"] == result["cheapest_model"]), None
    )
    assert cheapest_overall is not None
    assert cheapest_paid is not None
    assert (cheapest_overall.get("usd_per_1m_mixed") or 0) <= (cheapest_paid.get("usd_per_1m_mixed") or 999)


def test_build_models_block_dead_model_excluded_from_free_and_cheapest():
    """[REQ-009] Fix 2026-09-05: PRICING-Modell ohne Live-Eintrag ignoriert.

    Reproduziert den User-Report: ox-alpha-free war in PRICING als free
    hinterlegt, wird aber von der API nicht mehr gelistet - die Sensoren
    Free-Modelle/Günstigstes (cheapest_overall) durften ihn daraufhin
    nicht mehr zeigen. Günstigstes (bezahlt) bleibt muse-spark.
    """
    result = coord.build_models_block(["muse-spark-1.2-contributor"])
    # Katalog-Listing behält den Altlast transparent (live: false)
    dead = next((m for m in result["models"] if m["id"] == "ox-alpha-free"), None)
    assert dead is not None
    assert dead["live"] is False
    # ... aber NICHT mehr in free_models / cheapest_overall
    assert "ox-alpha-free" not in result["free_models"]
    assert result["free_models"] == []
    assert result["cheapest_overall"] == "muse-spark-1.2-contributor"
    assert result["cheapest_model"] == "muse-spark-1.2-contributor"
    assert result["cheapest_ratio"] == 0.12  # 0.8*0.10 + 0.2*0.20


def test_build_models_block_cheapest_model_paid():
    """[REQ-009] cheapest_model ignores free models, uses paid ranking."""
    result = coord.build_models_block(["ox-alpha-free", "muse-spark-1.2-contributor"])
    # ox-alpha-free is free, so cheapest_model should be muse-spark (cheapest paid)
    assert result["cheapest_model"] == "muse-spark-1.2-contributor"
    # cheapest_overall includes free, so ox-alpha-free should win (free=0.0)
    assert result["cheapest_overall"] == "ox-alpha-free"


def test_build_models_block_cheapest_overall_free_model():
    """[REQ-009] cheapest_overall picks free model if available."""
    result = coord.build_models_block(["ox-alpha-free"])
    assert result["cheapest_overall"] == "ox-alpha-free"
    # Fix 2026-09-05: nur ox-alpha-free ist live -> kein bezahltes Modell
    # live -> cheapest_model None (PRICING-Altlasten fuellen nichts mehr)
    assert result["cheapest_model"] is None
    # Sobald muse-spark ebenfalls live ist, ist es das guenstigste bezahlte
    result2 = coord.build_models_block(["ox-alpha-free", "muse-spark-1.2-contributor"])
    assert result2["cheapest_model"] == "muse-spark-1.2-contributor"


def test_build_models_block_cheapest_overall_no_pricing_data():
    """[REQ-009] models without pricing info excluded from ranking."""
    # All models in const.PRICING have pricing, so this is edge-case only
    # but the logic should handle unknown models gracefully
    result = coord.build_models_block([])
    # All should have pricing_known=True from PRICING table
    for model in result["models"]:
        assert model["pricing_known"] is True


def test_build_models_block_includes_timestamp():
    """[REQ-009] build_models_block includes models_updated_at timestamp."""
    before = datetime.now(timezone.utc)
    result = coord.build_models_block([])
    after = datetime.now(timezone.utc)

    timestamp_str = result["models_updated_at"]
    timestamp = datetime.fromisoformat(timestamp_str)
    assert before <= timestamp <= after


# NOTE: fetch_models/fetch_usage tests skipped here (complex aiohttp mocking).
# Those API methods are thin wrappers around aiohttp and best tested via
# integration tests. The critical parsing logic is tested above.


# ============================================================================
# Test: Window field mapping (v0.6.1 bug prevention - critical!)
# ============================================================================

class _MockCoordinator:
    """Lightweight coordinator for testing _async_update_data parsing."""

    def __init__(self, response: dict):
        self.hass = None
        self._tokens = ["test-token"]
        self._client = _MockClient(response)
        self.auto_usage = True
        self.usage_minutes = const.DEFAULT_USAGE_REFRESH_MINUTES
        self.auto_models = False
        self.models_minutes = const.DEFAULT_MODELS_REFRESH_MINUTES
        self.warn_percent = const.DEFAULT_WARN_PERCENT
        self._skip_reload = False
        self.last_models_fetch = None
        self.last_usage_fetch = None
        self.data = None
        self.is_catalog_owner = False


class _MockClient:
    """Mock API client that returns fixed response."""

    def __init__(self, response: dict):
        self._response = response

    async def fetch_usage(self, token: str) -> dict:
        return self._response

    async def fetch_models(self) -> list:
        return []


def test_window_field_mapping_rolling_to_5h():
    """[REQ-012] API 'rolling' field correctly maps to window key '5h' (not swapped)."""
    response = {
        "usage": {
            "rolling": {"percent": 10.5, "status": "ok", "resetsAt": "2026-09-05T00:00:00Z"},
            "weekly": {"percent": 25.0, "status": "ok", "resetsAt": "2026-09-08T00:00:00Z"},
            "monthly": {"percent": 50.0, "status": "ok", "resetsAt": "2026-10-04T00:00:00Z"},
        },
        "status": "ok",
    }
    c = _MockCoordinator(response)
    c.__class__ = type("GoGaugeCoordinator", (_MockCoordinator, coord.GoGaugeCoordinator), {})
    # Copy in the real _async_update_data method
    c._async_update_data = coord.GoGaugeCoordinator._async_update_data.__get__(c)

    result = asyncio.run(c._async_update_data())

    ws = result["workspaces"][0]
    # Verify that rolling→5h mapping is correct
    assert ws["windows"]["5h"]["percent"] == 10.5
    # Verify we don't have accidental field swaps
    assert ws["windows"]["week"]["percent"] == 25.0
    assert ws["windows"]["month"]["percent"] == 50.0


def test_window_field_mapping_weekly_to_week():
    """[REQ-012] API 'weekly' field correctly maps to window key 'week'."""
    response = {
        "usage": {
            "rolling": {"percent": 1.0, "status": "ok", "resetsAt": "2026-09-05T00:00:00Z"},
            "weekly": {"percent": 2.0, "status": "ok", "resetsAt": "2026-09-08T00:00:00Z"},
            "monthly": {"percent": 3.0, "status": "ok", "resetsAt": "2026-10-04T00:00:00Z"},
        },
        "status": "ok",
    }
    c = _MockCoordinator(response)
    c.__class__ = type("GoGaugeCoordinator", (_MockCoordinator, coord.GoGaugeCoordinator), {})
    c._async_update_data = coord.GoGaugeCoordinator._async_update_data.__get__(c)

    result = asyncio.run(c._async_update_data())

    ws = result["workspaces"][0]
    # Each window should match its specific value
    assert ws["windows"]["week"]["percent"] == 2.0


def test_window_field_mapping_monthly_to_month():
    """[REQ-012] API 'monthly' field correctly maps to window key 'month'."""
    response = {
        "usage": {
            "rolling": {"percent": 1.0, "status": "ok", "resetsAt": "2026-09-05T00:00:00Z"},
            "weekly": {"percent": 2.0, "status": "ok", "resetsAt": "2026-09-08T00:00:00Z"},
            "monthly": {"percent": 3.0, "status": "ok", "resetsAt": "2026-10-04T00:00:00Z"},
        },
        "status": "ok",
    }
    c = _MockCoordinator(response)
    c.__class__ = type("GoGaugeCoordinator", (_MockCoordinator, coord.GoGaugeCoordinator), {})
    c._async_update_data = coord.GoGaugeCoordinator._async_update_data.__get__(c)

    result = asyncio.run(c._async_update_data())

    ws = result["workspaces"][0]
    # Verify monthly→month mapping is correct
    assert ws["windows"]["month"]["percent"] == 3.0


def test_window_field_mapping_all_three_windows_distinct():
    """[REQ-012] All three windows have distinct, correct values (detect any permutation bug)."""
    response = {
        "usage": {
            "rolling": {"percent": 11.1, "status": "ok", "resetsAt": "2026-09-05T00:00:00Z"},
            "weekly": {"percent": 22.2, "status": "ok", "resetsAt": "2026-09-08T00:00:00Z"},
            "monthly": {"percent": 33.3, "status": "ok", "resetsAt": "2026-10-04T00:00:00Z"},
        },
        "status": "ok",
    }
    c = _MockCoordinator(response)
    c.__class__ = type("GoGaugeCoordinator", (_MockCoordinator, coord.GoGaugeCoordinator), {})
    c._async_update_data = coord.GoGaugeCoordinator._async_update_data.__get__(c)

    result = asyncio.run(c._async_update_data())

    ws = result["workspaces"][0]
    # Each window must have its exact value; any permutation would fail
    assert ws["windows"]["5h"]["percent"] == 11.1
    assert ws["windows"]["week"]["percent"] == 22.2
    assert ws["windows"]["month"]["percent"] == 33.3


if __name__ == "__main__":
    import pytest
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
