"""Shared entity base for Go Gauge (switch/number/sensor/binary_sensor)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)


class GoGaugeEntityBase(CoordinatorEntity):
    """Common device-info wiring + entry reference for all Go Gauge entities."""

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry,
                 *, kind: str = "sensor") -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._kind = kind
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
