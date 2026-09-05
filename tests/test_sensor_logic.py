#!/usr/bin/env python3
"""Unit tests for sensor entity logic (mocked data, no live API calls).

Tests the sensor classes (ModelCatalogSensor, UsagePercentSensor, etc.) against
mock Coordinator data, validating state transformations, attributes, and edge cases
like 'no_subscription' status and missing windows.

These tests extract the sensor logic phase from manual_offline_smoke.py and run
as pure pytest, making them part of the automated CI pipeline.
"""
from datetime import datetime
import json
import importlib.util
import sys
import types
from pathlib import Path

# Base homeassistant.* module fakes are installed by conftest.py before this
# test file is collected.

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "go_gauge"


def _class_getitem(cls, item):
    return cls


def _enum_like(*members):
    return type("EnumStub", (), {m.upper(): m for m in members})


# Setup coordinator entity base class and sensor imports
_uc = sys.modules["homeassistant.helpers.update_coordinator"]
_uc.DataUpdateCoordinator = type(
    "DataUpdateCoordinator", (object,), {"__class_getitem__": classmethod(_class_getitem)}
)


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


_uc.CoordinatorEntity = _CoordinatorEntity
sys.modules["homeassistant.components.sensor"].SensorStateClass = _enum_like("measurement")
sys.modules["homeassistant.components.sensor"].SensorDeviceClass = _enum_like("timestamp")


def _load_module(name, path):
    """Dynamically load an integration module for testing."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Load modules once at test collection time (not per-test fixtures)
const = _load_module("go_gauge.const", str(BASE / "const.py"))
_load_module("go_gauge.coordinator", str(BASE / "coordinator.py"))
_load_module("go_gauge.entity", str(BASE / "entity.py"))
sensor = _load_module("go_gauge.sensor", str(BASE / "sensor.py"))


class _FakeCoordinator:
    """Minimal Coordinator mock for sensor testing."""

    def __init__(self, data):
        self.data = data
        self.last_update_success = True


class _FakeEntry:
    """Minimal ConfigEntry mock."""

    entry_id = "test_entry"


class TestModelCatalogSensor:
    """Test ModelCatalogSensor: state = model count, attributes = catalog/rankings."""

    def test_catalog_count_success(self):
        """native_value returns model count from models_block."""
        coordinator = _FakeCoordinator({
            "models_block": {
                "models": [
                    {"id": "gpt-4o", "live": True, "free": False, "usd_per_1m_mixed": 0.03},
                    {"id": "gpt-4-turbo", "live": True, "free": False, "usd_per_1m_mixed": 0.01},
                ],
                "model_count_live": 2,
                "cheapest_model": "gpt-4-turbo",
                "cheapest_overall": "free-model-1",
                "free_models": ["free-model-1"],
                "models_updated_at": datetime.now(),
                "cheapest_ratio": 0.01,
            },
            "workspaces": [],
        })
        entry = _FakeEntry()
        cat = sensor.ModelCatalogSensor(coordinator, entry)
        cat.coordinator = coordinator

        assert cat.native_value == 2

    def test_catalog_attributes_structure(self):
        """extra_state_attributes contains catalog_json and rankings."""
        coordinator = _FakeCoordinator({
            "models_block": {
                "models": [
                    {"id": "model-a", "live": True, "free": False, "usd_per_1m_mixed": 0.05},
                    {"id": "model-b", "live": False, "free": True, "usd_per_1m_mixed": None},
                ],
                "model_count_live": 1,
                "cheapest_model": "model-a",
                "cheapest_overall": "model-b",
                "free_models": ["model-b"],
                "models_updated_at": datetime.now(),
                "cheapest_ratio": 0.05,
            },
            "workspaces": [],
        })
        entry = _FakeEntry()
        cat = sensor.ModelCatalogSensor(coordinator, entry)
        cat.coordinator = coordinator

        attrs = cat.extra_state_attributes
        assert "catalog_json" in attrs
        assert attrs["count"] == 2
        assert attrs["live_count"] == 1
        assert attrs["free_models"] == ["model-b"]
        assert attrs["cheapest_model"] == "model-a"

        # Verify catalog_json is valid JSON
        catalog = json.loads(attrs["catalog_json"])
        assert len(catalog) == 2
        assert catalog[0]["id"] == "model-a"

    def test_catalog_ranking_by_cost(self):
        """ranking_by_cost sorts models by usd_per_1m_mixed (cheapest first)."""
        coordinator = _FakeCoordinator({
            "models_block": {
                "models": [
                    {"id": "expensive", "live": True, "free": False, "usd_per_1m_mixed": 0.10},
                    {"id": "cheap", "live": True, "free": False, "usd_per_1m_mixed": 0.01},
                    {"id": "mid", "live": True, "free": False, "usd_per_1m_mixed": 0.05},
                ],
                "model_count_live": 3,
                "cheapest_model": "cheap",
                "cheapest_overall": "cheap",
                "free_models": [],
                "models_updated_at": datetime.now(),
                "cheapest_ratio": 0.01,
            },
            "workspaces": [],
        })
        entry = _FakeEntry()
        cat = sensor.ModelCatalogSensor(coordinator, entry)
        cat.coordinator = coordinator

        attrs = cat.extra_state_attributes
        ranking = attrs["ranking_by_cost"]
        assert len(ranking) == 3
        # Sorted cheapest first
        assert ranking[0]["id"] == "cheap"
        assert ranking[1]["id"] == "mid"
        assert ranking[2]["id"] == "expensive"

    def test_catalog_empty(self):
        """native_value returns None when models list is empty or missing."""
        coordinator = _FakeCoordinator({
            "models_block": {"models": []},
            "workspaces": [],
        })
        entry = _FakeEntry()
        cat = sensor.ModelCatalogSensor(coordinator, entry)
        cat.coordinator = coordinator

        assert cat.native_value is None

    def test_catalog_no_models_block(self):
        """native_value returns None when models_block is missing."""
        coordinator = _FakeCoordinator({"workspaces": []})
        entry = _FakeEntry()
        cat = sensor.ModelCatalogSensor(coordinator, entry)
        cat.coordinator = coordinator

        assert cat.native_value is None


class TestUsagePercentSensor:
    """Test UsagePercentSensor: reads workspace usage percent for a specific window."""

    def test_usage_percent_ok_status(self):
        """native_value returns float percent when status='ok' and window exists."""
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws1",
                    "status": "ok",
                    "windows": {
                        "month": {"percent": 42.5, "resets_at": datetime(2026, 10, 1)},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        pct = sensor.UsagePercentSensor(
            coordinator, entry, key="ws1", win="month", label="Monthly", ws_name="1"
        )
        pct.coordinator = coordinator

        assert pct.native_value == 42.5

    def test_usage_percent_no_subscription_status(self):
        """native_value returns 'Kein Abo' string when status='no_subscription'."""
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws2",
                    "status": "no_subscription",
                    "windows": {
                        "month": {"percent": None, "resets_at": None},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        pct = sensor.UsagePercentSensor(
            coordinator, entry, key="ws2", win="month", label="Monthly", ws_name="2"
        )
        pct.coordinator = coordinator

        assert pct.native_value == "Kein Abo"

    def test_usage_percent_error_status(self):
        """native_value returns 'Fehler' string when status='error'."""
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws3",
                    "status": "error",
                    "windows": {
                        "month": {"percent": None, "resets_at": None},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        pct = sensor.UsagePercentSensor(
            coordinator, entry, key="ws3", win="month", label="Monthly", ws_name="3"
        )
        pct.coordinator = coordinator

        assert pct.native_value == "Fehler"

    def test_usage_percent_missing_window(self):
        """native_value returns None when window key doesn't exist."""
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws1",
                    "status": "ok",
                    "windows": {
                        "month": {"percent": 50.0, "resets_at": datetime(2026, 10, 1)},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        pct = sensor.UsagePercentSensor(
            coordinator, entry, key="ws1", win="week", label="Weekly", ws_name="1"
        )
        pct.coordinator = coordinator

        # week window not in data
        assert pct.native_value is None

    def test_usage_percent_attributes(self):
        """extra_state_attributes includes workspace_key, window, status, resets_at_iso."""
        reset_time = datetime(2026, 10, 1, 12, 30, 0)
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws1",
                    "status": "ok",
                    "note": "All systems operational",
                    "windows": {
                        "month": {"percent": 75.0, "resets_at": reset_time},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        pct = sensor.UsagePercentSensor(
            coordinator, entry, key="ws1", win="month", label="Monthly", ws_name="1"
        )
        pct.coordinator = coordinator

        attrs = pct.extra_state_attributes
        assert attrs["workspace_key"] == "ws1"
        assert attrs["window"] == "month"
        assert attrs["status"] == "ok"
        assert attrs["note"] == "All systems operational"
        assert attrs["resets_at_iso"] == "2026-10-01T12:30:00"

    def test_usage_percent_integer_value(self):
        """native_value handles integer percent values (not just float)."""
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws1",
                    "status": "ok",
                    "windows": {
                        "5h": {"percent": 33, "resets_at": None},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        pct = sensor.UsagePercentSensor(
            coordinator, entry, key="ws1", win="5h", label="5-Hour", ws_name="1"
        )
        pct.coordinator = coordinator

        assert pct.native_value == 33.0


class TestResetTimestampSensor:
    """Test ResetTimestampSensor: emits datetime for window reset."""

    def test_reset_timestamp_valid(self):
        """native_value returns datetime when resets_at is present."""
        reset_time = datetime(2026, 9, 10, 14, 0, 0)
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws1",
                    "status": "ok",
                    "windows": {
                        "week": {"percent": 30.0, "resets_at": reset_time},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        rst = sensor.ResetTimestampSensor(
            coordinator, entry, key="ws1", win="week", label="Weekly", ws_name="1"
        )
        rst.coordinator = coordinator

        assert rst.native_value == reset_time

    def test_reset_timestamp_missing_window(self):
        """native_value returns None when window key doesn't exist."""
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws1",
                    "status": "ok",
                    "windows": {
                        "month": {"percent": 50.0, "resets_at": datetime(2026, 10, 1)},
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        rst = sensor.ResetTimestampSensor(
            coordinator, entry, key="ws1", win="5h", label="5-Hour", ws_name="1"
        )
        rst.coordinator = coordinator

        # 5h window not in data
        assert rst.native_value is None

    def test_reset_timestamp_no_reset_at(self):
        """native_value returns None when resets_at is missing."""
        coordinator = _FakeCoordinator({
            "workspaces": [
                {
                    "key": "ws1",
                    "status": "ok",
                    "windows": {
                        "month": {"percent": 50.0},  # no resets_at
                    },
                }
            ],
            "models_block": {},
        })
        entry = _FakeEntry()
        rst = sensor.ResetTimestampSensor(
            coordinator, entry, key="ws1", win="month", label="Monthly", ws_name="1"
        )
        rst.coordinator = coordinator

        assert rst.native_value is None


class TestCheapestModelSensor:
    """Test CheapestModelSensor: reads cheapest model ID from models_block."""

    def test_cheapest_model_value(self):
        """native_value returns the cheapest_model ID."""
        coordinator = _FakeCoordinator({
            "workspaces": [],
            "models_block": {
                "models": [],
                "cheapest_model": "gpt-4-turbo",
                "cheapest_overall": "free-model-1",
                "cheapest_ratio": 0.01,
            },
        })
        entry = _FakeEntry()
        cheapest = sensor.CheapestModelSensor(coordinator, entry)
        cheapest.coordinator = coordinator

        assert cheapest.native_value == "gpt-4-turbo"

    def test_cheapest_model_attributes(self):
        """extra_state_attributes includes cheapest_overall and cheapest_ratio."""
        coordinator = _FakeCoordinator({
            "workspaces": [],
            "models_block": {
                "models": [],
                "cheapest_model": "paid-model",
                "cheapest_overall": "free-model",
                "cheapest_ratio": 0.025,
            },
        })
        entry = _FakeEntry()
        cheapest = sensor.CheapestModelSensor(coordinator, entry)
        cheapest.coordinator = coordinator

        attrs = cheapest.extra_state_attributes
        assert attrs["cheapest_overall"] == "free-model"
        assert attrs["ratio_usd_per_1m"] == 0.025

    def test_cheapest_model_missing_models_block(self):
        """native_value returns None when models_block is missing."""
        coordinator = _FakeCoordinator({"workspaces": []})
        entry = _FakeEntry()
        cheapest = sensor.CheapestModelSensor(coordinator, entry)
        cheapest.coordinator = coordinator

        assert cheapest.native_value is None


class TestFreeModelsSensor:
    """Test FreeModelsSensor: joins free model IDs into comma-separated string."""

    def test_free_models_multiple(self):
        """native_value returns comma-separated free model IDs."""
        coordinator = _FakeCoordinator({
            "workspaces": [],
            "models_block": {
                "models": [],
                "free_models": ["free-model-1", "free-model-2"],
                "cheapest_model": "gpt-4-turbo",
            },
        })
        entry = _FakeEntry()
        free = sensor.FreeModelsSensor(coordinator, entry)
        free.coordinator = coordinator

        assert free.native_value == "free-model-1, free-model-2"

    def test_free_models_single(self):
        """native_value returns single free model ID as-is (no comma)."""
        coordinator = _FakeCoordinator({
            "workspaces": [],
            "models_block": {
                "models": [],
                "free_models": ["free-model-1"],
                "cheapest_model": "gpt-4-turbo",
            },
        })
        entry = _FakeEntry()
        free = sensor.FreeModelsSensor(coordinator, entry)
        free.coordinator = coordinator

        assert free.native_value == "free-model-1"

    def test_free_models_empty(self):
        """native_value returns None when free_models list is empty."""
        coordinator = _FakeCoordinator({
            "workspaces": [],
            "models_block": {
                "models": [],
                "free_models": [],
                "cheapest_model": "gpt-4-turbo",
            },
        })
        entry = _FakeEntry()
        free = sensor.FreeModelsSensor(coordinator, entry)
        free.coordinator = coordinator

        assert free.native_value is None

    def test_free_models_missing_block(self):
        """native_value returns None when models_block is missing."""
        coordinator = _FakeCoordinator({"workspaces": []})
        entry = _FakeEntry()
        free = sensor.FreeModelsSensor(coordinator, entry)
        free.coordinator = coordinator

        assert free.native_value is None
