"""Config flow: token setup (works offline) + refresh-cycle options."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_AUTO_UPDATE_MODELS,
    CONF_AUTO_UPDATE_USAGE,
    CONF_MODELS_REFRESH_MINUTES,
    CONF_WARN_PERCENT,
    CONF_USAGE_REFRESH_MINUTES,
    DEFAULT_MODELS_REFRESH_MINUTES,
    DEFAULT_USAGE_REFRESH_MINUTES,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
    USER_AGENT,
)

USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


async def _probe_token(hass, token: str) -> tuple[bool, str | None]:
    """Live-Check eines Tokens - nur fuer die Rueckmeldung, nicht zwingend."""
    session = aiohttp.ClientSession()
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
    finally:
        await session.close()


class GoGaugeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Token-Setup. Funktioniert auch, wenn opencode.ai gerade nicht erreichbar ist."""

    VERSION = 3

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        tokens_raw = (user_input or {}).get("tokens", "")
        skip_validation = bool((user_input or {}).get("skip_validation", False))

        if user_input is not None:
            tokens = [t.strip() for t in tokens_raw.replace(";", "\n").splitlines() if t.strip()]
            if not tokens:
                errors["base"] = "no_tokens"
            else:
                warn = None
                if not skip_validation:
                    ok, err = await _probe_token(self.hass, tokens[0])
                    if not ok and err == "invalid_token":
                        # Eindeutig falscher Token -> nur mit Skip weitermachbar
                        errors["base"] = "invalid_token"
                    elif not ok:
                        # Netz/Cloudflare-Problem -> Warnung, Speichern erlaubt
                        warn = "cannot_connect_warn"
                if not errors:
                    await self.async_set_unique_id("opencode_go_" + tokens[0][:8].lower())
                    self._abort_if_unique_id_configured()
                    title = f"Go Gauge ({len(tokens)} Workspace(s))"
                    if warn:
                        title += " ⚠️ offline gespeichert"
                    return self.async_create_entry(title=title, data={"tokens": tokens})

        schema = vol.Schema({
            vol.Required("tokens", default=tokens_raw): str,
            vol.Required("skip_validation", default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GoGaugeOptionsFlowHandler(config_entry)


class GoGaugeOptionsFlowHandler(config_entries.OptionsFlow):
    """Refresh-Zyklen: Usage + Modelle getrennt, je ein/aus + Minuten."""

    def __init__(self, config_entry) -> None:
        self.entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        opts = self.entry.options
        schema = vol.Schema({
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
