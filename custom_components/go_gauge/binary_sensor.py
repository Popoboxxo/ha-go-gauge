"""Go Gauge HA - binary sensors (rate-limit flags)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for ws in coordinator.data.get("workspaces", []):
        for win in ("5h", "week", "month"):
            entities.append(RateLimitedBinarySensor(coordinator, entry, ws["key"], ws["name"], win))
        entities.append(ReachableBinarySensor(coordinator, entry))
    async_add_entities(entities)


class _GoGaugeBinary(CoordinatorEntity):
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


class RateLimitedBinarySensor(_GoGaugeBinary, BinarySensorEntity):
    """ON when the API reports rate-limited for this workspace window."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:block-helper"

    def __init__(self, coordinator, entry, key: str, name: str, win: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_limited"
        labels = {"5h": "5h rolling", "week": "Weekly", "month": "Monthly"}
        self._attr_name = f"Go Gauge {name} {labels.get(win, win)} rate-limited"

    @property
    def is_on(self) -> bool | None:
        ws = self._ws(self._key)
        if not ws or ws.get("status") != "ok":
            return None
        return (ws["windows"].get(self._win) or {}).get("status") == "rate-limited"


class ReachableBinarySensor(_GoGaugeBinary, BinarySensorEntity):
    """ON while the monitor is reachable and data is fresh."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_monitor_reachable"
        self._attr_name = "Go Gauge Monitor erreichbar"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.last_update_success and bool(
            self.coordinator.data.get("fetched_at")
        )
