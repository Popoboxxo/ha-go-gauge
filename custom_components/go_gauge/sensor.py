"""Go Gauge HA - sensor platform (direct API data)."""
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
    """Create entities dynamically from coordinator data."""
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for ws in coordinator.data.get("workspaces", []):
        key = ws["key"]
        slot = ws.get("token_slot", key)
        name = f"Workspace {slot}"
        for win in ("5h", "week", "month"):
            label = WINDOW_LABELS.get(win, win)
            entities.append(UsagePercentSensor(coordinator, entry, key, slot, win, label))
            entities.append(ResetTimestampSensor(coordinator, entry, key, slot, win, label))

    entities.append(CheapestModelSensor(coordinator, entry))
    entities.append(FreeModelsSensor(coordinator, entry))
    entities.append(LiveModelsCountSensor(coordinator, entry))
    for m in coordinator.data.get("models", [])[:40]:
        if m["ratio"] is not None:
            entities.append(ModelRatioSensor(coordinator, entry, m["id"]))

    async_add_entities(entities)


class _GoGaugeEntity(CoordinatorEntity):
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
    """Percent usage of one workspace window (5h/week/month)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, entry, key: str, slot, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_percent"
        self._attr_name = f"Go Gauge {name_or_slot(slot)} {label} Nutzung"

    @property
    def native_value(self) -> float | None:
        ws = self._ws(self._key)
        if not ws or ws.get("status") not in ("ok", None):
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        pct = blk.get("percent")
        return float(pct) if isinstance(pct, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ws = self._ws(self._key)
        attrs: dict[str, Any] = {"workspace_key": self._key, "window": self._win}
        if ws:
            blk = (ws.get("windows") or {}).get(self._win) or {}
            reset = blk.get("resets_at")
            attrs.update({
                "status": blk.get("status"),
                "resets_at_iso": reset.isoformat() if isinstance(reset, datetime) else None,
            })
        return attrs


class ResetTimestampSensor(_GoGaugeEntity, SensorEntity):
    """Concrete reset time as HA timestamp entity."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-reset"

    def __init__(self, coordinator, entry, key: str, slot, win: str, label: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_reset"
        self._attr_name = f"Go Gauge {name_or_slot(slot)} {label} Reset"

    @property
    def native_value(self) -> datetime | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        val = blk.get("resets_at")
        return val if isinstance(val, datetime) else None


class LiveModelsCountSensor(_GoGaugeEntity, SensorEntity):
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
    """Cheapest paid model by mixed $/1M ratio."""

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
        out: dict[str, Any] = {
            "cheapest_overall": self.coordinator.data.get("cheapest_overall"),
        }
        cheapest = self.coordinator.data.get("cheapest_model")
        for m in self.coordinator.data.get("models", []):
            if m["id"] == cheapest and m.get("ratio"):
                out["usd_per_1m_mixed"] = m["ratio"].get("usd_per_1m_mixed")
                out["month_req_per_usd"] = m["ratio"].get("month_req_per_usd")
        return out


class FreeModelsSensor(_GoGaugeEntity, SensorEntity):
    _attr_icon = "mdi:gift-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_free_models"
        self._attr_name = "Go Gauge Free-Modelle"

    @property
    def native_value(self) -> str | None:
        free = self.coordinator.data.get("free_models") or []
        return ", ".join(free) if free else None


class ModelRatioSensor(_GoGaugeEntity, SensorEntity):
    """Per-model cost-benefit ratio ($/1M mixed tokens)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "USD/1M"
    _attr_icon = "mdi:chart-line"

    def __init__(self, coordinator, entry, model_id: str) -> None:
        super().__init__(coordinator, entry)
        self._model_id = model_id
        safe = model_id.replace("-", "_").replace(".", "_").lower()
        self._attr_unique_id = f"{entry.entry_id}_model_{safe}"
        self._attr_name = f"Go Gauge Modell {model_id}"

    def _find(self) -> dict[str, Any] | None:
        for m in self.coordinator.data.get("models", []):
            if m["id"] == self._model_id:
                return m
        return None

    @property
    def native_value(self) -> float | None:
        m = self._find()
        return (m or {}).get("ratio", {}).get("usd_per_1m_mixed") if m else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self._find() or {}
        r = m.get("ratio") or {}
        return {
            "live": m.get("live"),
            "free": m.get("free"),
            "pricing_known": m.get("pricing_known"),
            "month_req_per_usd": r.get("month_req_per_usd"),
        }


def name_or_slot(slot) -> str:
    return f"WS {slot}"
