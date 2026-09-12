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
    """Common device-info wiring + entry reference for all Go Gauge entities.

    ``has_entity_name`` is enabled globally: entity display names are resolved
    from ``translation_key`` lookups (strings.json / translations/*.json) and
    HA prefixes the device name automatically (e.g. "Go Gauge <workspace>" for
    workspace entities, "Go Gauge Konto" for workspace-independent ones). No
    entity sets ``_attr_name`` anymore - a set ``_attr_name`` would short-circuit
    the translation lookup.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        ws_name = getattr(coordinator, "ws_name", "") or "WS 1"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": f"Go Gauge {ws_name}",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }

    def _ws(self, key: str) -> dict[str, Any] | None:
        for ws in self.coordinator.data.get("workspaces", []):
            if ws.get("key") == key:
                return ws
        return None


class GoGaugeAccountEntityBase(GoGaugeEntityBase):
    """Workspace-independent entities (model catalog, API reachability).

    Own device ('Go Gauge Konto'), identified by a fixed key instead of the
    catalog-owner's entry_id - so it stays visually separate from any one
    workspace's device even though the catalog owner's config entry is what
    creates it (see __init__.py is_catalog_owner).
    """

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "account")},
            "name": "Go Gauge Konto",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }


def persist_options(hass: HomeAssistant, entry: ConfigEntry,
                    coordinator: GoGaugeCoordinator, **changes: Any) -> None:
    """Runtime-Entity-Aenderungen persistent speichern OHNE Entry-Reload.

    Die Entities haben den Coordinator bereits live umgestellt; der
    Update-Listener sieht das _skip_reload-Flag und laesst ihn laufen.
    """
    opts = {**entry.options, **changes}
    coordinator._skip_reload = True
    hass.config_entries.async_update_entry(entry, options=opts)
