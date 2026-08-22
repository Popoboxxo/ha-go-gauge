"""Go Gauge HA - switch platform (Auto-Update toggles)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GoGaugeCoordinator
from .entity import GoGaugeEntityBase, persist_options

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        AutoUpdateUsageSwitch(coordinator, entry),
        AutoUpdateModelsSwitch(coordinator, entry),
    ])


class AutoUpdateUsageSwitch(GoGaugeEntityBase, SwitchEntity):
    """Auto-Refresh fuer Nutzungsdaten ein/aus."""

    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_auto_update_usage"
        self._attr_name = "Go Gauge Nutzung Auto-Update"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.auto_usage

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_usage = True
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_usage=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_usage = False
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_usage=False)


class AutoUpdateModelsSwitch(GoGaugeEntityBase, SwitchEntity):
    """Auto-Refresh fuer Modell-Katalog ein/aus."""

    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_auto_update_models"
        self._attr_name = "Go Gauge Modelle Auto-Update"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.auto_models

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_models = True
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_models=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_models = False
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_models=False)
