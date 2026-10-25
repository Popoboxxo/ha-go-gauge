"""Go Gauge HA - integration setup (direct opencode.ai access).

Multi-Workspace-Design (Daniel v0.5): EINE Workspace-Instanz pro Token
(sprechender Name + ein Token). Workspace-Instanzen besitzen ausschliesslich
die Nutzungs-Entities.

Seit 2.0.0 gibt es zusaetzlich genau EINE workspace-unabhaengige Konto-Entry
(Domain ``go_gauge``, ``entry_kind == "account"``): sie besitzt den
Modell-Katalog, die API-Reachability und die globalen Modell-Settings. Der
frueherere "erste Instanz ist Catalog-Owner"-Mechanismus ist ersatzlos
entfallen (plan §2.6/§3.3) - es gibt keinen Besitzerwechsel mehr.
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .account import (
    async_ensure_account_entry,
    async_remove_account_entry,
    async_setup_account_entry,
    async_unload_account_entry,
)
from .const import (
    ACCOUNT_PLATFORMS,
    CONF_ENTRY_KIND,
    CONF_USAGE_REFRESH_MINUTES,
    DOMAIN,
    ENTRY_KIND_ACCOUNT,
    ENTRY_KIND_WORKSPACE,
    conf_entry_kind,
    migrate_entity_id,
    migrate_entity_name,
    token_unique_id,
)
from .coordinator import GoGaugeCoordinator

_LOGGER = logging.getLogger(__name__)

# ONE source for both entry kinds: a workspace entry and the account entry
# forward/unload the SAME platform set, so the list must never diverge
# (review fix m8). ``ACCOUNT_PLATFORMS`` lives in ``const.py`` and is the
# authoritative definition; this is only the workspace-facing alias.
PLATFORMS = list(ACCOUNT_PLATFORMS)


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
        if not isinstance(current, str):
            return None
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


async def _async_ensure_account_task(hass: HomeAssistant) -> None:
    """Run the account ensure as a background task body - never raises.

    Split out of :func:`async_setup` so the task itself cannot produce an
    unretrieved task exception: ``async_ensure_account_entry`` is already fully
    guarded, this is the second net for the "nobody awaits this task" case.
    """
    try:
        await async_ensure_account_entry(hass)
    except Exception as err:  # noqa: BLE001 - background task must never raise
        _LOGGER.warning(
            "Go Gauge: Konto-Entry beim HA-Start nicht sichergestellt (%s) - "
            "Integration laeuft weiter", err)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Domain-level setup: schedule the account-entry ensure (2.0.0, E2).

    HA-SELF-AWAIT HAZARD - the ensure must NOT be awaited here. While the
    domain is still being set up it is not listed in ``hass.config.components``,
    and ``async_ensure_account_entry`` may await ``flow.async_init`` /
    ``config_entries.async_setup``. That triggers the setup of an entry of THIS
    domain, which in turn waits for the component setup that is currently
    awaiting the ensure: a circular await that stalls the domain setup until
    HA's 300 s timeout and then fails it.

    The ensure therefore runs as its own task: ``hass.async_create_task`` starts
    it immediately after the current callback yields, which is after the domain
    setup has completed - it can never block (or be blocked by) this setup.
    :func:`_async_ensure_account_task` keeps an exception inside the task.

    The domain setup itself can never fail because of the account entry (plan
    §2.6, §3.1, §7.3: "Domain-``async_setup`` schlaegt fehl -> return True
    immer"); only a failing ``async_create_task`` (shutdown) is logged.
    """
    ensure_coro: Any = None
    try:
        ensure_coro = _async_ensure_account_task(hass)
        hass.async_create_task(ensure_coro, name=f"{DOMAIN}_ensure_account")
    except Exception as err:  # noqa: BLE001 - domain setup must never fail here
        # A coroutine that was never scheduled must be closed explicitly,
        # otherwise GC reports "coroutine was never awaited".
        if ensure_coro is not None:
            ensure_coro.close()
        _LOGGER.warning(
            "Go Gauge: Konto-Entry beim HA-Start nicht eingeplant (%s) - "
            "Integration laeuft weiter", err)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config-entry versions to current (VERSION = 8).

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
    v8:    markiert jede Entry explizit mit ``entry_kind`` (workspace);
           KEINE Registry-/Netz-Arbeit - O(1). Der Marker macht die Entry-Art
           abfragbar, ohne auf fehlende Schluessel zu raten (die Konto-Entry
           traegt ``account``, neue Workspaces schreiben ihn im Config-Flow).
    """
    if entry.version > 8:
        return False

    _LOGGER.info(
        "Go Gauge: migriere Config-Entry %s von Version %s auf 8",
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

    if new_version >= 7:
        # v8: jede Entry explizit als Workspace markieren. Bewusst KEIN
        # Registry-/Netz-Zugriff (B4) - reines Setzen eines Data-Keys, also
        # O(1). Die Bedingung ">= 7" (statt "< 8") stellt sicher, dass ein
        # GESCHEITERTER v7-Pass (new_version bleibt 6) NICHT auf 8 springt:
        # sonst waere die Entity-ID-Migration stillschweigend endgueltig
        # uebersprungen. ``setdefault`` haelt den Schritt idempotent und
        # respektiert eine bereits gesetzte (kostueme) Markierung.
        data.setdefault(CONF_ENTRY_KIND, ENTRY_KIND_WORKSPACE)
        new_version = 8

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        unique_id=new_unique_id,
        version=new_version,
    )
    return True


def _usage_only_fallback_snapshot(coordinator: Any) -> dict[str, Any]:
    """Return the tolerant-path usage snapshot (exact key set, plan §2.7/M3).

    Shared by the transient-error path and the auth-error path so the
    published shape stays identical: the model catalog lives on the account
    entry, so no catalog key appears here.
    """
    return {
        "fetched_at": None,
        "last_usage_fetch": None,
        "last_usage_ok": None,
        "last_usage_attempt": None,
        "last_usage_outcome": None,
        "auto_usage": coordinator.auto_usage,
        "usage_refresh_minutes": coordinator.usage_minutes,
        "workspaces": [],
    }


async def _async_start_reauth(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Start HA's reauth flow for ``entry`` without failing the setup.

    The setup deliberately returns ``True`` for a rejected token (HTTP 401):
    the platforms must load so ``ApiStatusSensor`` can publish
    ``auth_error`` with the real cause. HA's reauth UI is started on top of
    that, so the user sees "token rejected" instead of a bare "unavailable"
    and can fix it. A reauth problem is therefore never a setup failure.

    Idempotent on purpose: HA may already have started a flow itself (it does
    when a coordinator raises ``ConfigEntryAuthFailed``), so a matching flow in
    progress means "do nothing". The progress lookup is restricted to a real
    list result - an absent/raising API or a test double hands control to the
    starter instead of silently suppressing the reauth. Every step is guarded:
    this helper must never break the already successful setup.
    """
    progress = getattr(
        getattr(hass.config_entries, "flow", None),
        "async_progress_by_handler",
        None,
    )
    if callable(progress):
        try:
            active = progress(
                DOMAIN,
                match_context={
                    "source": SOURCE_REAUTH,
                    "entry_id": entry.entry_id,
                },
            )
        except Exception as err:  # noqa: BLE001 - start defensively then
            _LOGGER.debug(
                "Go Gauge: Reauth-Fortschritt fuer %s nicht lesbar (%s) - "
                "Reauth wird trotzdem gestartet",
                entry.entry_id,
                err,
            )
        else:
            if isinstance(active, (list, tuple, set, frozenset)) and active:
                _LOGGER.debug(
                    "Go Gauge: Reauth fuer %s laeuft bereits - kein zweiter "
                    "Start",
                    entry.entry_id,
                )
                return

    starter = getattr(entry, "async_start_reauth", None)
    if not callable(starter):
        _LOGGER.debug(
            "Go Gauge: Entry %s kennt async_start_reauth nicht - Setup bleibt "
            "aktiv",
            entry.entry_id,
        )
        return

    try:
        result = starter(hass)
        if inspect.isawaitable(result):
            await result
    except Exception as err:  # noqa: BLE001 - never fail the setup for this
        _LOGGER.warning(
            "Go Gauge: Reauth-Start fuer %s fehlgeschlagen (%s) - Setup bleibt "
            "aktiv",
            entry.entry_id,
            err,
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Go Gauge entry - account or workspace (2.0.0)."""
    if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT:
        # Account entries never reach the workspace path (no token, no usage
        # coordinator): their runtime, adoption and ACCOUNT_PLATFORMS forward
        # live in account.py (plan §2.6/§3.3).
        return await async_setup_account_entry(hass, entry)

    token: str = str(entry.data.get("token") or "")
    ws_name: str = str(entry.data.get("workspace_name") or "").strip()

    coordinator = GoGaugeCoordinator(hass, [token] if token else [], dict(entry.options))
    coordinator.ws_name = ws_name
    # First refresh TOLERANT: Wenn der erste Abruf fehlschlaegt (Cloudflare/
    # Netz beim HA-Start), darf das Setup NICHT abbrechen - sonst bleiben alle
    # Entities "nicht verfuegbar" bis zum naechsten Neustart. HA's Coordinator
    # retryt automatisch im Hintergrund. Dasselbe gilt fuer einen 401: die
    # Plattformen muessen laden, damit der ApiStatusSensor den Grund
    # ("auth_error") anzeigen kann - die Reauth-UI startet danach.
    auth_failed = False
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryAuthFailed:
        # Token ungueltig/abgelaufen: NICHT weiterreichen (sonst laedt keine
        # Plattform und der Nutzer sieht nur "unavailable") und den
        # Coordinator NICHT entfernen - er wird erst unten registriert.
        auth_failed = True
        _LOGGER.warning(
            "Go Gauge %s: API-Token abgelehnt (HTTP 401) - Reauth wird "
            "gestartet, Setup laeuft weiter", ws_name or "WS")
        # Der Coordinator publiziert im Auth-Pfad bereits einen Snapshot mit
        # status="auth_error"/last_update_success=False. Den NUR dann durch
        # den Fallback ersetzen, wenn er fehlt (Test-Doubles, Fremd-Coordinator).
        if not isinstance(getattr(coordinator, "data", None), dict):
            coordinator.async_set_updated_data(
                _usage_only_fallback_snapshot(coordinator)
            )
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning(
            "Go Gauge %s: erster Abruf fehlgeschlagen (%s) - Setup laeuft weiter, "
            "HA retryt automatisch", ws_name or "WS", err)
        # data initialisieren, damit Sensoren nicht auf None laufen.
        coordinator.async_set_updated_data(
            _usage_only_fallback_snapshot(coordinator)
        )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # ONLY workspaces get the options listener: the account entry applies its
    # settings live through the coordinator setters and must never reload
    # (plan §6.4). That is also why there is no owner hand-over any more.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    if auth_failed:
        # Erst nach dem Forward: die Plattformen sind geladen (auth_error ist
        # sichtbar), HA startet die Reauth-UI im Anschluss. Ein Fehler hier
        # darf das erfolgreiche Setup nicht kippen (_async_start_reauth faengt).
        await _async_start_reauth(hass, entry)

    # 2.0.0: a workspace becoming ready is the trigger that creates/loads the
    # singleton account entry (plan §3.1/§7.3). Tolerant on purpose - a failure
    # here must never fail the workspace setup (M5); ensure is additionally
    # guarded internally, this is the second safety net.
    try:
        await async_ensure_account_entry(hass)
    except Exception as err:  # noqa: BLE001 - workspace setup must not break
        _LOGGER.warning(
            "Go Gauge %s: Konto-Entry nicht sichergestellt (%s) - "
            "Workspace-Setup laeuft weiter", ws_name or "WS", err)
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
    if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT:
        return await async_unload_account_entry(hass, entry)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # No owner marker / no ownership transfer (2.0.0): the account entry
        # owns the catalog entities regardless of which workspace loads last
        # (plan §2.6/§3.7). Only this entry's runtime object is dropped.
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle deletion of one Go Gauge entry (2.0.0).

    Account entries delegate to ``account.async_remove_account_entry`` (deletes
    the stable adoption repair issues and any ``hass.data`` leftovers). Removing
    a workspace needs no domain-level cleanup - its coordinator and registry
    object were already dropped by ``async_unload_entry``.
    """
    if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT:
        await async_remove_account_entry(hass, entry)
