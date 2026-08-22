"""Runtime-adjustable settings as HA entities (Switches + Numbers).

Daniel-Anforderung (v0.4): Warnschwelle, Auto-Update ein/aus und Intervalle
NICHT nur im Options-Dialog, sondern zur LAUFZEIT editierbar - als normale
Entitaeten auf dem Device / im Lovelace.

Aenderungen greifen sofort (Coordinator wird live umgestellt) und werden
persistent in den Entry-Options gespeichert (ueberlebt Neustarts).
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_AUTO_UPDATE_MODELS,
    CONF_AUTO_UPDATE_USAGE,
    CONF_MODELS_REFRESH_MINUTES,
    CONF_WARN_PERCENT,
    CONF_USAGE_REFRESH_MINUTES,
    DEFAULT_MODELS_REFRESH_MINUTES,
    DEFAULT_USAGE_REFRESH_MINUTES,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
)
from .coordinator import GoGaugeCoordinator
from .entity import GoGaugeEntityBase

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
        WarnPercentNumber(coordinator, entry),
        UsageRefreshMinutesNumber(coordinator, entry),
        ModelsRefreshMinutesNumber(coordinator, entry),
    ])


def _persist(hass: HomeAssistant, entry: ConfigEntry,
             coordinator: GoGaugeCoordinator, **changes: Any) -> None:
    """Optionen persistent speichern OHNE Entry-Reload (live-Umschaltung).
    Der Update-Listener sieht das Flag und laesst den Coordinator laufen."""
    opts = {**entry.options, **changes}
    coordinator._skip_reload = True
    hass.config_entries.async_update_entry(entry, options=opts)


class AutoUpdateUsageSwitch(GoGaugeEntityBase):
    """Auto-Refresh fuer Nutzungsdaten ein/aus."""

    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, kind="switch")
        self._attr_unique_id = f"{entry.entry_id}_auto_update_usage"
        self._attr_name = "Go Gauge Nutzung Auto-Update"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.auto_usage

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_usage = True
        self.coordinator.recalculate_interval()
        _persist(self.hass, self._entry, self.coordinator,
                 auto_update_usage=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_usage = False
        self.coordinator.recalculate_interval()
        _persist(self.hass, self._entry, self.coordinator,
                 auto_update_usage=False)


class AutoUpdateModelsSwitch(GoGaugeEntityBase):
    """Auto-Refresh fuer Modell-Katalog ein/aus."""

    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, kind="switch")
        self._attr_unique_id = f"{entry.entry_id}_auto_update_models"
        self._attr_name = "Go Gauge Modelle Auto-Update"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.auto_models

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.auto_models = True
        self.coordinator.recalculate_interval()
        _persist(self.hass, self._entry, self.coordinator,
                 auto_update_models=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.auto_models = False
        self.coordinator.recalculate_interval()
        _persist(self.hass, self._entry, self.coordinator,
                 auto_update_models=False)


class _SettingNumber(GoGaugeEntityBase):
    """Basis fuer Zahlen-Einstellungen (Box-Modus, sofort wirksam)."""

    _attr_mode = None  # wird je Klasse gesetzt (Box)

    def __init__(self, coordinator, entry, *, unique_suffix: str, name: str,
                 min_value: int, max_value: int) -> None:
        super().__init__(coordinator, entry, kind="number")
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_name = name
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = 1


class WarnPercentNumber(_SettingNumber):
    """Warnschwelle in Prozent (1..100)."""

    _attr_icon = "mdi:alert-octagon-outline"
    _attr_native_unit_of_measurement = "%"
    _attr_mode = "box"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="warn_percent",
                         name="Go Gauge Warnschwelle",
                         min_value=1, max_value=100)

    @property
    def native_value(self) -> float | None:
        return getattr(self.coordinator, "warn_percent", DEFAULT_WARN_PERCENT)

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.warn_percent = int(value)
        _persist(self.hass, self._entry, self.coordinator,
                 warn_percent=int(value))


class UsageRefreshMinutesNumber(_SettingNumber):
    """Nutzungs-Refresh-Intervall in Minuten (1..1440)."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_mode = "box"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="usage_refresh_minutes",
                         name="Go Gauge Nutzung Refresh (Minuten)",
                         min_value=1, max_value=1440)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.usage_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.usage_minutes = int(value)
        self.coordinator.recalculate_interval()
        _persist(self.hass, self._entry, self.coordinator,
                 usage_refresh_minutes=int(value))


class ModelsRefreshMinutesNumber(_SettingNumber):
    """Modell-Refresh-Intervall in Minuten (1..1440)."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_mode = "box"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix="models_refresh_minutes",
                         name="Go Gauge Modelle Refresh (Minuten)",
                         min_value=1, max_value=1440)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.models_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.models_minutes = int(value)
        self.coordinator.recalculate_interval()
        _persist(self.hass, self._entry, self.coordinator,
                 models_refresh_minutes=int(value))
