"""Shared entity base + persistence helper for Go Gauge."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)


class GoGaugeEntityBase(CoordinatorEntity):
    """Common device-info wiring + entry reference for all Go Gauge entities."""

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
            if ws.get("key") == key:
                return ws
        return None


def persist_options(hass: HomeAssistant, entry: ConfigEntry,
                    coordinator: GoGaugeCoordinator, **changes: Any) -> None:
    """Runtime-Entity-Aenderungen persistent speichern OHNE Entry-Reload.

    Die Entities haben den Coordinator bereits live umgestellt; der
    Update-Listener sieht das _skip_reload-Flag und laesst ihn laufen.
    """
    opts = {**entry.options, **changes}
    coordinator._skip_reload = True
    hass.config_entries.async_update_entry(entry, options=opts)
