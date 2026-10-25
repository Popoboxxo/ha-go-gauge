"""Shared entity bases + persistence helpers for Go Gauge.

Two separate bases since 2.0.0 (plan §2.8):

* :class:`GoGaugeEntityBase` - workspace entities (usage windows). The device is
  the workspace's own device, identified by the config entry id.
* :class:`GoGaugeAccountEntityBase` - account entities (model catalog, API
  reachability, account settings). The device is the single
  ``(DOMAIN, "account")`` device and is shared by every account entry.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ACCOUNT_DEVICE_IDENTIFIER,
    DOMAIN,
    ENTITY_NAME_ACCOUNT,
    MANUFACTURER,
    MODEL,
)
from .coordinator import GoGaugeCoordinator

if TYPE_CHECKING:  # no runtime import: entity.py must stay loadable on its own
    from .account import AccountCoordinator

_LOGGER = logging.getLogger(__name__)


class GoGaugeEntityBase(CoordinatorEntity):
    """Common device-info wiring + entry reference for all workspace entities.

    ``has_entity_name`` is enabled globally: entity display names are resolved
    from ``translation_key`` lookups (strings.json / translations/*.json) and
    HA prefixes the device name automatically (e.g. "Go Gauge <workspace>").
    No entity sets ``_attr_name`` anymore - a set ``_attr_name`` would
    short-circuit the translation lookup.
    """

    _attr_has_entity_name = True

    # Narrow the base ``CoordinatorEntity.coordinator`` type so platform
    # subclasses can access Go Gauge-specific attributes without casts.
    coordinator: GoGaugeCoordinator

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


class GoGaugeAccountEntityBase(CoordinatorEntity):
    """Base for the workspace-independent account entities (2.0.0).

    Deliberately its OWN base instead of a ``GoGaugeEntityBase`` subclass: the
    account entities belong to the account ConfigEntry, use the fixed account
    device and never look up a workspace. Sharing the workspace base would drag
    the workspace device_info (entry_id identifier) and the ``_ws`` helper into
    entities that have neither (plan §2.8).
    """

    _attr_has_entity_name = True

    coordinator: AccountCoordinator

    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)},
            "name": ENTITY_NAME_ACCOUNT,
            "manufacturer": MANUFACTURER,
            "model": MODEL,
        }

    @property
    def available(self) -> bool:
        """Stay visible even without data.

        HA-conform: ``available = True`` plus a ``None`` state means "unknown",
        which is exactly what an account entity without (fresh) evidence should
        show. Inheriting ``CoordinatorEntity.available`` would render the entity
        ``unavailable`` and drop its state/attributes - including the
        reachability reason this integration exists to surface. ``None`` is the
        unknown state, never a fake value.
        """
        return True


def persist_options(hass: HomeAssistant, entry: ConfigEntry,
                    coordinator: GoGaugeCoordinator, **changes: Any) -> bool:
    """Runtime-Entity-Aenderungen persistent speichern OHNE Entry-Reload.

    Die Entities haben den Coordinator bereits live umgestellt; der
    Update-Listener sieht das _skip_reload-Flag und laesst ihn laufen.

    HA feuert die Update-Listener nur bei einer ECHTEN Aenderung und
    ``async_update_entry`` gibt genau das als bool zurueck. Wird derselbe Wert
    erneut gesetzt (Switch auf bereits-an, Number auf den gleichen Wert),
    bleibt sonst das Flag gesetzt und die NAECHSTE echte Options-Aenderung
    (z. B. Options-Flow) wuerde ohne Reload verworfen. Daher das Flag sofort
    zuruecksetzen, wenn nichts geaendert wurde - bei ``True`` laeuft der
    (asynchron eingeplante) Listener und setzt es selbst zurueck.
    """
    opts = {**entry.options, **changes}
    coordinator._skip_reload = True
    changed = hass.config_entries.async_update_entry(entry, options=opts)
    if not changed:
        coordinator._skip_reload = False
    return changed


def persist_account_options(
    hass: HomeAssistant, entry: ConfigEntry, **changes: Any
) -> bool:
    """Persist account options - WITHOUT a reload flag.

    The account entry has NO update listener on purpose (plan §6.4): its
    runtime entities apply their setters live via the coordinator, and
    ``hass.config_entries.async_update_entry`` therefore must not trigger a
    reload. That is why there is no ``_skip_reload`` counterpart here - the flag
    only exists for the workspace coordinator whose entry DOES have a listener.
    """
    options = {**entry.options, **changes}
    return bool(hass.config_entries.async_update_entry(entry, options=options))
