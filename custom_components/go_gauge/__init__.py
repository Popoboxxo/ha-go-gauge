"""Go Gauge HA - integration setup (direct opencode.ai access).

Multi-Workspace-Design (Daniel v0.5): EINE Instanz pro Workspace
(sprechender Name + ein Token). Der Modell-Katalog ist workspace-
unabhaengig und wird NUR von der ersten Instanz als Entities angelegt;
weitere Instanzen teilen ihn (hass.data[DOMAIN]['_catalog_owner']).
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_USAGE_REFRESH_MINUTES,
    DOMAIN,
    token_unique_id,
)
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "number"]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config-entry versions to current (VERSION = 5).

    v1/v2: Monitor-Ara (host/port) -> token-basiert
    v3:    Multi-Token-Liste       -> EIN Workspace pro Instanz:
           erster Token bleibt, Name = 'WS <slot>' (User benennt um).
    v4:    ConfigEntry.unique_id war ein 16-Zeichen-Klartext-Fragment des
           Tokens -> SHA-256-Hash, damit kein Token-Teil in HA-Storage /
           Diagnostics persistiert wird (AUDIT-2026-09-04).
    """
    if entry.version > 5:
        return False

    _LOGGER.info(
        "Go Gauge: migriere Config-Entry %s von Version %s auf 5",
        entry.entry_id, entry.version,
    )

    data = {**entry.data}
    options = {**entry.options}
    new_unique_id = entry.unique_id

    if entry.version < 3:
        # Monitor-Ara: nur Tokens uebernehmen falls vorhanden
        tokens = list(entry.data.get("tokens", []))
        data = {"tokens": tokens}
        old_interval = entry.options.get("scan_interval")
        if old_interval and CONF_USAGE_REFRESH_MINUTES not in options:
            options[CONF_USAGE_REFRESH_MINUTES] = max(1, int(old_interval) // 60)
        entry.version = 3  # durch die naechste Stufe laufen lassen

    if entry.version < 4:
        # Multi-Token -> Single-Workspace: ERSTEN Token behalten.
        # (Weitere Tokens: der User legt je eine neue Instanz an.)
        tokens = data.get("tokens", [])
        first = tokens[0] if tokens else ""
        data = {
            "token": first,
            "workspace_name": data.get("workspace_name", ""),
        }
        entry.version = 4

    if entry.version < 5:
        # Klartext-Token-Fragment in der unique_id durch SHA-256-Hash ersetzen.
        # Aus dem gespeicherten Token neu berechnen, damit die ID exakt der
        # entspricht, die der Config-Flow jetzt erzeugt. Idempotent/defensiv:
        # ohne Token bleibt die bestehende unique_id unveraendert (keine
        # Exception); bei erneutem Lauf greift diese Stufe nicht mehr.
        token = str(data.get("token") or "")
        if token:
            new_unique_id = token_unique_id(token)
        entry.version = 5

    hass.config_entries.async_update_entry(
        entry, data=data, options=options, unique_id=new_unique_id
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Go Gauge workspace instance."""
    token: str = str(entry.data.get("token") or "")
    ws_name: str = str(entry.data.get("workspace_name") or "").strip()

    coordinator = GoGaugeCoordinator(hass, [token] if token else [], dict(entry.options))
    coordinator.ws_name = ws_name
    # First refresh TOLERANT: Wenn der erste Abruf fehlschlaegt (Cloudflare/
    # Netz beim HA-Start), darf das Setup NICHT abbrechen - sonst bleiben alle
    # Entities "nicht verfuegbar" bis zum naechsten Neustart. HA's Coordinator
    # retryt automatisch im Hintergrund.
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Go Gauge %s: erster Abruf fehlgeschlagen (%s) - Setup laeuft weiter, "
            "HA retryt automatisch", ws_name or "WS", err)
        # data initialisieren, damit Sensoren nicht auf None laufen
        coordinator.async_set_updated_data({
            "fetched_at": None,
            "last_usage_fetch": None,
            "last_models_fetch": None,
            "auto_usage": coordinator.auto_usage,
            "auto_models": coordinator.auto_models,
            "usage_refresh_minutes": coordinator.usage_minutes,
            "models_refresh_minutes": coordinator.models_minutes,
            "workspaces": [],
            "models_block": None,
        })

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Katalog-Ownership: erste geladene Instanz legt die workspace-unabhaengigen
    # Entities an (Modell-Katalog, Live-Count, Guenstigstes, Free, API erreichbar,
    # Warnschwelle/Intervall-Settings). Weitere Instanzen: nur Workspace-Sensoren.
    is_catalog_owner = "_catalog_owner" not in hass.data[DOMAIN]
    coordinator.is_catalog_owner = is_catalog_owner
    if is_catalog_owner:
        hass.data[DOMAIN]["_catalog_owner"] = entry.entry_id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload on options change - ABER NICHT wenn nur Runtime-Entities
    persistiert haben (die haben den Coordinator live umgestellt)."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None and getattr(coordinator, "_skip_reload", False):
        coordinator._skip_reload = False
        _LOGGER.debug("Go Gauge: Options-Update aus Runtime-Entity - kein Reload")
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        was_owner = domain_data.get("_catalog_owner") == entry.entry_id
        domain_data.pop(entry.entry_id, None)
        if was_owner:
            domain_data.pop("_catalog_owner", None)
            # Naechste noch geladene Instanz wird neuer Owner (Entities wandern)
            for eid, coord in domain_data.items():
                if isinstance(coord, GoGaugeCoordinator):
                    coord.is_catalog_owner = True
                    domain_data["_catalog_owner"] = eid
                    break
    return unload_ok
