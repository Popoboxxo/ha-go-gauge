"""Go Gauge HA - account-level entity identity and reachability rules.

Deliberately HA-free: this module imports no ``homeassistant`` code, so entity
identity matching and the reachability matrix stay unit-testable without a
Home-Assistant installation.

Since 2.0.0 the five catalog entities and the API-reachability sensor belong to
the single workspace-independent *account* entry. Older releases registered
them on a workspace entry (``<workspace>_model_catalog``, ...), so the identity
helpers below accept both the canonical (``account_<kind>``) and the legacy
(``<anything>_<kind>``) shape - the adoption sweep needs to find the latter and
the entity classes need to claim the former.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .const import (
    ACCOUNT_UNIQUE_ID_PREFIX,
    MODELS_TTL_FACTOR,
    MODELS_TTL_FALLBACK_MINUTES,
    USAGE_TTL_FACTOR,
)

# The five account-level entities, in a stable order (registry sweeps and
# tests iterate this tuple).
ACCOUNT_ENTITY_KINDS: tuple[str, ...] = (
    "model_catalog",
    "models_live_count",
    "cheapest_model",
    "free_models",
    "api_reachable",
)

# Four catalog sensors plus one availability binary_sensor.
PLATFORM_BY_KIND: dict[str, str] = {
    "model_catalog": "sensor",
    "models_live_count": "sensor",
    "cheapest_model": "sensor",
    "free_models": "sensor",
    "api_reachable": "binary_sensor",
}

# Longest kind first: matching is suffix-based, so a hypothetical short kind
# that is a suffix of a longer one must never win. Sorting keeps the result
# deterministic and independent of ACCOUNT_ENTITY_KINDS' declaration order.
_KINDS_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted(ACCOUNT_ENTITY_KINDS, key=len, reverse=True)
)

# Evidence outcomes / sources, as used by the reachability matrix (plan §5).
OUTCOME_OK = "ok"
OUTCOME_NEUTRAL = "neutral"
OUTCOME_FAIL = "fail"

SOURCE_MODELS = "models"
SOURCE_USAGE = "usage"


def canonical_unique_id(kind: str) -> str:
    """Return the canonical ``unique_id`` of an account entity (``account_<kind>``)."""
    return f"{ACCOUNT_UNIQUE_ID_PREFIX}{kind}"


def kind_for_unique_id(unique_id: str | None) -> str | None:
    """Return the account entity kind encoded in a ``unique_id``, if any.

    Recognises both the canonical ``account_<kind>`` shape and the legacy
    ``<workspace_or_prefix>_<kind>`` shape left behind by pre-2.0.0 releases
    (e.g. ``ws1_model_catalog`` or ``abc_models_live_count``). Candidates are
    matched longest-kind-first, so no kind can shadow a longer one.

    Returns ``None`` for anything else - unknown prefixes, foreign unique_ids,
    empty/``None`` input and, importantly, the account *settings* ids
    (``account_auto_update_models``, ``account_models_refresh_minutes``): those
    are controls of the account entry, not catalog entities.
    """
    if not unique_id or not isinstance(unique_id, str):
        return None
    for kind in _KINDS_LONGEST_FIRST:
        # ``account_<kind>`` is also covered by the suffix test below; the
        # explicit equality keeps the canonical contract self-documenting.
        if unique_id == canonical_unique_id(kind) or unique_id.endswith(f"_{kind}"):
            return kind
    return None


def matches_kind(platform: str, unique_id: str | None, kind: str) -> bool:
    """Return whether an entity is the account entity for ``kind``.

    Both halves must hold: the platform has to be the one ``kind`` is published
    on (see ``PLATFORM_BY_KIND``) and the ``unique_id`` has to resolve to
    ``kind`` (canonical or legacy shape). An unknown ``kind`` never matches, so
    a typo cannot silently claim an entity.
    """
    expected_platform = PLATFORM_BY_KIND.get(kind)
    if expected_platform is None:
        return False
    return platform == expected_platform and kind_for_unique_id(unique_id) == kind


@dataclass(frozen=True)
class Evidence:
    """One observed, timestamped fetch outcome of a currently loaded coordinator.

    ``source`` is ``"models"`` (account catalog fetch) or ``"usage"``
    (workspace cycle); ``workspace`` is the workspace name for usage evidence
    and ``None`` for the account's own catalog fetch; ``outcome`` is ``"ok"``,
    ``"neutral"`` (the API answered but proved nothing, e.g. 401/403
    entitlement) or ``"fail"`` (network/5xx/Cloudflare).
    """

    source: str
    workspace: str | None
    outcome: str
    at: datetime


@dataclass(frozen=True)
class Reachability:
    """Resolved API-reachability state plus the reason and evidence counts.

    ``state`` is tri-state: ``True`` (at least one fresh success), ``False``
    (only fresh failures) or ``None`` (unknown - no fresh evidence, or fresh
    evidence without a success).
    """

    state: bool | None
    reason: str
    counts: dict[str, int]


def models_ttl(base_minutes: int | None) -> timedelta:
    """Return the lifetime of models evidence.

    ``MODELS_TTL_FACTOR`` times the account entry's *configured base* interval -
    deliberately not the backoff-extended effective interval, otherwise a
    backing-off integration would keep stale evidence alive indefinitely. With
    auto-refresh off (``base_minutes is None``) there is no interval to scale
    and ``MODELS_TTL_FALLBACK_MINUTES`` applies.
    """
    minutes = (
        MODELS_TTL_FALLBACK_MINUTES
        if base_minutes is None
        else MODELS_TTL_FACTOR * base_minutes
    )
    return timedelta(minutes=minutes)


def usage_ttl(minutes: int) -> timedelta:
    """Return the lifetime of usage evidence (``USAGE_TTL_FACTOR`` x interval)."""
    return timedelta(minutes=USAGE_TTL_FACTOR * minutes)


def _as_utc(value: datetime) -> datetime:
    """Interpret a naive timestamp as UTC.

    Timezone defensiveness (deliberate, documented): a naive ``at``/``now`` must
    never raise ``TypeError`` from a mixed aware/naive comparison - evidence
    resolution runs in the coordinator's publish tick and an exception there
    would take the whole sensor update down. Naive values are therefore assumed
    to be UTC (Home Assistant always hands out aware UTC timestamps, so this
    only covers hand-built/fake data) instead of being dropped as stale:
    dropping would silently flip a live state to "unknown", while the UTC
    assumption is deterministic and keeps the evidence usable.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def resolve_reachability(
    ev: Sequence[Evidence],
    now: datetime,
    models_ttl_: timedelta,
    usage_ttl_: timedelta,
) -> Reachability:
    """Resolve the tri-state reachability from timestamped evidence.

    Each evidence entry is aged against the TTL of *its* source (models ->
    ``models_ttl_``, usage -> ``usage_ttl_``; an unexpected source falls back to
    ``models_ttl_``). Fresh means ``now - at <= ttl``, so the boundary case
    ``age == ttl`` still counts as fresh. Stale evidence is ignored entirely -
    never reinterpreted as a failure.

    Priority is strict (plan §5): any fresh ``ok`` -> ``True``/``fresh_ok``;
    else any fresh ``neutral`` -> ``None``/``answered_no_success``; else only
    fresh ``fail`` -> ``False``/``all_fresh_failed``; else (no fresh evidence at
    all, including an empty sequence) -> ``None``/``no_evidence``.

    ``counts`` documents the tally: ``ok``/``neutral``/``fail`` count *fresh*
    evidence only, ``stale`` counts the ignored entries. An entry with an
    unknown outcome is ignored without being counted anywhere - it must never
    be able to prove either success or failure.
    """
    counts = {OUTCOME_OK: 0, OUTCOME_NEUTRAL: 0, OUTCOME_FAIL: 0, "stale": 0}
    now_utc = _as_utc(now)

    for entry in ev:
        ttl = usage_ttl_ if entry.source == SOURCE_USAGE else models_ttl_
        age = now_utc - _as_utc(entry.at)
        if age > ttl:
            counts["stale"] += 1
            continue
        if entry.outcome in (OUTCOME_OK, OUTCOME_NEUTRAL, OUTCOME_FAIL):
            counts[entry.outcome] += 1

    if counts[OUTCOME_OK]:
        return Reachability(True, "fresh_ok", counts)
    if counts[OUTCOME_NEUTRAL]:
        return Reachability(None, "answered_no_success", counts)
    if counts[OUTCOME_FAIL]:
        return Reachability(False, "all_fresh_failed", counts)
    return Reachability(None, "no_evidence", counts)
