"""Go Gauge HA - number platform (runtime-editable settings)."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_MODELS_REFRESH_MINUTES,
    DEFAULT_USAGE_REFRESH_MINUTES,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
)
from .coordinator import GoGaugeCoordinator
from .entity import GoGaugeEntityBase, persist_options

_LOGGER = logging.getLogger(__name__)


def _with_ws(coordinator: GoGaugeCoordinator, base: str) -> str:
    ws = getattr(coordinator, "ws_name", "") or "WS 1"
    return f"{base} · {ws}" if ws else base


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WarnPercentNumber(coordinator, entry),
        UsageRefreshMinutesNumber(coordinator, entry),
        ModelsRefreshMinutesNumber(coordinator, entry),
    ])


class _SettingNumber(GoGaugeEntityBase, NumberEntity):
    """Basis fuer Zahlen-Einstellungen (Box-Modus, sofort wirksam)."""

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 unique_suffix: str, name: str,
                 min_value: int, max_value: int) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_name = name
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = 1


class WarnPercentNumber(_SettingNumber):
    """Warnschwelle in Prozent (1..100)."""

    _attr_icon = "mdi:alert-octagon-outline"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="warn_percent",
                         name=f"{_with_ws(coordinator, 'Go Gauge Warnschwelle')}",
                         min_value=1, max_value=100)

    @property
    def native_value(self) -> float | None:
        return getattr(self.coordinator, "warn_percent", DEFAULT_WARN_PERCENT)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.warn_percent = int(value)
        persist_options(self.hass, self._entry, self.coordinator,
                        warn_percent=int(value))


class UsageRefreshMinutesNumber(_SettingNumber):
    """Nutzungs-Refresh-Intervall in Minuten (1..1440)."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="usage_refresh_minutes",
                         name=f"{_with_ws(coordinator, 'Go Gauge Nutzung Refresh (Minuten)')}",
                         min_value=1, max_value=1440)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.usage_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.usage_minutes = int(value)
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        usage_refresh_minutes=int(value))


class ModelsRefreshMinutesNumber(_SettingNumber):
    """Modell-Refresh-Intervall in Minuten (1..1440)."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="models_refresh_minutes",
                         name=f"{_with_ws(coordinator, 'Go Gauge Modelle Refresh (Minuten)')}",
                         min_value=1, max_value=1440)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.models_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.models_minutes = int(value)
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        models_refresh_minutes=int(value))
