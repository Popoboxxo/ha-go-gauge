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
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_WORKSPACE_NAME,
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


class GoGaugeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Ein Workspace = eine Instanz: Name + Token."""

    VERSION = 5

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GoGaugeOptionsFlowHandler(config_entry)


class GoGaugeOptionsFlowHandler(config_entries.OptionsFlow):
    """Refresh-Zyklen + Warnschwelle + Workspace-Umbenennung."""

    def __init__(self, config_entry) -> None:
        self.entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
            CONF_WARN_PERCENT,
            CONF_USAGE_REFRESH_MINUTES,
            DEFAULT_MODELS_REFRESH_MINUTES,
            DEFAULT_USAGE_REFRESH_MINUTES,
        )
        schema = vol.Schema({
            vol.Required("workspace_name",
                         default=self.entry.data.get("workspace_name", "")): str,
            vol.Required(CONF_WARN_PERCENT,
                         default=opts.get(CONF_WARN_PERCENT, DEFAULT_WARN_PERCENT)): int,
            vol.Required(CONF_AUTO_UPDATE_USAGE,
                         default=opts.get(CONF_AUTO_UPDATE_USAGE, True)): bool,
            vol.Required(CONF_USAGE_REFRESH_MINUTES,
                         default=opts.get(CONF_USAGE_REFRESH_MINUTES, DEFAULT_USAGE_REFRESH_MINUTES)): int,
            vol.Required(CONF_AUTO_UPDATE_MODELS,
                         default=opts.get(CONF_AUTO_UPDATE_MODELS, True)): bool,
            vol.Required(CONF_MODELS_REFRESH_MINUTES,
                         default=opts.get(CONF_MODELS_REFRESH_MINUTES, DEFAULT_MODELS_REFRESH_MINUTES)): int,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
