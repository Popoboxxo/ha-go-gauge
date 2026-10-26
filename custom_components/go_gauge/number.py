"""Go Gauge HA - number platform (runtime-editable settings).

2.0.0 split (plan §2.12):

* workspace -> warning threshold, pace red limit, the usage refresh interval and
  (since 2.1.0) the three burn-rate lookback sliders;
* account   -> ``AccountModelsRefreshMinutesNumber`` (global catalog interval), live
  effect via ``AccountCoordinator.set_fetch_interval`` + persisted options.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACCOUNT_UNIQUE_ID_PREFIX,
    BURN_LOOKBACK_WINDOWS,
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
        BurnLookback5hNumber(coordinator, entry),
        BurnLookbackWeekNumber(coordinator, entry),
        BurnLookbackMonthNumber(coordinator, entry),
    ])


class _WorkspaceSettingNumber(GoGaugeEntityBase, NumberEntity):
    """Basis fuer Zahlen-Einstellungen eines Workspaces (Box-Modus, sofort wirksam).

    Der Anzeigename kommt aus ``translation_key`` (Klassen-Attribut der
    jeweiligen Unterklasse); HA stellt den Geraetenamen voran.
    """

    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

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
    _attr_entity_category = EntityCategory.CONFIG

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


class _BurnLookbackNumber(_WorkspaceSettingNumber):
    """Basis fuer die drei Burn-Lookback-Slider eines Workspaces (2.1.0).

    ``EntityCategory.CONFIG`` und ``NumberMode.SLIDER``: die Lookbacks sind
    Nutzerkonfiguration, keine Diagnose (plan D7/D20). ``_window`` ist das
    Fenster-Label aus ``BURN_LOOKBACK_WINDOWS``; die konkrete Unterklasse
    liefert Unique-Suffix und Grenzen als Literale.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:history"
    _attr_native_unit_of_measurement = "min"

    _window: str

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 unique_suffix: str, min_value: int, max_value: int) -> None:
        super().__init__(coordinator, entry,
                         unique_suffix=unique_suffix,
                         min_value=min_value,
                         max_value=max_value)
        # Schrittweite kommt aus der einzigen Quelle (const), nicht als Literal:
        # sie ist fuer die Bedienung relevant, aber kein Kanon-Merkmal.
        # (The concrete subclasses additionally declare equivalent literal
        # ``_attr_native_step`` values for the static canon parser; both agree.)
        self._attr_native_step = BURN_LOOKBACK_WINDOWS[self._window]["step"]

    @property
    def native_value(self) -> float | None:
        spec = BURN_LOOKBACK_WINDOWS[self._window]
        return self.coordinator.burn_lookback_minutes.get(
            self._window, spec["default"])

    async def async_set_native_value(self, value: float) -> None:
        minutes = int(value)
        spec = BURN_LOOKBACK_WINDOWS[self._window]
        self.coordinator.set_burn_lookback(self._window, minutes)
        # Persist the NORMALISED value read back from the coordinator, never the
        # raw input: ``set_burn_lookback`` clamps to the window range, so storing
        # the raw value would let the persisted option drift from the live one
        # on an out-of-range service call.
        normalized = self.coordinator.burn_lookback_minutes.get(
            self._window, spec["default"])
        persist_options(self.hass, self._entry, self.coordinator,
                        **{spec["option"]: normalized})
        # Alle Burn-Rate-Sensoren dieses Workspaces sofort neu rendern.
        self.coordinator.async_update_listeners()
        self.async_write_ha_state()


class BurnLookback5hNumber(_BurnLookbackNumber):
    """5h-Burn-Lookback in Minuten (15..300, Default 120, Schritt 5)."""

    _attr_translation_key = "burn_lookback_5h"
    _window = "5h"
    _attr_native_min_value = 15
    _attr_native_max_value = 300
    # Canon metadata for the static checker - NOT an HA ``NumberEntity`` attribute.
    _attr_native_default_value = 120
    _attr_native_step = 5

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        # Grenzen und Default als Literale (Spiegel von
        # BURN_LOOKBACK_WINDOWS["5h"]): der statische Canon-Checker parst Ziffern
        # und loest keine Dict-Lookups auf (``native_value`` nutzt einen).
        super().__init__(coordinator, entry,
                         unique_suffix="burn_lookback_5h_minutes",
                         min_value=15, max_value=300)


class BurnLookbackWeekNumber(_BurnLookbackNumber):
    """Weekly-Burn-Lookback in Minuten (60..10080, Default 720, Schritt 60)."""

    _attr_translation_key = "burn_lookback_week"
    _window = "week"
    _attr_native_min_value = 60
    _attr_native_max_value = 10080
    _attr_native_default_value = 720
    _attr_native_step = 60

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        # Literale aus BURN_LOOKBACK_WINDOWS["week"] (Canon-Checker, s. o.).
        super().__init__(coordinator, entry,
                         unique_suffix="burn_lookback_week_minutes",
                         min_value=60, max_value=10080)


class BurnLookbackMonthNumber(_BurnLookbackNumber):
    """Monthly-Burn-Lookback in Minuten (60..10080, Default 2880, Schritt 60)."""

    _attr_translation_key = "burn_lookback_month"
    _window = "month"
    _attr_native_min_value = 60
    _attr_native_max_value = 10080
    _attr_native_default_value = 2880
    _attr_native_step = 60

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        # Literale aus BURN_LOOKBACK_WINDOWS["month"] (Canon-Checker, s. o.).
        super().__init__(coordinator, entry,
                         unique_suffix="burn_lookback_month_minutes",
                         min_value=60, max_value=10080)


class AccountModelsRefreshMinutesNumber(_AccountSettingNumber):
    """Globales Modell-Refresh-Intervall in Minuten (1..1440, Default 60).

    ``native_value`` is the BASE interval (not the backoff-extended effective
    one): the user configures the base, the backoff is an internal reaction to
    API failures (plan §6.1/§6.3).
    """

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"
    _attr_translation_key = "models_refresh_min"
    # Literal template (not ``canonical_unique_id``): the static canon checker
    # parses ``_attr_unique_id`` textually and cannot resolve helper calls.
    # The resulting value is unchanged (``account_models_refresh_minutes``).
    _attr_unique_id = f"{ACCOUNT_UNIQUE_ID_PREFIX}models_refresh_minutes"

    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry) -> None:
        # Literal bounds as well - same 1..1440 range, statically parseable.
        super().__init__(coordinator, entry,
                         unique_id=self._attr_unique_id,
                         min_value=1, max_value=1440)

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
