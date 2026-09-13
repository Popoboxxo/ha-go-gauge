"""Go Gauge HA - number platform (runtime-editable settings)."""
from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_PACE_RED_PERCENT,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
)
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
        WarnPercentNumber(coordinator, entry),
        PaceRedPercentNumber(coordinator, entry),
        UsageRefreshMinutesNumber(coordinator, entry),
        ModelsRefreshMinutesNumber(coordinator, entry),
    ])


class _SettingNumber(GoGaugeEntityBase, NumberEntity):
    """Basis fuer Zahlen-Einstellungen (Box-Modus, sofort wirksam).

    Der Anzeigename kommt aus ``translation_key`` (Klassen-Attribut der
    jeweiligen Unterklasse); HA stellt den Geraetenamen voran.
    """

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 unique_suffix: str,
                 min_value: int, max_value: int) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = 1


class WarnPercentNumber(_SettingNumber):
    """Warnschwelle in Prozent (1..100)."""

    _attr_icon = "mdi:alert-octagon-outline"
    _attr_native_unit_of_measurement = "%"
    _attr_translation_key = "warning_threshold"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="warn_percent",
                         min_value=1, max_value=100)

    @property
    def native_value(self) -> float | None:
        return getattr(self.coordinator, "warn_percent", DEFAULT_WARN_PERCENT)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.warn_percent = int(value)
        persist_options(self.hass, self._entry, self.coordinator,
                        warn_percent=int(value))
        self.async_write_ha_state()


class PaceRedPercentNumber(_SettingNumber):
    """Gelb/Rot-Grenze der Pace-Ampel in Prozent (1..300)."""

    _attr_icon = "mdi:alert-decagram-outline"
    _attr_native_unit_of_measurement = "%"
    _attr_translation_key = "pace_red_limit"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="pace_red_percent",
                         min_value=1, max_value=300)

    @property
    def native_value(self) -> float | None:
        return getattr(self.coordinator, "pace_red_percent", DEFAULT_PACE_RED_PERCENT)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.pace_red_percent = int(value)
        persist_options(self.hass, self._entry, self.coordinator,
                        pace_red_percent=int(value))
        self.async_write_ha_state()


class UsageRefreshMinutesNumber(_SettingNumber):
    """Nutzungs-Refresh-Intervall in Minuten (1..1440)."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_translation_key = "usage_refresh_min"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="usage_refresh_minutes",
                         min_value=1, max_value=1440)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.usage_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.usage_minutes = int(value)
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        usage_refresh_minutes=int(value))
        self.async_write_ha_state()


class ModelsRefreshMinutesNumber(_SettingNumber):
    """Modell-Refresh-Intervall in Minuten (1..1440)."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_translation_key = "models_refresh_min"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="models_refresh_minutes",
                         min_value=1, max_value=1440)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.models_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.models_minutes = int(value)
        self.coordinator.recalculate_interval()
        persist_options(self.hass, self._entry, self.coordinator,
                        models_refresh_minutes=int(value))
        self.async_write_ha_state()
