"""Diagnostics for Go Gauge HA - redacted dump, split by entry kind (2.0.0).

The export must answer "why is nothing arriving" without a follow-up chat - and
since 2.0.0 there are TWO kinds of entries that can be broken independently
(plan §2.13):

* **workspace** entries - usage polling, token-based. Carries the token
  FINGERPRINT (SHA-256, truncated, non-reversible) for correlation, the usage
  coordinator state, the workspace status list and a compact ``account`` block
  that shows whether the shared account runtime is loaded.
* **account** entries - the single workspace-independent catalog/reachability
  runtime. Carries the account settings, the fetch/backoff state, the resolved
  reachability matrix and the last adoption report. It has NO token at all, so
  neither a token field nor a token fingerprint is emitted here.

The reachability block is NOT recomputed in this module: :func:`account.
account_reachability` / its snapshot carrier is the one place that owns the
TTL/evidence rule (``models_ttl_s`` / ``usage_ttl_s`` included), so the export
can never disagree with the ``api_reachable`` binary sensor.

``REDACT_KEYS`` is unchanged from the audited v0.x export.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.loader import async_get_integration

from .const import (
    DATA_ACCOUNT_KEY,
    DATA_ADOPTION_REPORT,
    DOMAIN,
    ENTRY_KIND_ACCOUNT,
    conf_entry_kind,
)

_LOGGER = logging.getLogger(__name__)

REDACT_KEYS = {"token", "tokens", "authorization", "workspace_name"}


def _domain_data(hass: Any) -> dict[str, Any]:
    """Return ``hass.data[DOMAIN]`` as a dict, or an empty dict when unusable.

    Defensive on purpose: the export runs against whatever state the integration
    happens to be in - a ``MagicMock`` in the offline tests, a half-torn-down
    ``hass.data`` during a reload - and must still produce a readable dump
    instead of an exception.
    """
    hass_data = getattr(hass, "data", None)
    if not isinstance(hass_data, dict):
        return {}
    domain_data = hass_data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return {}
    return domain_data


def _reachability_snapshot(hass: Any) -> Any | None:
    """Resolve the reachability once via the shared helper (never raises).

    Imported lazily so this module stays loadable on its own (the offline tests
    import ``diagnostics.py`` without the account runtime). ``None`` only when
    the helper itself is unavailable - callers then omit the block instead of
    inventing a state.
    """
    try:
        from .account import account_reachability_snapshot

        return account_reachability_snapshot(hass)
    except Exception as err:  # noqa: BLE001 - diagnostics must never fail here
        _LOGGER.debug("Go Gauge: reachability export skipped (%s)", err)
        return None


def _reachability_block(snapshot: Any | None) -> dict[str, Any] | None:
    """Render the reachability matrix for the export (TTLs from the snapshot)."""
    if snapshot is None:
        return None
    result = snapshot.reachability
    return {
        "reason": result.reason,
        "state": result.state,
        "counts": dict(result.counts),
        "models_ttl_s": snapshot.models_ttl.total_seconds(),
        "usage_ttl_s": snapshot.usage_ttl.total_seconds(),
    }


def _adoption_block(domain_data: dict[str, Any]) -> dict[str, Any] | None:
    """Serialize the last ``AdoptionReport`` into a JSON-safe dict.

    ``dataclasses.asdict`` turns the report's lists/tuples into plain
    lists, so the export stays serializable by HA's download writer (the
    ``renamed``/``collisions`` pairs become two-element lists - exactly the
    readable form a support case needs). A missing or non-dataclass value
    (older runtime, test double) yields ``None``.
    """
    report = domain_data.get(DATA_ADOPTION_REPORT)
    if isinstance(report, type) or not dataclasses.is_dataclass(report):
        return None
    try:
        # ``is_dataclass`` accepts instances AND classes; the guard above rules
        # out the class case, but mypy keeps the union - hence the narrow ignore.
        return dataclasses.asdict(report)  # type: ignore[arg-type]
    except Exception as err:  # noqa: BLE001 - adoption detail is best effort
        _LOGGER.debug("Go Gauge: adoption report export skipped (%s)", err)
        return None


def _token_fingerprint(entry: ConfigEntry) -> str:
    """Return the non-reversible token fingerprint of a WORKSPACE entry."""
    token = str(entry.data.get("token") or "")
    return (
        hashlib.sha256(token.encode()).hexdigest()[:8]
        if token
        else "KEIN TOKEN HINTERLEGT!"
    )


def _workspace_states(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Render the per-workspace status entries (windows + note)."""
    return [
        {
            "key": ws.get("key"),
            "status": ws.get("status"),
            "note": (ws.get("note") or "")[:120],
            "fetched_at": ws.get("fetched_at"),
            "windows": {
                win: {
                    "percent": ((ws.get("windows") or {}).get(win) or {}).get("percent"),
                    "reset": str(
                        ((ws.get("windows") or {}).get(win) or {}).get("resets_at")
                    ),
                }
                for win in ("5h", "week", "month")
            },
        }
        for ws in data.get("workspaces", [])
    ]


def _account_hints(data: dict[str, Any], reachability: dict[str, Any] | None) -> list[str]:
    """Interpretation hints for the account branch."""
    hints: list[str] = []
    if not data.get("last_models_ok"):
        hints.append(
            "NOCH NIE erfolgreich gefetcht: der Modell-Katalog liegt nicht vor. "
            "Entweder Netz/Cloudflare beim Start oder die Konto-Entry im "
            "SETUP_ERROR. Log auf 'Go Gauge' pruefen."
        )
    if data.get("backoff_active"):
        hints.append(
            f"Backoff aktiv ({data.get('backoff_failures')} consecutive "
            "Fehlschlaege): das naechste Katalog-Fetch wartet laenger. Bei "
            "HTTP 403 ohne Entitlement = Cloudflare-Rate-Limit, Basisintervall "
            "erhoehen."
        )
    if data.get("last_error"):
        hints.append(f"Letzter Katalog-Fehler: {data.get('last_error')}")
    reason = (reachability or {}).get("reason")
    if reason == "no_evidence":
        hints.append(
            "Keine frische Evidenz: seit mehr als der TTL (2x Basisintervall "
            "fuer Modelle, 3x Usage-Intervall fuer Workspaces) gab es keinen "
            "beobachteten Fetch-Ausgang."
        )
    elif reason == "all_fresh_failed":
        hints.append(
            "Nur frische Fehlschlaege: opencode.ai ist aktuell nicht erreichbar "
            "(Netz, 5xx oder Cloudflare)."
        )
    elif reason == "answered_no_success":
        hints.append(
            "Die API hat geantwortet, aber keinen Erfolg belegt (401/kein Abo) "
            "- der Katalog-Fetch ist davon unberuehrt."
        )
    return hints


def _workspace_hints(
    data: dict[str, Any],
    ws_states: list[dict[str, Any]],
    account: dict[str, Any],
) -> list[str]:
    """Interpretation hints for the workspace branch (pre-2.0.0 content kept)."""
    hints: list[str] = []
    if not data.get("fetched_at"):
        hints.append(
            "NOCH NIE erfolgreich abgerufen. Entweder Netz/Cloudflare "
            "beim Start oder Entry im SETUP_ERROR. HA neustarten und "
            "Log auf 'Go Gauge' pruefen."
        )
    for ws in ws_states:
        if ws["status"] == "no_subscription":
            hints.append(
                f"{ws['key']}: Token gueltig, aber Workspace hat KEIN "
                "aktives Abo -> in der OpenCode-UI Abo reaktivieren "
                "oder andere Tokens verwenden."
            )
        if ws["status"] == "error":
            hints.append(
                f"{ws['key']}: Abruffehler ({ws['note']}). Bei 'HTTP 403' "
                "ohne EntitlementError = Cloudflare-Rate-Limit, "
                "Intervall erhoehen."
            )
    if not account["loaded"]:
        hints.append(
            "Konto-Entry nicht geladen: Modell-Katalog, API-Reachability und "
            "die Konto-Einstellungen fehlen. Konto-Entry unter Eintraege "
            "pruefen/neu laden."
        )
    return hints


def _account_diagnostics(
    hass: Any,
    entry: ConfigEntry,
    coordinator: Any,
    integration: Any,
    domain_data: dict[str, Any],
) -> dict[str, Any]:
    """Diagnostics of an ACCOUNT entry - no token, no token fingerprint."""
    data = coordinator.data or {}
    snapshot = _reachability_snapshot(hass)
    reachability = _reachability_block(snapshot)

    return {
        "integration_version": integration.version,
        "config_entry_version": entry.version,
        "entry_state": str(entry.state),
        "entry_kind": ENTRY_KIND_ACCOUNT,
        "options": async_redact_data(dict(entry.options), REDACT_KEYS),
        "coordinator": {
            "auto_fetch": coordinator.auto_fetch,
            "base_interval_minutes": coordinator.base_interval_minutes,
            "fetch_interval_minutes": coordinator.fetch_interval_minutes,
            "backoff_active": data.get("backoff_active"),
            "backoff_failures": data.get("backoff_failures"),
            "last_models_attempt": data.get("last_models_attempt"),
            "last_models_ok": data.get("last_models_ok"),
            "last_models_outcome": data.get("last_models_outcome"),
            "last_error": data.get("last_error"),
        },
        "reachability": reachability,
        "adoption": _adoption_block(domain_data),
        "interpretation_hints": _account_hints(data, reachability),
    }


def _workspace_diagnostics(
    hass: Any,
    entry: ConfigEntry,
    coordinator: Any,
    integration: Any,
    domain_data: dict[str, Any],
) -> dict[str, Any]:
    """Diagnostics of a WORKSPACE entry (usage polling + account runtime view)."""
    data = coordinator.data or {}
    ws_states = _workspace_states(data)

    # The account runtime is shared, not owned by this workspace (no owner flag
    # since 2.0.0): read it from the foreign ``hass.data`` key, defensively.
    account_coordinator = domain_data.get(DATA_ACCOUNT_KEY)
    snapshot = _reachability_snapshot(hass)
    account = {
        "loaded": account_coordinator is not None,
        "entry_id": getattr(
            getattr(account_coordinator, "entry", None), "entry_id", None
        ),
        "auto_fetch": (
            getattr(account_coordinator, "auto_fetch", None)
            if account_coordinator is not None
            else None
        ),
        "base_interval_minutes": (
            getattr(account_coordinator, "base_interval_minutes", None)
            if account_coordinator is not None
            else None
        ),
        "reachability_reason": (
            snapshot.reachability.reason if snapshot is not None else None
        ),
    }

    return {
        "integration_version": integration.version,
        "config_entry_version": entry.version,
        "entry_state": str(entry.state),
        "entry_kind": conf_entry_kind(entry.data),
        "token_fingerprint": _token_fingerprint(entry),
        "workspace_name": async_redact_data(
            dict(entry.data), REDACT_KEYS
        ).get("workspace_name"),
        "options": async_redact_data(dict(entry.options), REDACT_KEYS),
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "auto_usage": coordinator.auto_usage,
            "usage_refresh_minutes": coordinator.usage_minutes,
            "last_usage_fetch": data.get("last_usage_fetch"),
            "last_usage_ok": data.get("last_usage_ok"),
            "last_usage_attempt": data.get("last_usage_attempt"),
            "last_usage_outcome": data.get("last_usage_outcome"),
        },
        "account": account,
        "workspaces": async_redact_data(ws_states, REDACT_KEYS),
        "interpretation_hints": _workspace_hints(data, ws_states, account),
    }


async def async_get_config_entry_diagnostics(
    hass, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the diagnostics of THIS entry kind (plan §2.13)."""
    kind = conf_entry_kind(getattr(entry, "data", None))
    domain_data = _domain_data(hass)
    coordinator = domain_data.get(entry.entry_id)

    if coordinator is None:
        # Branch-specific failure hint: an account entry has no token to check,
        # so the workspace wording would send the user down the wrong path.
        if kind == ENTRY_KIND_ACCOUNT:
            error = (
                "Konto-Coordinator nicht geladen - Setup ist fehlgeschlagen! "
                "Log pruefen: 'Go Gauge' filtern."
            )
        else:
            error = (
                "Coordinator nicht geladen - Setup ist fehlgeschlagen! "
                "Log pruefen: 'Go Gauge' filtern."
            )
        return {
            "error": error,
            "entry_state": str(entry.state),
            "entry_kind": kind,
        }

    integration = await async_get_integration(hass, DOMAIN)

    if kind == ENTRY_KIND_ACCOUNT:
        return _account_diagnostics(
            hass, entry, coordinator, integration, domain_data
        )
    return _workspace_diagnostics(
        hass, entry, coordinator, integration, domain_data
    )
