"""Go Gauge HA - refresh button (manual update regardless of auto-cycles)."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoGaugeCoordinator
from .entity import GoGaugeEntityBase

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RefreshButton(coordinator, entry)])


class RefreshButton(GoGaugeEntityBase, ButtonEntity):
    """Erzwingt sofortiges Nachladen BEIDER Zyklen (Usage + Modelle),
    unabhaengig von den Auto-Update-Schaltern."""

    _attr_icon = "mdi:refresh"
    _attr_name = "Go Gauge Aktualisieren"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_refresh"

    async def async_press(self) -> None:
        # Beide Zyklus-Zeitstempel zuruecksetzen => naechster Refresh holt beides
        coordinator = self.coordinator
        coordinator.last_usage_fetch = None
        coordinator.last_models_fetch = None
        await coordinator.async_request_refresh()
