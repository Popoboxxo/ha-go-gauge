"""Config flow: EIN Workspace pro Integration-Instanz (Name + Token).

Design-Entscheidung (Daniel-Feedback 22.08.): Mehrere Workspaces werden
ueber MEHRERE Instanzen gemanagt - eine pro Workspace, mit sprechendem
Namen. Der Multi-Token-Textarea-Ansatz war verwirrend und funktionierte
in der Praxis nicht sauber.

Der Modell-Katalog (workspace-unabhaengig) wird nur von der ERSTEN
Instanz als Entities angelegt; weitere Instanzen registrieren ihren
Katalog als 'shared' in hass.data, ohne eigene Katalog-Entities zu
erzeugen (keine Duplikate mehr).
"""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DEFAULT_WARN_PERCENT,
    DOMAIN,
    USER_AGENT,
    token_unique_id,
)

USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


async def _probe_token(hass: HomeAssistant, token: str) -> tuple[bool, str | None]:
    """Live-Check eines Tokens - nur fuer die Rueckmeldung, nicht zwingend."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            USAGE_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Origin": "https://opencode.ai",
                "Authorization": f"Bearer {token}",
            },
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status == 401:
                return False, "invalid_token"
            return True, None
    except Exception:  # noqa: BLE001
        return False, "cannot_connect"


# `domain=` is consumed by HA's ``ConfigFlow.__init_subclass__``. That symbol
# is only visible when homeassistant is type-checked: CI deliberately omits the
# package and the pyproject mypy override skips it locally, so the base
# degrades to ``object`` and the kwarg raises a false ``call-arg`` error there.
# Ignore is scoped to that single, HA-typing-only diagnostic.
class GoGaugeConfigFlow(  # type: ignore[call-arg]
    config_entries.ConfigFlow, domain=DOMAIN
):
    """Ein Workspace = eine Instanz: Name + Token."""

    VERSION = 7

    def __init__(self) -> None:
        """Initialize the flow (needed for the reauth context entry)."""
        super().__init__()
        self._reauth_entry: ConfigEntry | None = None

    def _context_entry(self) -> ConfigEntry | None:
        """Resolve a flow entry from the context on old and new HA versions."""
        entry_id = self.context.get("entry_id")
        if not entry_id:
            return None
        return self.hass.config_entries.async_get_entry(entry_id)

    def _update_entry_and_abort(
        self,
        entry: ConfigEntry,
        *,
        data: dict[str, Any],
        reason: str,
    ) -> ConfigFlowResult:
        """Update and reload an entry, with a compatibility fallback.

        ``unique_id`` is deliberately NOT passed: the reauth path must never
        touch the stable entry/entity identity (iron rule).
        """
        update: dict[str, Any] = {"data": data}
        reload_and_abort = getattr(self, "async_update_reload_and_abort", None)
        if callable(reload_and_abort):
            return reload_and_abort(entry, reason=reason, **update)
        update_and_abort = getattr(self, "async_update_and_abort", None)
        if callable(update_and_abort):
            return update_and_abort(entry, reason=reason, **update)
        self.hass.config_entries.async_update_entry(entry, **update)
        schedule_reload = getattr(self.hass.config_entries, "async_schedule_reload", None)
        if callable(schedule_reload):
            schedule_reload(entry.entry_id)
        return self.async_abort(reason=reason)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        name = (user_input or {}).get("workspace_name", "")
        token = ((user_input or {}).get("token", "") or "").strip()
        skip_validation = bool((user_input or {}).get("skip_validation", False))

        if user_input is not None:
            if not name or not token:
                errors["base"] = "missing_fields"
            else:
                warn = None
                if not skip_validation:
                    ok, err = await _probe_token(self.hass, token)
                    if not ok and err == "invalid_token":
                        errors["base"] = "invalid_token"
                    elif not ok:
                        warn = "cannot_connect_warn"  # Netz-Problem: Speichern erlaubt
                if not errors:
                    # Eindeutige ID pro TOKEN (nicht pro Name) - gleicher Token
                    # zweimal = echter Duplicate-Fall. SHA-256-Hash statt
                    # Klartext-Fragment, damit kein Token-Teil in HA-Storage /
                    # Diagnostics landet (AUDIT-2026-09-04).
                    await self.async_set_unique_id(token_unique_id(token))
                    self._abort_if_unique_id_configured()
                    title = f"Go Gauge {name}"
                    if warn:
                        title += " ⚠️ offline gespeichert"
                    return self.async_create_entry(
                        title=title,
                        data={"workspace_name": name.strip(), "token": token},
                    )

        schema = vol.Schema({
            vol.Required("workspace_name",
                         default=(user_input or {}).get("workspace_name", "")): str,
            vol.Required("token",
                         default=(user_input or {}).get("token", "")): str,
            vol.Required("skip_validation", default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start the reauth flow when HA detects an invalid/expired token."""
        self._reauth_entry = self._context_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a fresh token and validate it before updating ONLY the token.

        ``unique_id`` / ``entry_id`` stay untouched (iron rule): no
        ``async_set_unique_id`` call, no ``unique_id=`` argument - the stored
        token is replaced, nothing else about the identity is re-derived.
        """
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="reauth_failed")
        errors: dict[str, str] = {}
        token = str((user_input or {}).get("token", "") or "").strip()
        if user_input is not None:
            if not token:
                errors["base"] = "missing_fields"
            else:
                ok, err = await _probe_token(self.hass, token)
                if not ok and err == "invalid_token":
                    errors["base"] = "invalid_token"
                elif not ok:
                    errors["base"] = "cannot_connect"
                if not errors:
                    # Preserve all other entry data (e.g. workspace_name); only
                    # the token field changes.
                    return self._update_entry_and_abort(
                        entry,
                        data={**entry.data, "token": token},
                        reason="reauth_successful",
                    )
        schema = vol.Schema({
            vol.Required("token", default=(user_input or {}).get("token", "")): str,
        })
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GoGaugeOptionsFlowHandler(config_entry)


class GoGaugeOptionsFlowHandler(config_entries.OptionsFlow):
    """Refresh-Zyklen + Warnschwelle + Workspace-Umbenennung."""

    def __init__(self, config_entry) -> None:
        self.entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            new_data = {**self.entry.data}
            if user_input.get("workspace_name"):
                new_data["workspace_name"] = user_input["workspace_name"].strip()
            user_input.pop("workspace_name", None)
            # Data (Rename) und Options in EINEM async_update_entry-Aufruf setzen:
            # der OptionsFlowManager ruft nach async_create_entry() intern nochmal
            # async_update_entry(entry, options=...) auf - da die Options hier
            # bereits identisch gesetzt sind, erkennt HA keine Aenderung mehr und
            # der zweite Aufruf feuert den Update-Listener (Reload) nicht erneut.
            self.hass.config_entries.async_update_entry(
                self.entry, data=new_data, options=user_input
            )
            return self.async_create_entry(title="", data=user_input)

        opts = self.entry.options
        from .const import (
            CONF_AUTO_UPDATE_MODELS,
            CONF_AUTO_UPDATE_USAGE,
            CONF_MODELS_REFRESH_MINUTES,
            CONF_PACE_RED_PERCENT,
            CONF_USAGE_REFRESH_MINUTES,
            CONF_WARN_PERCENT,
            DEFAULT_MODELS_REFRESH_MINUTES,
            DEFAULT_PACE_RED_PERCENT,
            DEFAULT_USAGE_REFRESH_MINUTES,
        )
        usage_minutes_default = opts.get(
            CONF_USAGE_REFRESH_MINUTES, DEFAULT_USAGE_REFRESH_MINUTES
        )
        models_minutes_default = opts.get(
            CONF_MODELS_REFRESH_MINUTES, DEFAULT_MODELS_REFRESH_MINUTES
        )
        schema = vol.Schema({
            vol.Required("workspace_name",
                         default=self.entry.data.get("workspace_name", "")): str,
            vol.Required(CONF_WARN_PERCENT,
                         default=opts.get(CONF_WARN_PERCENT, DEFAULT_WARN_PERCENT)): int,
            vol.Required(CONF_PACE_RED_PERCENT,
                         default=opts.get(CONF_PACE_RED_PERCENT, DEFAULT_PACE_RED_PERCENT)): int,
            vol.Required(CONF_AUTO_UPDATE_USAGE,
                         default=opts.get(CONF_AUTO_UPDATE_USAGE, True)): bool,
            vol.Required(CONF_USAGE_REFRESH_MINUTES,
                         default=usage_minutes_default): int,
            vol.Required(CONF_AUTO_UPDATE_MODELS,
                         default=opts.get(CONF_AUTO_UPDATE_MODELS, True)): bool,
            vol.Required(CONF_MODELS_REFRESH_MINUTES,
                         default=models_minutes_default): int,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
