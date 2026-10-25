"""Go Gauge HA - number platform (runtime-editable settings).

2.0.0 split (plan §2.12):

* workspace -> warning threshold, pace red limit and the usage refresh interval;
* account   -> ``AccountModelsRefreshMinutesNumber`` (global catalog interval), live
  effect via ``AccountCoordinator.set_fetch_interval`` + persisted options.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .account_logic import canonical_unique_id
from .const import (
    ACCOUNT_MODELS_REFRESH_MAX,
    ACCOUNT_MODELS_REFRESH_MIN,
    CONF_MODELS_REFRESH_MINUTES,
    DEFAULT_PACE_RED_PERCENT,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
    ENTRY_KIND_ACCOUNT,
    conf_entry_kind,
)
from .coordinator import GoGaugeCoordinator
from .entity import (
    GoGaugeAccountEntityBase,
    GoGaugeEntityBase,
    persist_account_options,
    persist_options,
)

if TYPE_CHECKING:  # no runtime import: this module must stay loadable on its own
    from .account import AccountCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the numbers of THIS entry kind (plan §2.12)."""
    if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT:
        account_coordinator: AccountCoordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities([AccountModelsRefreshMinutesNumber(account_coordinator, entry)])
        return

    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        WarnPercentNumber(coordinator, entry),
        PaceRedPercentNumber(coordinator, entry),
        UsageRefreshMinutesNumber(coordinator, entry),
    ])


class _WorkspaceSettingNumber(GoGaugeEntityBase, NumberEntity):
    """Basis fuer Zahlen-Einstellungen eines Workspaces (Box-Modus, sofort wirksam).

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


class _AccountSettingNumber(GoGaugeAccountEntityBase, NumberEntity):
    """Basis fuer die globalen Konto-Zahlen (canonical unique_id, Box-Modus).

    Separater Zweig statt eines generischen ``Any``-Basistyps: die beiden
    Coordinator-Typen haben unterschiedliche Setter und die Konto-Entities
    gehoeren zu einem anderen Device (plan §2.12).
    """

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry, *,
                 unique_id: str,
                 min_value: int, max_value: int) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = unique_id
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = 1


class WarnPercentNumber(_WorkspaceSettingNumber):
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


class PaceRedPercentNumber(_WorkspaceSettingNumber):
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


class UsageRefreshMinutesNumber(_WorkspaceSettingNumber):
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


class AccountModelsRefreshMinutesNumber(_AccountSettingNumber):
    """Globales Modell-Refresh-Intervall in Minuten (1..1440, Default 60).

    ``native_value`` is the BASE interval (not the backoff-extended effective
    one): the user configures the base, the backoff is an internal reaction to
    API failures (plan §6.1/§6.3).
    """

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_translation_key = "models_refresh_min"

    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry,
                         unique_id=canonical_unique_id("models_refresh_minutes"),
                         min_value=ACCOUNT_MODELS_REFRESH_MIN,
                         max_value=ACCOUNT_MODELS_REFRESH_MAX)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.base_interval_minutes

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_fetch_interval(int(value))
        persist_account_options(
            self.hass,
            self._entry,
            **{CONF_MODELS_REFRESH_MINUTES: self.coordinator.base_interval_minutes},
        )
        self.async_write_ha_state()
