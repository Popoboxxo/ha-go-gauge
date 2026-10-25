"""Go Gauge HA - binary sensors (rate-limit flags, subscription, reachability).

Two entry kinds (2.0.0, plan §2.10):

* workspace -> the per-window rate-limit flags and the subscription sensor;
* account   -> exactly ONE ``ApiReachableBinarySensor`` (unique_id
  ``account_api_reachable``) that resolves the API state from timestamped
  evidence (``account.collect_evidence``) instead of the old
  ``last_update_success`` + ``fetched_at`` heuristic.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .account_logic import canonical_unique_id
from .const import (
    DOMAIN,
    ENTRY_KIND_ACCOUNT,
    WINDOW_LABELS,
    conf_entry_kind,
)
from .coordinator import GoGaugeCoordinator
from .entity import GoGaugeAccountEntityBase, GoGaugeEntityBase

if TYPE_CHECKING:  # no runtime import: this module must stay loadable on its own
    from .account import AccountCoordinator, ReachabilitySnapshot

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the binary sensors of THIS entry kind (plan §2.10)."""
    if conf_entry_kind(entry.data) == ENTRY_KIND_ACCOUNT:
        account_coordinator: AccountCoordinator = hass.data[DOMAIN][entry.entry_id]
        async_add_entities([ApiReachableBinarySensor(account_coordinator, entry)])
        return

    coordinator: GoGaugeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for ws in coordinator.data.get("workspaces", []):
        for win in ("5h", "week", "month"):
            entities.append(RateLimitedBinarySensor(
                coordinator, entry, key=ws["key"], win=win))
        # Abo-Status je Workspace (403 EntitlementError -> no_subscription)
        entities.append(SubscriptionActiveBinarySensor(
            coordinator, entry, key=ws["key"]))
    async_add_entities(entities)


class RateLimitedBinarySensor(GoGaugeEntityBase, BinarySensorEntity):
    """ON when OpenCode reports rate-limited for this workspace window."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:block-helper"
    _attr_translation_key = "rate_limited"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str, win: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._win = win
        self._attr_unique_id = f"{entry.entry_id}_{key}_{win}_limited"
        label = WINDOW_LABELS.get(win, win)
        self._attr_translation_placeholders = {"window": label}

    @property
    def is_on(self) -> bool | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        blk = (ws.get("windows") or {}).get(self._win) or {}
        return blk.get("status") == "rate-limited"

    @property
    def available(self) -> bool:
        """Nicht verfuegbar (unavailable), wenn kein Abo oder Update fehlschlaegt.

        Respektiert zusaetzlich den Coordinator-Update-Erfolg (super().available),
        damit veraltete Daten nach einem fehlgeschlagenen Refresh nicht faelschlich
        als verfuegbar angezeigt werden - nicht einfach OFF.
        """
        ws = self._ws(self._key)
        return super().available and not (ws and ws.get("status") == "no_subscription")


class SubscriptionActiveBinarySensor(GoGaugeEntityBase, BinarySensorEntity):
    """ON = Workspace hat ein aktives Abo (API liefert Nutzungsdaten).

    OFF + Attribut 'note' wenn 403 EntitlementError (kein aktives Abo).

    KEIN device_class (Fix 2026-09-25): CONNECTIVITY veranlasst HA, den
    Zustand als "Getrennt" zu rendern. Das ist doppelt falsch - die API ist
    in diesem Fall erreichbar, es fehlt das Abo. Zwei-Fronten-Verwirrung:
    der Nutzer las "Getrennt" und hielt die API fuer tot (falscher Diagnose-
    Weg, siehe auch den neuen ApiStatusSensor). Ein Abo ist ein Zustand der
    Subskription, nicht der Verbindung - der einzige connectivity-Sensor
    der Instanz ist ApiReachableBinarySensor.
    """

    _attr_icon = "mdi:shield-check-outline"
    _attr_translation_key = "subscription_active"

    def __init__(self, coordinator: GoGaugeCoordinator, entry: ConfigEntry, *,
                 key: str) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}_subscription_active"

    @property
    def is_on(self) -> bool | None:
        ws = self._ws(self._key)
        if not ws:
            return None
        if ws.get("status") == "no_subscription":
            return False
        if ws.get("status") == "ok":
            return True
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ws = self._ws(self._key)
        return {"workspace_key": self._key, "note": (ws or {}).get("note")}


class ApiReachableBinarySensor(GoGaugeAccountEntityBase, BinarySensorEntity):
    """ON while opencode.ai delivers FRESH evidence (plan §5).

    The state is tri-state on purpose: ``True`` = at least one fresh success,
    ``False`` = only fresh failures, ``None`` (unknown) = no fresh evidence or
    only "answered but proved nothing" evidence (401/no subscription). Stale
    evidence is ignored, never reinterpreted as failure.

    Evidence comes from :func:`account.collect_evidence` - the attempt/outcome
    timestamps every loaded coordinator records. This replaces the pre-2.0.0
    heuristic ``last_update_success and fetched_at``, which could not tell a
    stale-but-successful catalog from a fresh API outage.

    ``is_on`` and ``extra_state_attributes`` both delegate to
    :func:`account.account_reachability_snapshot`, so the TTL rule and the
    evidence matrix exist exactly once (the diagnostics export uses the same
    helper). ``is_on`` additionally CACHES that snapshot for the attribute path:
    HA writes state and attributes in the same update, and computing them twice
    with two different ``now`` values could make the state and its attributes
    disagree at a TTL edge (review fix). An attribute read without a preceding
    state write recomputes defensively.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "api_reachable"

    def __init__(self, coordinator: AccountCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = canonical_unique_id("api_reachable")
        # One snapshot per state write - filled in ``is_on``, reused by
        # ``extra_state_attributes`` (see the class docstring).
        self._reachability_snapshot: ReachabilitySnapshot | None = None

    @property
    def is_on(self) -> bool | None:
        """Tri-state reachability from the shared matrix (single TTL site).

        Stores the snapshot for the attribute read of the same state write.
        """
        from .account import account_reachability_snapshot  # local: no cycle

        snapshot = account_reachability_snapshot(self.hass)
        self._reachability_snapshot = snapshot
        return snapshot.reachability.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Reason, evidence rows and the TTLs used for THIS resolution."""
        from .account import account_reachability_snapshot

        snapshot = self._reachability_snapshot
        if snapshot is None:
            # Defensive: an attribute read without a preceding ``is_on`` (e.g. a
            # template querying the attributes directly).
            snapshot = account_reachability_snapshot(self.hass)
        return {
            "reachability_reason": snapshot.reachability.reason,
            "evidence": snapshot.evidence_attributes(),
            "models_ttl_s": snapshot.models_ttl.total_seconds(),
            "usage_ttl_s": snapshot.usage_ttl.total_seconds(),
            "checked_at": snapshot.now.isoformat(),
        }
