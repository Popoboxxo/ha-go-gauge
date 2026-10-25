"""Go Gauge HA - switch platform (auto-update toggles).

2.0.0 split (plan §2.12):

* workspace -> ONLY ``AutoUpdateUsageSwitch`` (usage cycle);
* account   -> ``AccountAutoUpdateModelsSwitch`` (global catalog auto-refresh), live
  effect without a reload: ``AccountCoordinator.set_auto_fetch`` +
  ``persist_account_options`` (the account entry has no update listener).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACCOUNT_UNIQUE_ID_PREFIX,
    CONF_AUTO_UPDATE_MODELS,
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
    """Create the switches of THIS entry kind (plan §2.12)."""
    if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT:
        account_coordinator: AccountCoordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities([AccountAutoUpdateModelsSwitch(account_coordinator, entry)])
        return

    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AutoUpdateUsageSwitch(coordinator, entry)])


class AutoUpdateUsageSwitch(GoGaugeEntityBase, SwitchEntity):
    """Auto-Refresh fuer Nutzungsdaten ein/aus (workspace)."""

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


class AccountAutoUpdateModelsSwitch(GoGaugeAccountEntityBase, SwitchEntity):
    """Global auto-refresh of the model catalog (account entry, E4).

    No ``recalculate_interval()`` and no reload: the account coordinator owns a
    separate fetch timer that ``set_auto_fetch`` re-arms immediately, and the
    entry deliberately has no update listener (plan §6.4).
    """

    _attr_icon = "mdi:autorenew"
    _attr_translation_key = "auto_update_models"

    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{ACCOUNT_UNIQUE_ID_PREFIX}auto_update_models"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.auto_fetch

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._apply(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._apply(False)

    def _apply(self, enabled: bool) -> None:
        """Apply the toggle live and persist it WITHOUT a reload."""
        self.coordinator.set_auto_fetch(enabled)
        persist_account_options(
            self.hass, self._entry, **{CONF_AUTO_UPDATE_MODELS: enabled}
        )
        self.async_write_ha_state()
