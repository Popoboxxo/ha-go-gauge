"""Reachability evidence collection and snapshot resolution (2.1.0 account split).

Extracted from :mod:`.account` in the 2.1.0 per-concern split (plan §4.3). This
module owns the API reachability matrix of the installation (plan §5):

* ``workspace_coordinators`` - the read-only view of the loaded workspace
  coordinators in ``hass.data[DOMAIN]`` (the evidence source),
* ``collect_evidence`` - the timestamped fetch outcomes of the loaded
  coordinators (account catalog fetch + every loaded workspace cycle),
* ``ReachabilitySnapshot`` / ``account_reachability_snapshot`` /
  ``account_reachability`` - the single computation site that resolves evidence
  plus TTLs into the tri-state reachability consumed by the account binary
  sensor and the diagnostics export.

The shared ``hass.data[DOMAIN]`` accessor ``_domain_data`` and the
``AccountCoordinator`` runtime live in :mod:`.account_coordinator` and are
imported from there (no own copy). ``account.py`` re-exports the public symbols
of this module so ``from .account import ...`` stays stable for the platforms.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .account_coordinator import AccountCoordinator, _domain_data
from .const import (
    ACCOUNT_ENTRY_UNIQUE_ID,
    DATA_ACCOUNT_KEY,
    DEFAULT_USAGE_REFRESH_MINUTES,
    DOMAIN,
    ENTRY_KIND_WORKSPACE,
    conf_entry_kind,
)
from .coordinator import GoGaugeCoordinator

if TYPE_CHECKING:  # no runtime import: account_logic is loaded lazily on purpose
    from .account_logic import Evidence, Reachability

_LOGGER = logging.getLogger(__name__)


def workspace_coordinators(hass: HomeAssistant) -> dict[str, GoGaugeCoordinator]:
    """Return ``{entry_id: GoGaugeCoordinator}`` for the loaded workspace entries.

    ``hass.data[DOMAIN]`` holds one runtime object per entry plus foreign keys
    (e.g. ``_account``, the adoption lock), so the mapping
    is filtered by type instead of trusting every value. Defensive on
    ``hass.data``/``hass.data[DOMAIN]``: the legacy tests pass a ``MagicMock``
    and diagnostics may run while ``hass.data`` is not a plain dict yet - both
    must yield an empty mapping instead of raising.
    """
    hass_data = getattr(hass, "data", None)
    if not isinstance(hass_data, dict):
        return {}
    domain_data = hass_data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return {}
    return {
        entry_id: coordinator
        for entry_id, coordinator in domain_data.items()
        if isinstance(coordinator, GoGaugeCoordinator)
    }


def collect_evidence(hass: HomeAssistant) -> list[Evidence]:
    """Collect the timestamped fetch outcomes of the loaded coordinators.

    The source of the reachability matrix (plan §5): one ``Evidence`` per
    observed attempt -

    * account catalog fetch -> ``source="models"`` (outcome ``ok``/``fail``),
    * every loaded workspace cycle -> ``source="usage"`` with the workspace
      name and the coordinator's ``last_usage_outcome`` (an unknown/missing
      outcome counts as ``fail``: it must never be able to prove success).

    Returns a plain list (the caller only iterates it). Defensive by design: a
    coordinator without the evidence attributes (older object, test double) is
    skipped instead of raising - this function runs from a sensor property and
    must never take the entity update down.
    """
    from .account_logic import SOURCE_MODELS, SOURCE_USAGE, Evidence

    evidence: list[Evidence] = []
    data = _domain_data(hass)
    account = data.get(DATA_ACCOUNT_KEY) if data is not None else None
    if isinstance(account, AccountCoordinator):
        attempt = getattr(account, "last_models_attempt", None)
        if isinstance(attempt, datetime):
            outcome = "ok" if getattr(account, "last_models_outcome", None) == "ok" else "fail"
            evidence.append(Evidence(SOURCE_MODELS, None, outcome, attempt))

    for coordinator in workspace_coordinators(hass).values():
        attempt = getattr(coordinator, "last_usage_attempt", None)
        if not isinstance(attempt, datetime):
            continue
        usage_outcome = getattr(coordinator, "last_usage_outcome", None)
        if usage_outcome not in ("ok", "neutral", "fail"):
            usage_outcome = "fail"
        evidence.append(
            Evidence(
                SOURCE_USAGE,
                getattr(coordinator, "ws_name", None),
                usage_outcome,
                attempt,
            )
        )
    return evidence


@dataclass(frozen=True)
class ReachabilitySnapshot:
    """One consistent reachability resolution plus the inputs it was built from.

    The binary sensor and the diagnostics export need more than the tri-state
    itself: the TTLs they publish and the per-evidence rows (age/TTL/fresh) come
    from the SAME computation, so a state and its attributes can never disagree.
    Hence this carrier instead of separate TTL helpers at the call sites - see
    :func:`account_reachability_snapshot`.
    """

    reachability: Reachability
    evidence: list[Evidence]
    now: datetime
    models_ttl: timedelta
    usage_ttl: timedelta

    def evidence_attributes(self) -> list[dict[str, Any]]:
        """Render the evidence as JSON-safe attribute rows.

        Each row carries ``age_s``/``ttl_s``/``fresh`` so a support case (and the
        sensor attributes) can see WHY the matrix resolved the way it did. The
        per-row freshness rule (``age <= ttl``, unknown source -> models TTL) is
        applied HERE, on the same ``now``/TTL inputs the matrix itself used, so
        the attributes explain exactly the state they were published with;
        consumers only format the result and never recompute a ``now``.
        """
        rows: list[dict[str, Any]] = []
        for item in self.evidence:
            ttl = self.usage_ttl if item.source == "usage" else self.models_ttl
            at = (
                item.at
                if item.at.tzinfo is not None
                else item.at.replace(tzinfo=timezone.utc)
            )
            age = (self.now - at).total_seconds()
            rows.append({
                "source": item.source,
                "workspace": item.workspace,
                "outcome": item.outcome,
                "age_s": round(age, 3),
                "ttl_s": ttl.total_seconds(),
                "fresh": age <= ttl.total_seconds(),
            })
        return rows


def account_reachability_snapshot(hass: HomeAssistant) -> ReachabilitySnapshot:
    """Collect evidence + TTLs ONCE and resolve the reachability matrix (plan §5).

    The single computation site behind :func:`account_reachability`: it owns both
    inputs of the matrix -

    * the evidence (:func:`collect_evidence`: account catalog fetch + every
      loaded workspace cycle),
    * the TTLs (models = ``MODELS_TTL_FACTOR`` x the account's *configured base*
      interval, fallback when auto-fetch is off; usage = ``USAGE_TTL_FACTOR`` x
      the smallest workspace interval, fallback
      ``DEFAULT_USAGE_REFRESH_MINUTES`` when no workspace is loaded).

    Defensive by design: a missing account coordinator, a non-dict ``hass.data``
    or a coordinator with broken attributes must never take a sensor update or
    the diagnostics export down. In that case the resolution runs with EMPTY
    evidence, which the matrix answers with ``None``/``no_evidence`` - "unknown"
    instead of a made-up state.
    """
    from .account_logic import models_ttl, resolve_reachability, usage_ttl

    now = datetime.now(timezone.utc)
    evidence: list[Evidence] = []
    models_ttl_ = models_ttl(None)
    usage_minutes = DEFAULT_USAGE_REFRESH_MINUTES

    try:
        evidence = collect_evidence(hass)
        data = _domain_data(hass)
        account = data.get(DATA_ACCOUNT_KEY) if data is not None else None
        if account is not None and bool(getattr(account, "auto_fetch", False)):
            models_ttl_ = models_ttl(
                int(getattr(account, "base_interval_minutes"))
            )
        minutes = [
            int(
                getattr(
                    coordinator, "usage_minutes", DEFAULT_USAGE_REFRESH_MINUTES
                )
            )
            for coordinator in workspace_coordinators(hass).values()
        ]
        if minutes:
            usage_minutes = min(minutes)
    except Exception as err:  # noqa: BLE001 - evidence is always optional
        _LOGGER.debug(
            "Go Gauge: reachability inputs unusable (%s) - resolving without "
            "evidence",
            err,
        )
        evidence = []
        models_ttl_ = models_ttl(None)
        usage_minutes = DEFAULT_USAGE_REFRESH_MINUTES

    usage_ttl_ = usage_ttl(usage_minutes)
    return ReachabilitySnapshot(
        reachability=resolve_reachability(
            evidence, now, models_ttl_, usage_ttl_
        ),
        evidence=list(evidence),
        now=now,
        models_ttl=models_ttl_,
        usage_ttl=usage_ttl_,
    )


def account_reachability(hass: HomeAssistant) -> Reachability:
    """Resolve the API reachability of this installation (plan §5).

    THE one place where the TTL/evidence rule is applied for BOTH consumers -
    the account binary sensor (:class:`binary_sensor.ApiReachableBinarySensor`)
    and the diagnostics export. Neither consumer re-implements TTL scaling,
    evidence collection or the resolution order; both go through this function
    (or its :func:`account_reachability_snapshot` carrier when they also need
    the TTLs/evidence rows).

    TTLs: models evidence = ``MODELS_TTL_FACTOR`` x the account's configured
    base interval (``MODELS_TTL_FALLBACK_MINUTES`` when auto-fetch is off),
    usage evidence = ``USAGE_TTL_FACTOR`` x the smallest workspace usage interval
    (``DEFAULT_USAGE_REFRESH_MINUTES`` when no workspace is loaded).

    Never raises: without a coordinator (or with broken attributes) the matrix
    resolves empty evidence into ``None``/``no_evidence``.
    """
    return account_reachability_snapshot(hass).reachability


def _account_entries(entries: list[Any]) -> list[Any]:
    """Return the entries whose ``unique_id`` marks them as the account entry."""
    return [
        entry for entry in entries
        if getattr(entry, "unique_id", None) == ACCOUNT_ENTRY_UNIQUE_ID
    ]


def _entry_is_loaded(entry: Any) -> bool:
    """Return ``True`` when the entry's state is ``ConfigEntryState.LOADED``.

    Compares both by identity and by equality: in production this is the real
    StrEnum (``is``), while the offline test fakes carry the raw string values
    (``==``). A missing/unfamiliar ``state`` attribute is treated as "not
    loaded"; the ensure then only logs at DEBUG level, because HA - not this
    integration - owns (re)loading a stored entry (plan §7.2).
    """
    state = getattr(entry, "state", None)
    loaded = getattr(ConfigEntryState, "LOADED", None)
    if state is None or loaded is None:
        return False
    return bool(state is loaded or state == loaded)


def _has_enabled_workspace(entries: list[Any]) -> bool:
    """Return ``True`` when at least one enabled workspace entry exists.

    Workspace = ``conf_entry_kind(entry.data) == ENTRY_KIND_WORKSPACE`` (the
    default for every pre-2.0.0 entry). A disabled workspace does not count:
    the account entry must not come into existence just because a workspace is
    switched off (plan §3.5 - "deaktiviert = Nutzerintent respektieren").
    """
    for entry in entries:
        if conf_entry_kind(getattr(entry, "data", None)) != ENTRY_KIND_WORKSPACE:
            continue
        if getattr(entry, "disabled_by", None) is None:
            return True
    return False
