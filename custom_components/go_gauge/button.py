"""Go Gauge HA - refresh buttons (workspace usage vs. account catalog).

2.0.0 split (plan §2.11):

* workspace -> ``RefreshButton`` forces the usage cycle only;
* account   -> ``AccountRefreshButton`` forces a catalog fetch via
  ``AccountCoordinator.async_fetch_now()`` (the only HTTP path of the account).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .account_logic import canonical_unique_id
from .const import DOMAIN, ENTRY_KIND_ACCOUNT, conf_entry_kind
from .coordinator import GoGaugeCoordinator
from .entity import GoGaugeAccountEntityBase, GoGaugeEntityBase

if TYPE_CHECKING:  # no runtime import: this module must stay loadable on its own
    from .account import AccountCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the refresh button of THIS entry kind (plan §2.11)."""
    if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT:
        account_coordinator: AccountCoordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities([AccountRefreshButton(account_coordinator, entry)])
        return

    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([RefreshButton(coordinator, entry)])


class RefreshButton(GoGaugeEntityBase, ButtonEntity):
    """Erzwingt sofortiges Nachladen des Usage-Zyklus.

    Der Modell-Katalog gehoert seit 2.0.0 der Konto-Entry (eigener Button), also
    setzt dieser Button NUR ``last_usage_fetch`` zurueck - er darf keinen
    Katalog-Fetch ausloesen (plan §2.11/§8.3 M3).
    """

    _attr_icon = "mdi:refresh"
    _attr_translation_key = "refresh"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_refresh"

    async def async_press(self) -> None:
        # Nur den Usage-Zeitstempel zuruecksetzen => naechster Refresh holt Usage
        coordinator = self.coordinator
        coordinator.last_usage_fetch = None
        await coordinator.async_request_refresh()


class AccountRefreshButton(GoGaugeAccountEntityBase, ButtonEntity):
    """Forces a live model-catalog fetch (account entry, plan §2.11)."""

    _attr_icon = "mdi:refresh"
    _attr_translation_key = "refresh_account"

    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = canonical_unique_id("refresh")

    async def async_press(self) -> None:
        # The ONLY account HTTP path; it never raises and re-arms the timer.
        await self.coordinator.async_fetch_now()
