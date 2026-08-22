"""Go Gauge HA - sensor platform.

Dynamic entities: one set of sensors per workspace (from /state usage map)
plus a workspace-independent model catalog block. All percent values,
concrete reset timestamps and cost figures come straight from the monitor.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL, WINDOW_LABELS
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Go Gauge sensors dynamically from coordinator data."""
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    # Workspace sensors (ws1..wsN) x windows + reset timestamps
    for ws in coordinator.data.get("workspaces", []):
        for win in ("5h", "week", "month"):
            label = WINDOW_LABELS.get(win, win)
            entities.append(UsagePercentSensor(coordinator, entry, ws["key"], ws["name"], win, label))
            entities.append(ResetTimestampSensor(coordinator, entry, ws["key"], ws["name"], win, label))
        if ws.get("windows", {}).get("month", {}).get("usd") is not None:
            entities.append(MonthUsdSensor(coordinator, entry, ws["key"], ws["name"]))
        entities.append(ModelCountSensor(coordinator, entry))

    # Model catalog (workspace-independent)
    entities.append(CheapestModelSensor(coordinator, entry))
    entities.append(FreeModelsSensor(coordinator, entry))
    entities.append(LiveModelsCountSensor(coordinator, entry))
    for m in coordinator.data.get("models", [])[:40]:
        entities.append(ModelRatioSensor(coordinator, entry, m["id"]))

    async_add_entities(entities)


class _GoGaugeEntity(CoordinatorEntity):
    """Base entity wiring."""

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Go Gauge HA",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }

    def _ws(self, key: str) -> dict[str, Any] | None:
        for ws in self.coordinator.data.get("workspaces", []):
            if ws["key"] == key:
                return ws
        return None


class UsagePercentSensor(_GoGaugeEntity, SensorEntity):
    """Percent usage of one workspace window."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, entry, key: str, name: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_percent"
        self._attr_name = f"Go Gauge {name} {label} Nutzung"

    @property
    def native_value(self) -> float | None:
        ws = self._ws(self._key)
        if not ws or ws.get("status") != "ok":
            return None
        blk = ws["windows"].get(self._win) or {}
        return blk.get("percent")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ws = self._ws(self._key)
        attrs: dict[str, Any] = {"workspace_key": self._key, "window": self._win}
        if ws and ws.get("status") == "ok":
            blk = ws["windows"].get(self._win) or {}
            attrs.update({
                "usd": blk.get("usd"),
                "limit_usd": blk.get("limit_usd"),
                "status": blk.get("status"),
                "resets_at_iso": (blk["resets_at"].isoformat() if blk.get("resets_at") else None),
            })
        else:
            attrs["status"] = ws.get("status") if ws else "unknown"
        return attrs


class ResetTimestampSensor(_GoGaugeEntity, SensorEntity):
    """Concrete reset time as HA timestamp entity."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-reset"

    def __init__(self, coordinator, entry, key: str, name: str, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_reset"
        self._attr_name = f"Go Gauge {name} {label} Reset"

    @property
    def native_value(self) -> datetime | None:
        ws = self._ws(self._key)
        if not ws or ws.get("status") != "ok":
            return None
        return (ws["windows"].get(self._win) or {}).get("resets_at")


class MonthUsdSensor(_GoGaugeEntity, SensorEntity):
    """Spent USD this month for one workspace."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "USD"
    _attr_icon = "mdi:cash"

    def __init__(self, coordinator, entry, key: str, name: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}_month_usd"
        self._attr_name = f"Go Gauge {name} Monat USD"

    @property
    def native_value(self) -> float | None:
        ws = self._ws(self._key)
        if not ws or ws.get("status") != "ok":
            return None
        return (ws["windows"].get("month") or {}).get("usd")


class ModelCountSensor(_GoGaugeEntity, SensorEntity):
    """Number of live models in the catalog."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:format-list-numbered"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_models_live_count"
        self._attr_name = "Go Gauge Live-Modelle"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("model_count_live")


class CheapestModelSensor(_GoGaugeEntity, SensorEntity):
    """Cheapest model by mixed $/1M ratio."""

    _attr_icon = "mdi:crown-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_cheapest_model"
        self._attr_name = "Go Gauge Günstigstes Modell"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("cheapest_model")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        cheapest = self.coordinator.data.get("cheapest_model")
        out: dict[str, Any] = {}
        for m in self.coordinator.data.get("models", []):
            if m["id"] == cheapest:
                out["usd_per_1m_mixed"] = m["usd_per_1m_mixed"]
                out["month_req_per_usd"] = m["month_req_per_usd"]
        return out


class FreeModelsSensor(_GoGaugeEntity, SensorEntity):
    """Comma-separated list of free models (attribute list)."""

    _attr_icon = "mdi:gift-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_free_models"
        self._attr_name = "Go Gauge Free-Modelle"

    @property
    def native_value(self) -> str | None:
        free = self.coordinator.data.get("free_models") or []
        return ", ".join(free) if free else None


class LiveModelsCountSensor(ModelCountSensor):
    """Alias kept for clarity; count already covers live models."""


class ModelRatioSensor(_GoGaugeEntity, SensorEntity):
    """Per-model cost ratio ($/1M mixed). One entity per model."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "USD/1M"
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry, model_id: str) -> None:
        super().__init__(coordinator, entry)
        self._model_id = model_id
        safe = model_id.replace("-", "_").replace(".", "_").lower()
        self._attr_unique_id = f"{entry.entry_id}_model_{safe}"
        self._attr_name = f"Go Gauge Modell {model_id}"

    @property
    def native_value(self) -> float | None:
        for m in self.coordinator.data.get("models", []):
            if m["id"] == self._model_id:
                return m.get("usd_per_1m_mixed")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        for m in self.coordinator.data.get("models", []):
            if m["id"] == self._model_id:
                return {
                    "live": m.get("live"),
                    "free": m.get("free"),
                    "pricing_known": m.get("pricing_known"),
                    "in_usd_1m": m.get("in"),
                    "out_usd_1m": m.get("out"),
                    "cache_read_usd_1m": m.get("cache_read"),
                    "month_req_per_usd": m.get("month_req_per_usd"),
                }
        return {}
