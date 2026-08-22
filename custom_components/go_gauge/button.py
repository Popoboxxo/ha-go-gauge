"""Go Gauge HA - refresh button."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RefreshButton(coordinator, entry)])


class RefreshButton(CoordinatorEntity, ButtonEntity):
    """Trigger an immediate coordinator refresh."""

    _attr_icon = "mdi:refresh"
    _attr_name = "Go Gauge Aktualisieren"
    _attr_unique_id: str

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Go Gauge HA",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }

    async def async_press(self) -> None:
        await self.coordinator.async_request_refresh()
