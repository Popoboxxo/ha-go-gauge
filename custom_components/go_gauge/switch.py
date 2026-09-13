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
    _attr_translation_key = "auto_update_usage"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_auto_update_usage"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.auto_usage

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_usage = True
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_usage=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_usage = False
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_usage=False)
        self.async_write_ha_state()


class AutoUpdateModelsSwitch(GoGaugeEntityBase, SwitchEntity):
    """Auto-Refresh fuer Modell-Katalog ein/aus."""

    _attr_icon = "mdi:autorenew"
    _attr_translation_key = "auto_update_models"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_auto_update_models"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.auto_models

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_models = True
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_models=True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_models = False
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        auto_update_models=False)
        self.async_write_ha_state()
