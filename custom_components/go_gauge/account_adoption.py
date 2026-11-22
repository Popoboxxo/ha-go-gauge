"""Account entity/device adoption sweep (2.1.0 account split).

Extracted from :mod:`.account` in the 2.1.0 per-concern split (plan §4.3). This
module owns the ONE-TIME, idempotent registry migration that moves the five
catalog entities onto the account entry (plan §3.3, §4.1-§4.7, §8.3/§8.4):

* ``async_adopt_account_entities`` + ``AdoptionReport`` - the public entry point
  and its diagnostics-ready report of one pass,
* the registry sweep per kind, the collision/loser path, the duplicate-device
  merge and the device hand-over to the account entry,
* ``async_remove_legacy_model_settings`` - the removal of the legacy workspace
  model controls (``report.removed_settings``, plan §4.7).

The shared ``hass.data[DOMAIN]`` accessor ``_domain_data`` lives in
:mod:`.account_coordinator` and is imported from there (no own copy).
``account.py`` re-exports the public symbols of this module so
``from .account import ...`` stays stable for ``__init__`` and the platforms.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .account_coordinator import _domain_data
from .const import (
    ACCOUNT_DEVICE_IDENTIFIER,
    DATA_ADOPTION_LOCK,
    DATA_ADOPTION_REPORT,
    DOMAIN,
    ENTRY_KIND_WORKSPACE,
    conf_entry_kind,
)

_LOGGER = logging.getLogger(__name__)

# Repair-issue id suffixes that are not per-entity-kind (plan §4.4/§4.7). The
# per-kind collision ids are derived from ``account_logic.ACCOUNT_ENTITY_KINDS``
# in ``_adoption_issue_ids`` below.
_GLOBAL_ADOPTION_ISSUE_IDS: tuple[str, ...] = (
    "account_device_merged",
    "account_device_not_empty",
    "adoption_unsupported",
    "adoption_failed",
)

# Legacy workspace model controls removed on adoption (plan §4.7). The two
# canonical account ids are the new home of these settings and must survive.
_LEGACY_MODEL_SETTING_SUFFIXES: tuple[str, ...] = (
    "_auto_update_models",
    "_models_refresh_minutes",
)
_CANONICAL_MODEL_SETTING_IDS: frozenset[str] = frozenset({
    "account_auto_update_models",
    "account_models_refresh_minutes",
})


def _adoption_issue_ids() -> tuple[str, ...]:
    """Return every stable adoption repair-issue id (plan §4.4).

    Imported lazily: ``account_logic`` is a sibling module, and the offline test
    loader only resolves such imports for modules already registered in
    ``sys.modules``. A module-level import here would force every existing test
    file that loads ``account.py`` to preload it as well; the helper is the only
    consumer, so resolving it on first use keeps that coupling out of the test
    suite (and costs one import lookup per account removal).
    """
    from .account_logic import ACCOUNT_ENTITY_KINDS

    return tuple(
        f"account_entity_collision_{kind}" for kind in ACCOUNT_ENTITY_KINDS
    ) + _GLOBAL_ADOPTION_ISSUE_IDS


def _delete_adoption_issues(hass: HomeAssistant) -> None:
    """Delete all adoption repair issues of this domain - idempotent.

    Deleting a non-existent issue is a no-op in HA, so a second call (e.g.
    remove after a previous remove, or a remove without any adoption run ever
    having happened) is safe. Every single delete is guarded: the issue registry
    is HA machinery that must never be able to fail the entry removal.
    """
    for issue_id in _adoption_issue_ids():
        try:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
        except Exception as err:  # noqa: BLE001 - issue cleanup is best effort
            _LOGGER.debug(
                "Go Gauge: deleting the repair issue %s failed (%s)",
                issue_id,
                err,
            )


@dataclass
class AdoptionReport:
    """What one adoption pass changed (plan §2.4/§4.5, diagnostics source).

    Every field is a plain, JSON-safe record of ONE pass, so the diagnostics
    export can show a support case exactly what the last adoption did without
    re-reading the registries:

    * ``renamed`` - ``(old unique_id, canonical unique_id)`` per rewritten
      winner (plan §4.5 step 1),
    * ``moved`` - ``entity_id``s whose ``config_entry_id`` and/or ``device_id``
      was written (plan §4.5 steps 2-3). Collision losers are reported in
      ``collisions`` instead, so "moved" only ever names adopted winners,
    * ``device_moved`` - id of the device handed over to the account entry, or
      ``None`` when there was nothing to hand over (HA creates the device on the
      platform forward),
    * ``merged_devices`` - ids of EMPTY duplicate devices that were removed
      (plan §3.3 step 3b),
    * ``collisions`` - ``(kind, loser entity_id)`` per disabled duplicate
      (plan §4.3),
    * ``errors`` - human-readable, non-fatal problems of this pass. Non-empty
      means "try again on the next start", never "the setup failed",
    * ``removed_settings`` - legacy workspace control entities removed by this
      pass (plan §4.7: ``*_auto_update_models`` / ``*_models_refresh_minutes``,
      canonical ``account_*`` excluded). ``0`` on every idempotent re-run.
    """

    renamed: list[tuple[str, str]] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    device_moved: str | None = None
    merged_devices: list[str] = field(default_factory=list)
    collisions: list[tuple[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    removed_settings: int = 0


def _user_disabler() -> Any:
    """Return ``RegistryEntryDisabler.USER`` defensively.

    Read through ``getattr`` with the real string as fallback: the offline tests
    hand out a throwaway class for every attribute of a faked ``homeassistant``
    module, and a collision loser must still be disabled instead of crashing the
    sweep (plan §4.3/§11.1 R6). The real enum member is returned unchanged (no
    ``str()`` conversion) so ``async_update_entity`` receives exactly what HA
    expects.
    """
    return getattr(er.RegistryEntryDisabler, "USER", "user")


def _warning_severity() -> Any:
    """Return ``IssueSeverity.WARNING`` unchanged (same reason as above)."""
    return getattr(ir.IssueSeverity, "WARNING", "warning")


def _create_adoption_issue(
    hass: HomeAssistant,
    issue_id: str,
    *,
    translation_key: str | None = None,
    placeholders: dict[str, str] | None = None,
) -> None:
    """Create/update one stable adoption repair issue - never raises.

    Stable issue ids are what makes the whole adoption idempotent (plan §4.4):
    creating an existing id overwrites it instead of piling up duplicates. The
    issue registry is HA machinery; a failing (or unmocked) registry must never
    be able to abort the adoption or the account setup (M5).
    """
    try:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=_warning_severity(),
            translation_key=translation_key or issue_id,
            translation_placeholders=dict(placeholders or {}),
        )
    except Exception as err:  # noqa: BLE001 - repairs are best effort
        _LOGGER.debug(
            "Go Gauge: creating the repair issue %s failed (%s)", issue_id, err
        )


def _delete_adoption_issue(hass: HomeAssistant, issue_id: str) -> None:
    """Delete one adoption repair issue (idempotent, never raises)."""
    try:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
    except Exception as err:  # noqa: BLE001 - repairs are best effort
        _LOGGER.debug(
            "Go Gauge: deleting the repair issue %s failed (%s)", issue_id, err
        )


def _fresh_entry(ent_reg: Any, entity_id: str | None, fallback: Any) -> Any:
    """Re-read one registry entry after a write, falling back to the snapshot.

    Real HA hands out immutable ``RegistryEntry`` snapshots, so after a write the
    local object is stale. Re-resolving keeps the following predicates honest
    ("write only on a violated predicate", plan §4.5) without depending on the
    registry returning the mutated object.
    """
    try:
        current = ent_reg.async_get(entity_id)
    except Exception:  # noqa: BLE001 - stale snapshot beats a crash
        return fallback
    return current if current is not None else fallback


def _note_moved(report: AdoptionReport, entity_id: str | None) -> None:
    """Record ``entity_id`` in ``report.moved`` at most once."""
    if entity_id is not None and entity_id not in report.moved:
        report.moved.append(entity_id)


def _canonical_blocker(
    ent_reg: Any, entity_domain: str, canonical: str, winner: Any
) -> Any | None:
    """Return the entry that already owns ``canonical``, if it is not the winner.

    Two independent checks (plan §4.6):

    * ``async_get_entity_id`` is the authoritative lookup real HA uses to reject
      a duplicate ``new_unique_id`` with ``ValueError`` - checking it up front
      is what avoids that exception in the first place,
    * a second scan covers disabled-by-config entries: they have
      ``entity_id is None``, so ``async_get_entity_id`` cannot report them, but
      the registry entry still occupies the unique_id.

    HA semantics trap (the bug this parameter rename exists for): a
    ``RegistryEntry`` carries ``platform`` = the INTEGRATION domain
    (``go_gauge``) and ``domain`` = the ENTITY domain (``sensor``,
    ``binary_sensor``, ...). ``async_get_entity_id`` takes them in the order
    ``(domain, platform, unique_id)`` - the entity domain first. Swapping the
    two makes every lookup miss and the whole adoption a silent no-op.
    """
    try:
        occupant = ent_reg.async_get_entity_id(entity_domain, DOMAIN, canonical)
    except Exception:  # noqa: BLE001 - fall through to the entry scan
        occupant = None
    if occupant and occupant != getattr(winner, "entity_id", None):
        return _fresh_entry(ent_reg, occupant, None)

    for other in ent_reg.entities.values():
        if other is winner:
            continue
        if getattr(other, "platform", None) != DOMAIN:
            continue
        if getattr(other, "domain", None) != entity_domain:
            continue
        if getattr(other, "unique_id", None) != canonical:
            continue
        if getattr(other, "entity_id", None) != getattr(winner, "entity_id", None):
            return other
    return None


def _adopt_winner(
    ent_reg: Any,
    entry: ConfigEntry,
    report: AdoptionReport,
    canonical: str,
    winner: Any,
    device_id: str | None,
) -> None:
    """Migrate one winner to the account identity (plan §4.5 steps 1-3).

    Three independent predicates, each write guarded by its own violation check,
    so a second pass is write-free. The order matters: the ``unique_id`` is
    normalised first, then the ownership, then the device - and any exception
    propagates to the per-entity guard of the caller.
    """
    current = _fresh_entry(ent_reg, winner.entity_id, winner)
    if getattr(current, "unique_id", None) != canonical:
        old_unique_id = getattr(current, "unique_id", None) or ""
        ent_reg.async_update_entity(current.entity_id, new_unique_id=canonical)
        report.renamed.append((str(old_unique_id), canonical))
        current = _fresh_entry(ent_reg, current.entity_id, current)

    if getattr(current, "config_entry_id", None) != entry.entry_id:
        ent_reg.async_update_entity(
            current.entity_id, config_entry_id=entry.entry_id
        )
        _note_moved(report, current.entity_id)
        current = _fresh_entry(ent_reg, current.entity_id, current)

    if device_id is not None and getattr(current, "device_id", None) != device_id:
        ent_reg.async_update_entity(current.entity_id, device_id=device_id)
        _note_moved(report, current.entity_id)


def _adopt_loser(
    ent_reg: Any,
    entry: ConfigEntry,
    report: AdoptionReport,
    kind: str,
    loser: Any,
    device_id: str | None,
) -> None:
    """Neutralise one collision loser without deleting it (plan §4.3).

    ``config_entry_id`` -> account entry (a deleted workspace must not orphan
    the entry), ``device_id`` -> canonical account device (no ghost device left
    behind), ``disabled_by = USER`` (no unique_id clash with the platform, no
    polluting the device card) - and the legacy ``unique_id`` stays untouched so
    the user can still tell the two entries apart. No ``async_remove``:
    deleting a user's registry entry is their decision, not ours (plan §11.1 R5).
    """
    entity_id = getattr(loser, "entity_id", None)
    current = _fresh_entry(ent_reg, entity_id, loser)

    if getattr(current, "config_entry_id", None) != entry.entry_id:
        ent_reg.async_update_entity(entity_id, config_entry_id=entry.entry_id)
        current = _fresh_entry(ent_reg, entity_id, current)

    if device_id is not None and getattr(current, "device_id", None) != device_id:
        ent_reg.async_update_entity(entity_id, device_id=device_id)
        current = _fresh_entry(ent_reg, entity_id, current)

    if getattr(current, "disabled_by", None) is None:
        ent_reg.async_update_entity(entity_id, disabled_by=_user_disabler())

    report.collisions.append((kind, str(entity_id)))


def _sweep_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    ent_reg: Any,
    *,
    kinds: tuple[str, ...],
    entity_domain_by_kind: dict[str, str],
    canonical_unique_id: Any,
    device_id: str | None,
) -> set[str]:
    """Run the per-kind entity sweep and return the candidate device ids.

    Per kind (plan §4.1/§4.2/§4.3):

    1. collect the candidates via the INTEGRATION platform + entity domain +
       ``unique_id`` (canonical OR legacy suffix, so both ``account_<kind>`` and
       ``<any entry>_<kind>`` are found; ``config_entry_id is None`` is
       included). Careful with HA's field names: ``RegistryEntry.platform`` is
       the integration domain (``DOMAIN`` = ``go_gauge``) while
       ``RegistryEntry.domain`` is the entity domain (``sensor``,
       ``binary_sensor``, ...) - ``entity_domain_by_kind`` therefore supplies
       the LATTER, never a platform name.
    2. skip disabled-by-config entries (``entity_id is None``) - they cannot be
       adopted, but they DO occupy their unique_id (see ``_canonical_blocker``),
    3. pick the deterministic winner, run the winner predicates (unique_id ->
       config_entry_id -> device_id) or fall back to the loser path when another
       entry already owns the canonical unique_id,
    4. converge the per-kind repair issue.

    Every entity is processed inside its own ``except (ValueError, TypeError,
    KeyError)``: a concurrent workspace removal may invalidate a registry entry
    mid-sweep, and that must end up in ``report.errors`` instead of aborting the
    pass (plan §4.6/§11.1 R6).
    """
    candidate_device_ids: set[str] = set()

    for kind in kinds:
        entity_domain = entity_domain_by_kind[kind]
        canonical = canonical_unique_id(kind)
        collision_issue_id = f"account_entity_collision_{kind}"

        candidates = [
            candidate
            for candidate in ent_reg.entities.values()
            if getattr(candidate, "platform", None) == DOMAIN
            and getattr(candidate, "domain", None) == entity_domain
            and getattr(candidate, "unique_id", None)
            and (
                candidate.unique_id == canonical
                or candidate.unique_id.endswith(f"_{kind}")
            )
        ]

        adoptable = []
        for candidate in candidates:
            if candidate.entity_id is None:
                _LOGGER.debug(
                    "Go Gauge: account adoption skips %s (%s) - no entity_id "
                    "(disabled by config)",
                    candidate.unique_id,
                    kind,
                )
                continue
            adoptable.append(candidate)
            if candidate.device_id:
                candidate_device_ids.add(candidate.device_id)

        if not adoptable:
            # Nothing to adopt for this kind -> the collision repair (if any) is
            # resolved. Idempotent self-healing, plan §4.4.
            _delete_adoption_issue(hass, collision_issue_id)
            continue

        # Deterministic winner (plan §4.2): canonical first, then the smallest
        # entity_id. The third key is only a tie-breaker for registry doubles -
        # real HA's RegistryEntry has no ``id`` field, hence the defensive
        # ``getattr`` (an AttributeError here would abort the whole kind).
        winner = min(
            adoptable,
            key=lambda candidate: (
                candidate.unique_id != canonical,
                candidate.entity_id or "",
                str(getattr(candidate, "id", None) or ""),
            ),
        )

        blocker = _canonical_blocker(ent_reg, entity_domain, canonical, winner)
        if blocker is None:
            try:
                _adopt_winner(ent_reg, entry, report, canonical, winner, device_id)
            except (ValueError, TypeError, KeyError) as err:
                report.errors.append(f"{kind}:{winner.entity_id}: {err}")
                _LOGGER.warning(
                    "Go Gauge: adopting the account entity %s (%s) failed (%s) - "
                    "continuing with the other kinds",
                    winner.entity_id,
                    kind,
                    err,
                )
            losers = [
                candidate for candidate in adoptable if candidate is not winner
            ]
            winner_label = winner.entity_id or ""
        else:
            # The canonical unique_id is already taken by an entry we cannot
            # (or must not) rename away from: every candidate takes the loser
            # path, no rename is attempted at all (plan §4.6).
            losers = adoptable
            winner_label = getattr(blocker, "entity_id", None) or canonical
            _LOGGER.debug(
                "Go Gauge: canonical unique_id %s is already occupied by %s - "
                "treating all %d candidate(s) as collision losers",
                canonical,
                getattr(blocker, "entity_id", None) or "<disabled>",
                len(losers),
            )

        for loser in losers:
            try:
                _adopt_loser(ent_reg, entry, report, kind, loser, device_id)
            except (ValueError, TypeError, KeyError) as err:
                report.errors.append(f"{kind}:{loser.entity_id}: {err}")
                _LOGGER.warning(
                    "Go Gauge: disabling the duplicate account entity %s (%s) "
                    "failed (%s) - continuing",
                    loser.entity_id,
                    kind,
                    err,
                )

        if losers:
            _create_adoption_issue(
                hass,
                collision_issue_id,
                translation_key="account_entity_collision",
                placeholders={
                    "kind": kind,
                    "winner": winner_label,
                    "losers": ", ".join(
                        str(getattr(loser, "entity_id", "") or "")
                        for loser in losers
                    ),
                },
            )
        else:
            _delete_adoption_issue(hass, collision_issue_id)

    return candidate_device_ids


def _device_entries(dev_reg: Any) -> list[Any]:
    """Return every device entry of the registry - no mapping access.

    The device registry changed shape in HA 2026.9: the ``devices`` collection
    now yields ``DeviceEntry`` objects when iterated (the supported enumeration),
    while using it as a mapping - subscript, membership, ``.values()``, ``.get()``
    - is deprecated and stops working in HA 2027.9. Up to HA 2026.8 the same
    attribute is still a plain ``Mapping`` whose iteration yields device *ids*;
    there ``.values()`` is the only entry access and NOT deprecated.

    The collection type separates both generations reliably (the deprecated
    2026.9 view is not a ``Mapping``). Note that ``DeviceRegistry`` itself is
    NOT iterable on either version, so ``for device in registry`` is not an
    option.

    A missing collection yields an empty list, never an exception: the real
    ``DeviceRegistry`` only creates ``devices`` during ``_async_load()``, so the
    attribute is simply absent on a not-yet-loaded registry (and on foreign
    registry doubles). "No devices known" is the conservative truth in that
    state, and every caller - including the unguarded ones in
    :func:`_device_has_dependencies` and :func:`_move_account_device` - may rely
    on getting a list back instead of having to catch ``AttributeError``.
    """
    devices = getattr(dev_reg, "devices", None)
    if devices is None:
        return []
    if isinstance(devices, Mapping):
        return list(devices.values())
    return list(devices)


def _canonical_device(dev_reg: Any) -> Any | None:
    """Return the device carrying ``(DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)``.

    Primary path is HA's own non-deprecated identifier lookup
    ``DeviceRegistry.async_get_devices(identifiers={...})`` (available since HA
    2026.8, i.e. the declared minimum). It replaces the two deprecated lookup
    styles - ``async_get_device`` and treating the registry's ``devices``
    attribute as a mapping - which stop working in HA 2027.9. Deliberately NOT
    ``async_get_device_by_identifier``: that one is scoped to ONE owning config
    entry (``identifier, config_entry_id``) and would miss the canonical device
    while it is still owned by the legacy workspace entry.

    Version fallback: without that helper the entries are enumerated through
    :func:`_device_entries` and matched by identifier - the path supported by
    older cores. Identifiers are stable for the canonical account device;
    should more than one device carry the identifier (registry drift), the
    smallest ``id`` wins so the choice is deterministic across runs. A registry
    without a usable surface or any lookup error is a normal "no device" state -
    never a setup failure.
    """
    identifier = (DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)

    lookup = getattr(dev_reg, "async_get_devices", None)
    if callable(lookup):
        try:
            matches = list(lookup(identifiers={identifier}))
        except (TypeError, AttributeError):
            # Older core: no such lookup / different signature -> enumerate.
            matches = None
        except Exception:  # noqa: BLE001 - missing device is a normal state
            return None
        if matches is not None:
            if not matches:
                return None
            return min(matches, key=lambda device: str(getattr(device, "id", "")))

    try:
        matches = [
            device
            for device in _device_entries(dev_reg)
            if identifier in getattr(device, "identifiers", set())
        ]
    except Exception:  # noqa: BLE001 - missing device is a normal state
        return None
    if not matches:
        return None
    return min(matches, key=lambda device: str(getattr(device, "id", "")))


def _workspace_device_identifiers(
    hass: HomeAssistant, entry: ConfigEntry
) -> set[tuple[str, str]]:
    """Return the device identifiers of pure workspace devices.

    A workspace device (``identifiers = {(DOMAIN, <workspace entry_id>)}``,
    created by ``entity.GoGaugeEntityBase``) is NEVER a duplicate: it carries
    the workspace's usage entities and legitimately stays its own device. Its
    identifiers are returned so the duplicate-device merge skips it entirely - a
    workspace device must not even produce an ``account_device_not_empty``
    repair (it was never a candidate for merging).
    """
    account_entry_id = getattr(entry, "entry_id", None)
    try:
        entries = list(hass.config_entries.async_entries(DOMAIN))
    except Exception:  # noqa: BLE001 - no entry list -> no exclusions
        return set()

    return {
        (DOMAIN, str(candidate_entry_id))
        for workspace in entries
        if (candidate_entry_id := getattr(workspace, "entry_id", None))
        and candidate_entry_id != account_entry_id
        and conf_entry_kind(getattr(workspace, "data", None)) == ENTRY_KIND_WORKSPACE
    }


def _device_identifiers(device: Any) -> set[tuple[str, str]]:
    """Return a device's identifiers as a plain set (defensive).

    Real HA hands out a ``set``/``frozenset``; a partially faked registry may
    hand out ``None`` or something uniterable - both resolve to an empty set so
    no caller has to guard the type itself.
    """
    identifiers = getattr(device, "identifiers", None) or set()
    try:
        return set(identifiers)
    except TypeError:  # pragma: no cover - defensive for foreign doubles
        return set()


def _is_workspace_device(device: Any, excluded: set[tuple[str, str]]) -> bool:
    """Return whether ``device`` carries a pure workspace identifier."""
    return bool(_device_identifiers(device) & excluded)


def _go_gauge_entry_ids(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Return the entry ids of every go_gauge ConfigEntry of this domain.

    Fallback guard of the device hand-over (review follow-up): a fallback target
    is adopted only while its CURRENT ownership set consists exclusively of this
    integration's entries. ``async_update_device(new_config_entry_id=...)``
    REPLACES the whole set, and HA then removes every entity whose
    ``config_entry_id`` is no longer listed on the device - a device that is
    (also) owned by a foreign integration would lose those entities
    irrecoverably.

    Fail-closed on purpose: when the entry list cannot be read, only the account
    entry itself counts as ours, so a device owned by anything else is never
    adopted.
    """
    allowed: set[str] = set()
    account_entry_id = getattr(entry, "entry_id", None)
    if account_entry_id:
        allowed.add(str(account_entry_id))

    try:
        entries = list(hass.config_entries.async_entries(DOMAIN))
    except Exception:  # noqa: BLE001 - no entry list -> only the account entry
        return allowed

    for candidate in entries:
        candidate_entry_id = getattr(candidate, "entry_id", None)
        if candidate_entry_id:
            allowed.add(str(candidate_entry_id))
    return allowed


def _is_go_gauge_owned(device: Any, allowed_entry_ids: set[str]) -> bool:
    """Return whether every owner of ``device`` is a go_gauge entry.

    A device without owners counts as adoptable: it has no ownership set to
    replace, so the hand-over cannot take anything away. An unreadable ownership
    set is NOT adoptable (fail-closed - see :func:`_go_gauge_entry_ids`).
    """
    owners = getattr(device, "config_entries", None) or set()
    try:
        owner_ids = {str(owner) for owner in owners}
    except TypeError:  # pragma: no cover - defensive for foreign doubles
        return False
    return owner_ids <= allowed_entry_ids


def _device_has_dependencies(ent_reg: Any, dev_reg: Any, device_id: str) -> bool:
    """Return whether a device still has entities or child devices.

    Checked against ALL entities (disabled ones included): a disabled collision
    loser still points at its old device, and removing that device would orphan
    it. ``via_device_id`` covers HA >= 2026.9 child devices (plan §3.3 step 3b).

    The child scan goes through :func:`_device_entries`, which enumerates the
    registry without the deprecated mapping access (see there) - the supported
    path that keeps working after 2027.9.
    """
    for candidate in ent_reg.entities.values():
        if getattr(candidate, "device_id", None) == device_id:
            return True
    for child in _device_entries(dev_reg):
        if getattr(child, "via_device_id", None) == device_id:
            return True
    return False


def _merge_duplicate_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    ent_reg: Any,
    dev_reg: Any,
    candidate_device_ids: set[str],
    canonical_device_id: str | None,
) -> None:
    """Remove EMPTY duplicate devices, report the rest (plan §3.3 step 3b).

    Runs AFTER the entity sweep on purpose: only then is a legacy device really
    empty, and only then may it disappear. A duplicate that still has entities
    or child devices is left in place with an ``account_device_not_empty``
    repair - never deleted, never silently re-parented (plan §11.1 R5).

    Pure workspace devices are excluded up front (see
    ``_workspace_device_identifiers``): their entities are not account entities and
    their device is not an adoption artifact.
    """
    excluded_identifiers = _workspace_device_identifiers(hass, entry)
    duplicates = candidate_device_ids - {canonical_device_id}
    not_empty: list[str] = []

    for duplicate_id in sorted(duplicates):
        device = None
        try:
            device = dev_reg.async_get(duplicate_id)
        except Exception:  # noqa: BLE001 - treat unknown as gone
            device = None
        if device is None:
            continue
        if _is_workspace_device(device, excluded_identifiers):
            _LOGGER.debug(
                "Go Gauge: device %s belongs to a workspace - never a duplicate",
                duplicate_id,
            )
            continue

        if _device_has_dependencies(ent_reg, dev_reg, duplicate_id):
            not_empty.append(duplicate_id)
            label = str(getattr(device, "name", None) or duplicate_id)
            report.errors.append(
                f"device {duplicate_id}: not empty - left in place"
            )
            _create_adoption_issue(
                hass,
                "account_device_not_empty",
                placeholders={"device": label},
            )
            continue

        try:
            dev_reg.async_remove_device(duplicate_id)
        except Exception as err:  # noqa: BLE001 - report, never abort
            report.errors.append(f"device {duplicate_id}: remove failed ({err})")
            _LOGGER.warning(
                "Go Gauge: removing the duplicate account device %s failed (%s)",
                duplicate_id,
                err,
            )
            continue
        report.merged_devices.append(duplicate_id)

    if report.merged_devices:
        _create_adoption_issue(
            hass,
            "account_device_merged",
            placeholders={"device": ", ".join(report.merged_devices)},
        )
    else:
        _delete_adoption_issue(hass, "account_device_merged")

    if not not_empty:
        _delete_adoption_issue(hass, "account_device_not_empty")


def _device_exists(dev_reg: Any, device_id: str) -> bool:
    """Return whether ``device_id`` still resolves to a device after a write.

    ``DeviceRegistry.async_update_device`` returns ``None`` for an unknown id -
    a move that never happened must not be reported as ``device_moved``. An
    unavailable lookup counts as "exists": the write succeeded and we merely
    cannot verify it (older/partial registry double), so the caller is not
    pushed into a false negative.
    """
    try:
        return dev_reg.async_get(device_id) is not None
    except Exception:  # noqa: BLE001 - unverifiable is not a failure
        return True


def _move_account_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    dev_reg: Any,
    candidate_device_ids: set[str],
) -> None:
    """Hand the account device over to the account entry - the LAST step.

    Target resolution (plan §3.3 step 3c): the device carrying
    ``(DOMAIN, ACCOUNT_DEVICE_IDENTIFIER)``; if it does not exist yet, the
    best-ranked surviving candidate device (already owned by the account entry
    first, then lowest id). If neither exists, nothing is written: HA creates the
    device on the platform forward.

    A WORKSPACE device is never a valid fallback target (review blocker fix):
    ``async_update_device(new_config_entry_id=...)`` REPLACES the whole
    ownership set, and HA then removes every entity whose ``config_entry_id`` is
    no longer listed on the device. Handing the workspace device over would take
    its usage entities with it - irrecoverable data loss. Candidate devices
    carrying a workspace identifier are therefore skipped; if that leaves no
    target, ``device_moved`` stays ``None`` and nothing is written.

    A candidate owned by a FOREIGN integration is skipped for the same reason
    (review follow-up): replacing its ownership set would delete the entities of
    that integration. Only a device whose ``config_entries`` are entirely
    go_gauge entries is adopted - see ``_go_gauge_entry_ids``. Whenever that
    check cannot be satisfied with certainty, nothing is written and HA creates
    the account device with the platform forward instead.

    ``new_config_entry_id`` replaces the whole ownership set. An HA version
    without that keyword raises ``TypeError``; the guard then raises a repair
    issue and keeps the integration running (plan §11.1 R1) - it never raises.

    The fallback survivors are enumerated through :func:`_device_entries`,
    which never uses the deprecated mapping access on the registry's ``devices``
    collection (removed in HA 2027.9). Filtering and the deterministic
    best-rank sort are unchanged.
    """
    target = _canonical_device(dev_reg)
    if target is None:
        excluded_identifiers = _workspace_device_identifiers(hass, entry)
        allowed_owner_ids = _go_gauge_entry_ids(hass, entry)
        survivors = []
        for device in _device_entries(dev_reg):
            device_id = getattr(device, "id", None)
            if device_id not in candidate_device_ids:
                continue
            if _is_workspace_device(device, excluded_identifiers):
                continue
            if not _is_go_gauge_owned(device, allowed_owner_ids):
                _LOGGER.debug(
                    "Go Gauge: device %s is (also) owned by a foreign "
                    "integration (%s) - never a fallback move target",
                    device_id,
                    sorted(str(owner) for owner in
                           (getattr(device, "config_entries", None) or set())),
                )
                continue
            survivors.append(device)
        if survivors:
            target = min(
                survivors,
                key=lambda device: (
                    entry.entry_id
                    not in (getattr(device, "config_entries", None) or set()),
                    str(getattr(device, "id", "")),
                ),
            )

    if target is None:
        _LOGGER.debug(
            "Go Gauge: no account device to hand over - HA creates it with the "
            "platform forward"
        )
        return

    target_id = str(getattr(target, "id", ""))
    owners = getattr(target, "config_entries", None) or set()
    if entry.entry_id in owners:
        _delete_adoption_issue(hass, "adoption_unsupported")
        return

    try:
        updated = dev_reg.async_update_device(
            target_id, new_config_entry_id=entry.entry_id
        )
    except TypeError as err:
        # HA without `new_config_entry_id` (plan §11.1 R1).
        report.errors.append(f"device {target_id}: transfer unsupported ({err})")
        _LOGGER.warning(
            "Go Gauge: this Home Assistant version cannot hand the account "
            "device over (%s) - the account entities keep working, please "
            "update Home Assistant",
            err,
        )
        _create_adoption_issue(
            hass,
            "adoption_unsupported",
            placeholders={"error": str(err)},
        )
    except Exception as err:  # noqa: BLE001 - adoption must never raise
        report.errors.append(f"device {target_id}: transfer failed ({err})")
        _LOGGER.warning(
            "Go Gauge: handing the account device %s over failed (%s)",
            target_id,
            err,
        )
    else:
        # Only report a move that really happened: an unknown id makes
        # ``async_update_device`` return ``None``, and HA may have dropped the
        # device between the lookup and the write.
        if updated is not None and _device_exists(dev_reg, target_id):
            report.device_moved = target_id
            _delete_adoption_issue(hass, "adoption_unsupported")
        else:
            _LOGGER.debug(
                "Go Gauge: the account device %s vanished during the hand-over "
                "(%r) - not reported as moved",
                target_id,
                updated,
            )


def async_remove_legacy_model_settings(hass: HomeAssistant) -> int:
    """Remove the pre-2.0.0 workspace model controls from the entity registry.

    Plan §4.7 (Breaking, but deliberate): ``auto_update_models`` and
    ``models_refresh_minutes`` are global account settings now. Leaving the old
    ``<workspace>_auto_update_models`` / ``<workspace>_models_refresh_minutes``
    registry entries behind would show dead controls that write nothing.
    They are REMOVED (not migrated, no value transfer) - the canonical
    ``account_*`` ids are explicitly exempt.

    Returns the number of removed entries and is idempotent: a second pass
    finds no candidate and removes nothing. Every removal is guarded on its
    own, so one failing entry cannot abort the adoption (plan §4.6).

    HA semantics: ``RegistryEntry.platform`` is the integration domain
    (``go_gauge``) and ``RegistryEntry.domain`` is the entity domain
    (``switch``/``number``). The predicates below reflect that order.
    """
    removed = 0
    try:
        ent_reg = er.async_get(hass)
        candidates = list(ent_reg.entities.values())
    except Exception as err:  # noqa: BLE001 - removal is best effort
        _LOGGER.debug(
            "Go Gauge: legacy model-setting removal skipped (%s)", err
        )
        return 0

    for candidate in candidates:
        try:
            # HA field names (see _canonical_blocker): ``platform`` is the
            # integration domain, ``domain`` is the entity domain. Checking
            # them the other way round removed nothing at all.
            if getattr(candidate, "platform", None) != DOMAIN:
                continue
            if getattr(candidate, "domain", None) not in {"switch", "number"}:
                continue
            unique_id = getattr(candidate, "unique_id", None)
            if not isinstance(unique_id, str):
                continue
            if unique_id in _CANONICAL_MODEL_SETTING_IDS:
                continue
            if not unique_id.endswith(_LEGACY_MODEL_SETTING_SUFFIXES):
                continue
            entity_id = getattr(candidate, "entity_id", None)
            if entity_id is None:
                continue
            ent_reg.async_remove(entity_id)
            removed += 1
        except Exception as err:  # noqa: BLE001 - never break the adoption
            _LOGGER.warning(
                "Go Gauge: removing the legacy model setting %s failed (%s)",
                getattr(candidate, "entity_id", "?"),
                err,
            )

    if removed:
        _LOGGER.info(
            "Go Gauge: removed %d legacy workspace model setting(s) - the "
            "controls live on the account entry now",
            removed,
        )
    return removed


def _adopt_registries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    report: AdoptionReport,
    ent_reg: Any,
    dev_reg: Any,
) -> None:
    """Run the adoption phases in their mandatory order (plan §3.3 step 3).

    ENTITIES first, then the duplicate-device merge, then the device hand-over,
    and finally the removal of the legacy workspace model controls.
    The order is not cosmetic: moving/removing a device while entities still
    point at it would take those entities with it (plan §4.5, §11.3 V2).

    Ordering INVARIANT (review fix M1): a single failed entity write stops the
    pass BEFORE any device write. ``report.errors`` non-empty means the sweep is
    incomplete, and an entity may still carry the old ``config_entry_id``;
    handing the device over would then replace the ownership set and HA would
    DELETE that entity (the E2E-B1 cascade). So no merge, no device move and no
    legacy-setting removal happen in that state - ``merged_devices``,
    ``device_moved`` and ``removed_settings`` stay empty/``0``, and the next
    start repeats the whole pass idempotently.
    """
    # Lazy import on purpose: see ``_adoption_issue_ids`` - the offline test
    # loader only resolves sibling modules that are already in ``sys.modules``,
    # so a module-level import here would force every existing test file that
    # loads ``account.py`` to preload ``account_logic`` as well.
    from .account_logic import (
        ACCOUNT_ENTITY_KINDS,
        PLATFORM_BY_KIND,
        canonical_unique_id,
    )

    # ``PLATFORM_BY_KIND`` maps a kind to its ENTITY domain ("sensor",
    # "binary_sensor") - NOT to an integration platform. The sweep filters on
    # ``RegistryEntry.domain`` with it and on ``RegistryEntry.platform`` with
    # ``DOMAIN`` (see _canonical_blocker for the HA semantics trap).

    device = _canonical_device(dev_reg)
    device_id = str(getattr(device, "id", "")) if device is not None else None

    candidate_device_ids = _sweep_entities(
        hass,
        entry,
        report,
        ent_reg,
        kinds=ACCOUNT_ENTITY_KINDS,
        entity_domain_by_kind=PLATFORM_BY_KIND,
        canonical_unique_id=canonical_unique_id,
        device_id=device_id,
    )

    # Review fix M1: no device write while the sweep is incomplete. See the
    # ordering invariant in the docstring - the next run retries idempotently.
    if report.errors:
        _LOGGER.warning(
            "Go Gauge: account adoption stopped after the entity sweep (%d "
            "error(s)) - no device merge, no device hand-over and no legacy "
            "setting removal this pass; the next start retries idempotently",
            len(report.errors),
        )
        return

    _merge_duplicate_devices(
        hass,
        entry,
        report,
        ent_reg,
        dev_reg,
        candidate_device_ids,
        device_id,
    )

    _move_account_device(hass, entry, report, dev_reg, candidate_device_ids)

    # Last step (plan §3.3 step 3d / §4.7): the legacy workspace controls are
    # removed only AFTER the device hand-over, so the account device never
    # inherits an entity that is about to disappear.
    report.removed_settings = async_remove_legacy_model_settings(hass)


async def async_adopt_account_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> AdoptionReport:
    """Move the five catalog entities onto the account entry - never raises.

    Plan §3.3/§4: this is the ONE-TIME (idempotent) registry migration that runs
    at the start of every account setup and therefore on every v7 -> v8 upgrade,
    workspace reload and HA start. It performs the whole registry/device work
    BEFORE the platform forward, because HA then finds the existing
    registry entries through their canonical ``unique_id`` and keeps
    ``entity_id``, history, area, labels and the device assignment (plan §3.3
    step 3e). Running it after the forward would create a second generation of
    entities instead of adopting the existing ones.

    Order, strictly (each step is a conjunction of predicates whose violation is
    the ONLY trigger of the corresponding write, plan §4.5):

    0. take the shared ``DATA_ADOPTION_LOCK`` (serialises HA start, import flow
       and workspace setup; plan §7.2) and resolve both registries,
    1. collect the candidates per kind and remember their devices,
    2. sweep the entities per kind (rename -> config_entry_id -> device_id),
       or disable the losers of a canonical-unique_id collision,
    3. remove duplicate devices - only when empty - and report the rest,
    4. hand the canonical device over to the account entry,
    5. converge the ``adoption_failed`` repair and persist the report in
       ``hass.data[DOMAIN][DATA_ADOPTION_REPORT]``.

    NEVER raises (plan §2.4/M5, §4.6, §11.1 R6): an adoption problem -
    registry drift, a concurrent workspace removal, an HA version without
    ``new_config_entry_id`` - must not fail the account setup, which in turn must
    not take the workspaces down. Problems end up in ``report.errors`` plus a
    repair issue and are retried idempotently on the next start. The function is
    a no-op (plus a debug log) when ``hass.data`` is not a plain dict, e.g. in
    the legacy MagicMock-based tests.
    """
    report = AdoptionReport()

    try:
        data = _domain_data(hass, create=True)
        if data is None:
            _LOGGER.debug(
                "Go Gauge: account adoption skipped - hass.data is not a dict"
            )
            return report

        lock = data.setdefault(DATA_ADOPTION_LOCK, asyncio.Lock())
        async with lock:
            try:
                _adopt_registries(
                    hass, entry, report, er.async_get(hass), dr.async_get(hass)
                )
            except Exception as err:  # noqa: BLE001 - never break the setup
                _LOGGER.warning(
                    "Go Gauge: adopting the account entities failed unexpectedly "
                    "(%s) - the setup continues, the next start retries "
                    "idempotently",
                    err,
                )
                report.errors.append(f"adoption: {err}")

            # Step 5 - all bookkeeping inside the lock, so two concurrent
            # callers cannot interleave "report written" and "issue converged".
            data[DATA_ADOPTION_REPORT] = report
            if report.errors:
                _create_adoption_issue(
                    hass,
                    "adoption_failed",
                    placeholders={"count": str(len(report.errors))},
                )
            else:
                _delete_adoption_issue(hass, "adoption_failed")
    except Exception as err:  # noqa: BLE001 - outermost safety net
        _LOGGER.warning(
            "Go Gauge: account adoption failed unexpectedly (%s) - the setup "
            "continues, the next start retries idempotently",
            err,
        )
        report.errors.append(f"adoption: {err}")

    return report
