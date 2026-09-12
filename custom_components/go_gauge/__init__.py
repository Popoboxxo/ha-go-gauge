"""Go Gauge HA - integration setup (direct opencode.ai access).

Multi-Workspace-Design (Daniel v0.5): EINE Instanz pro Workspace
(sprechender Name + ein Token). Der Modell-Katalog ist workspace-
unabhaengig und wird NUR von der ersten Instanz als Entities angelegt;
weitere Instanzen teilen ihn (hass.data[DOMAIN]['_catalog_owner']).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_USAGE_REFRESH_MINUTES,
    DOMAIN,
    migrate_entity_id,
    migrate_entity_name,
    token_unique_id,
)
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "button", "switch", "number"]


def _entity_name_migration_callback(registry_entry: Any) -> dict[str, str] | None:
    """Registry-entry callback translating a legacy German ``original_name``.

    Returns ``{"original_name": <english>}`` only when the name actually
    changes, else ``None``. ``unique_id`` is never part of the returned dict -
    the entity-name migration must not touch stable IDs.
    """
    original = getattr(registry_entry, "original_name", None)
    migrated = migrate_entity_name(original)
    if migrated is None:
        return None
    return {"original_name": migrated}


async def _async_migrate_entity_names(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Translate already-registered German entity names to English (v6).

    Delegates to HA's entity registry so the persisted ``original_name`` values
    are rewritten without touching ``unique_id``. Defensive: a missing registry
    or an already-migrated instance must never abort the config-entry migration.
    """
    try:
        from homeassistant.helpers.entity_registry import async_migrate_entries

        await async_migrate_entries(
            hass, entry.entry_id, _entity_name_migration_callback
        )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Go Gauge: Entity-Name-Migration fuer %s fehlgeschlagen (%s) - "
            "Namen bleiben unveraendert", entry.entry_id, err)


def _make_entity_id_migration_callback(
    hass: HomeAssistant,
    skipped: list[tuple[str, str]] | None = None,
) -> Callable[[Any], dict[str, str] | None]:
    """Build the v7 registry callback that renames legacy German entity_ids.

    A closure is required because the collision check needs the live entity
    registry and state machine. The callback returns
    ``{"new_entity_id": <english slug>}`` only when the current ``entity_id`` is
    a known legacy German slug AND the target id is free. ``unique_id`` is never
    part of the returned dict - the iron rule is that only the slug changes.

    ``skipped`` (if given) collects ``(current, target)`` pairs whose rename was
    suppressed, so the caller can log an incomplete pass.

    Collision handling mirrors ``EntityRegistry._entity_id_available``: the
    target must be neither registry-registered nor present/reserved in the state
    machine. Any occupant would make HA's ``async_update_entity`` raise
    ``ValueError: Entity with this ID is already registered`` and abort the whole
    migration pass, so the rename is skipped and only a warning is logged instead
    of ever raising.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    skipped_pairs = skipped if skipped is not None else []

    def _callback(registry_entry: Any) -> dict[str, str] | None:
        current = getattr(registry_entry, "entity_id", None)
        target = migrate_entity_id(current)
        if target is None:
            return None
        if registry.async_get(target) is not None or not hass.states.async_available(target):
            skipped_pairs.append((current, target))
            _LOGGER.warning(
                "Go Gauge: entity_id %s -> %s ist bereits belegt - Rename "
                "uebersprungen (unique_id bleibt stabil)", current, target)
            return None
        return {"new_entity_id": target}

    return _callback


async def _async_migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Rename already-registered German entity_id slugs to English (v7).

    Delegates to HA's entity registry so the persisted ``entity_id`` values are
    rewritten while ``unique_id`` stays untouched. Returns ``True`` when the
    pass completed without an exception and ``False`` when it failed - the v7
    caller must then NOT advance ``entry.version`` so HA retries on the next
    start. Collisions are not failures: they are skipped and reported as a
    warning with count and pairs, because retrying cannot free an occupied id.
    """
    skipped: list[tuple[str, str]] = []
    try:
        from homeassistant.helpers.entity_registry import async_migrate_entries

        callback = _make_entity_id_migration_callback(hass, skipped)
        await async_migrate_entries(hass, entry.entry_id, callback)
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Go Gauge: Entity-ID-Migration fuer %s fehlgeschlagen (%s) - "
            "entity_ids bleiben unveraendert, Migration wird beim naechsten "
            "Start erneut versucht", entry.entry_id, err)
        return False

    if skipped:
        _LOGGER.warning(
            "Go Gauge: Entity-ID-Migration fuer %s unvollstaendig - %d Rename(s) "
            "wegen belegtem Ziel uebersprungen: %s",
            entry.entry_id,
            len(skipped),
            ", ".join(f"{current} -> {target}" for current, target in skipped),
        )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config-entry versions to current (VERSION = 7).

    v1/v2: Monitor-Ara (host/port) -> token-basiert (Token-Liste uebernommen).
    v3:    Token-Liste uebernommen, Scan-Intervall (Sekunden) ->
           usage_refresh_minutes (Minuten).
    v4:    Multi-Token-Liste -> EIN Workspace pro Instanz: erster Token bleibt,
           Name = 'WS <slot>' (User benennt um).
    v5:    ConfigEntry.unique_id war ein 16-Zeichen-Klartext-Fragment des
           Tokens -> SHA-256-Hash, damit kein Token-Teil in HA-Storage /
           Diagnostics persistiert wird (AUDIT-2026-09-04).
    v6:    Bereits registrierte Entities von deutschen auf englische
           original_name-Werte umstellen; unique_id bleibt stabil.
    v7:    Bereits registrierte Entities von deutschen auf englische
           entity_id-Slugs umstellen (via Entity-Registry ``new_entity_id``);
           unique_id bleibt stabil, Kollisionen werden uebersprungen.
    """
    if entry.version > 7:
        return False

    _LOGGER.info(
        "Go Gauge: migriere Config-Entry %s von Version %s auf 7",
        entry.entry_id, entry.version,
    )

    data = {**entry.data}
    options = {**entry.options}
    new_unique_id = entry.unique_id
    # HA forbids direct ``entry.version = N`` assignment since 2026.9:
    # ConfigEntry.__setattr__ raises AttributeError("version cannot be changed
    # directly, use async_update_entry instead"). Track the target version in a
    # local cursor and persist data/options/unique_id/version together in ONE
    # ``async_update_entry`` call at the end. That keeps the write atomic (no
    # intermediate version persisted with not-yet-migrated data) and still sets
    # data/options/unique_id exactly once. ``version=`` is a keyword-only
    # parameter of ``ConfigEntries.async_update_entry`` - verified present since
    # HA 2024.6.0 up to 2026.9.1.
    new_version = entry.version

    if new_version < 3:
        # Monitor-Ara: nur Tokens uebernehmen falls vorhanden
        tokens = list(entry.data.get("tokens", []))
        data = {"tokens": tokens}
        old_interval = entry.options.get("scan_interval")
        if old_interval and CONF_USAGE_REFRESH_MINUTES not in options:
            options[CONF_USAGE_REFRESH_MINUTES] = max(1, int(old_interval) // 60)
        new_version = 3  # durch die naechste Stufe laufen lassen

    if new_version < 4:
        # Multi-Token -> Single-Workspace: ERSTEN Token behalten.
        # (Weitere Tokens: der User legt je eine neue Instanz an.)
        tokens = data.get("tokens", [])
        first = tokens[0] if tokens else ""
        data = {
            "token": first,
            "workspace_name": data.get("workspace_name", ""),
        }
        new_version = 4

    if new_version < 5:
        # Klartext-Token-Fragment in der unique_id durch SHA-256-Hash ersetzen.
        # Aus dem gespeicherten Token neu berechnen, damit die ID exakt der
        # entspricht, die der Config-Flow jetzt erzeugt. Idempotent/defensiv:
        # ohne Token bleibt die bestehende unique_id unveraendert (keine
        # Exception); bei erneutem Lauf greift diese Stufe nicht mehr.
        token = str(data.get("token") or "")
        if token:
            new_unique_id = token_unique_id(token)
        new_version = 5

    if new_version < 6:
        # Bereits registrierte Entities heissen noch deutsch -> original_name
        # uebersetzen; die unique_id bleibt unangetastet (eiserne Regel).
        await _async_migrate_entity_names(hass, entry)
        new_version = 6

    if new_version < 7:
        # Bereits registrierte Entities haben noch deutsche entity_id-Slugs ->
        # ueber die Entity-Registry auf Englisch umstellen. unique_id bleibt
        # unangetastet; belegte Ziel-IDs werden uebersprungen (kein ValueError).
        # Nur bei erfolgreichem Durchlauf die Version anheben: schlaegt der
        # Registry-Pass fehl, bleibt die Version stehen und HA versucht ihn beim
        # naechsten Start erneut (kein stilles Weiterziehen bei Teilfehler).
        if await _async_migrate_entity_ids(hass, entry):
            new_version = 7
        else:
            _LOGGER.warning(
                "Go Gauge: Entity-ID-Migration fuer %s fehlgeschlagen - "
                "Config-Entry bleibt auf Version %s, HA versucht die Migration "
                "beim naechsten Start erneut", entry.entry_id, new_version,
            )

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        unique_id=new_unique_id,
        version=new_version,
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
