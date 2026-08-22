"""Go Gauge HA - binary sensors (rate-limit flags, connectivity, subscription)."""
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

from .const import DOMAIN, MANUFACTURER, MODEL, WINDOW_LABELS
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    ws_name = getattr(coordinator, "ws_name", "") or "WS 1"
    for ws in coordinator.data.get("workspaces", []):
        for win in ("5h", "week", "month"):
            entities.append(RateLimitedBinarySensor(
                coordinator, entry, ws["key"], win, ws_name))
        # Abo-Status je Workspace (403 EntitlementError -> no_subscription)
        entities.append(SubscriptionActiveBinarySensor(
            coordinator, entry, ws["key"], ws_name))
    if getattr(coordinator, "is_catalog_owner", True):
        entities.append(ApiReachableBinarySensor(coordinator, entry))
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
    """ON when OpenCode reports rate-limited for this workspace window."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:block-helper"

    def __init__(self, coordinator, entry, key: str, win: str, ws_name: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_limited"
        label = WINDOW_LABELS.get(win, win)
        self._attr_name = f"Go Gauge {ws_name or 'WS 1'} {label} rate-limited"

    @property
    def is_on(self) -> bool | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        return blk.get("status") == "rate-limited"

    @property
    def available(self) -> bool:
        """Nicht verfuegbar (unavailable), wenn kein Abo - nicht einfach OFF."""
        ws = self._ws(self._key)
        if ws and ws.get("status") == "no_subscription":
            return False
        return True


class SubscriptionActiveBinarySensor(_GoGaugeBinary, BinarySensorEntity):
    """ON = Workspace hat ein aktives Abo (API liefert Nutzungsdaten).

    OFF + Attribut 'note' wenn 403 EntitlementError (kein aktives Abo).
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:shield-check-outline"

    def __init__(self, coordinator, entry, key: str, ws_name: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}_subscription_active"
        self._attr_name = f"Go Gauge {ws_name or 'WS 1'} Abo aktiv"

    @property
    def is_on(self) -> bool | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        if ws.get("status") == "no_subscription":
            return False
        if ws.get("status") == "ok":
            return True
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ws = self._ws(self._key)
        return {"workspace_key": self._key, "note": (ws or {}).get("note")}


class ApiReachableBinarySensor(_GoGaugeBinary, BinarySensorEntity):
    """ON while the opencode.ai API delivers fresh data."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_api_reachable"
        self._attr_name = "Go Gauge API erreichbar"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.last_update_success and bool(
            self.coordinator.data.get("fetched_at"))
