"""Go Gauge HA - integration setup (direct opencode.ai access)."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_USAGE_REFRESH_MINUTES, DOMAIN
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "number"]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config-entry versions to current (VERSION = 3).

    v1/v2 (host/port-basiert, Monitor-Ara)  -> v3 (token-basiert, direkt API).
    Ohne diese Funktion wuerde HA 'Migration handler not found' werfen und
    den Eintrag nicht laden - genau das war der Fehler beim v0.3-Update.
    """
    from .const import DOMAIN  # local import avoids circulars during migration

    if entry.version > 3:
        # Zukunftsfaelle: downgraded install -> nicht anfassen
        return False

    _LOGGER.info("Go Gauge: migriere Config-Entry von Version %s auf 3", entry.version)

    data = {**entry.data}
    options = {**entry.options}

    if entry.version < 3:
        # v1/v2 hatten host/port + ggf. legacy scan_interval. Tokens sind dort
        # nicht vorhanden -> leer hinterlegen (User traegt sie im Dialog nach).
        # Alte Schluessel entfernen, damit kein Muell uebrig bleibt.
        tokens = list(entry.data.get("tokens", []))
        data = {"tokens": tokens}
        # Legacy-Optionen auf die neuen Zyklen mappen
        old_interval = entry.options.get("scan_interval")
        if old_interval and "usage_refresh_minutes" not in options:
            options[CONF_USAGE_REFRESH_MINUTES] = max(1, int(old_interval) // 60)

    hass.config_entries.async_update_entry(entry, data=data, options=options)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Go Gauge from a config entry."""
    tokens: list[str] = list(entry.data.get("tokens", []))

    coordinator = GoGaugeCoordinator(hass, tokens, dict(entry.options))
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload on options change - ABER NICHT wenn nur Runtime-Entities
    (Switches/Numbers) persistiert haben; die haben den Coordinator schon
    live umgestellt und der Reload wuerde die Entities kurz killen."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and getattr(coordinator, "_skip_reload", False):
        coordinator._skip_reload = False
        _LOGGER.debug("Go Gauge: Options-Update aus Runtime-Entity - kein Reload")
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
