"""Config flow: Go Gauge talks DIRECTLY to opencode.ai - only tokens needed."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_WARN_PERCENT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
    USER_AGENT,
)

MODELS_URL = "https://opencode.ai/zen/go/v1/models"
USAGE_URL = "https://opencode.ai/zen/go/v1/usage"


async def _validate_token(hass, token: str) -> tuple[bool, str | None]:
    """A token is valid when /usage answers 200 with a usage block."""
    session = async_get_clientsession(hass)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Origin": "https://opencode.ai",
        "Authorization": f"Bearer {token}",
    }
    try:
        async with session.get(USAGE_URL, headers=headers,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 401:
                return False, "invalid_token"
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            if "usage" not in data:
                return False, "invalid_token"
            return True, None
    except Exception:  # noqa: BLE001
        return False, "cannot_connect"


class GoGaugeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Token-based setup (direct API access, no monitor needed)."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            # Tokens: eine pro Zeile; Leerzeilen/Whitespace ignorieren
            raw = user_input.get("tokens", "") or ""
            tokens = [t.strip() for t in raw.replace(";", "\n").splitlines() if t.strip()]
            if not tokens:
                errors["base"] = "no_tokens"
            else:
                # Ersten Token validieren (alle kommen vom selben Account)
                ok, err = await _validate_token(self.hass, tokens[0])
                if ok:
                    await self.async_set_unique_id(
                        "opencode_go_" + tokens[0][:8].lower())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Go Gauge ({len(tokens)} Workspace(s))",
                        data={"tokens": tokens},
                    )
                errors["base"] = err or "invalid_token"

        schema = vol.Schema({
            vol.Required("tokens", default=user_input.get("tokens", "") if user_input else ""): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return GoGaugeOptionsFlowHandler(config_entry)


class GoGaugeOptionsFlowHandler(config_entries.OptionsFlow):
    """Adjust warning threshold and poll interval without restart."""

    def __init__(self, config_entry) -> None:
        self.entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        schema = vol.Schema({
            vol.Required(CONF_WARN_PERCENT,
                         default=self.entry.options.get(CONF_WARN_PERCENT, DEFAULT_WARN_PERCENT)): int,
            vol.Required("scan_interval",
                         default=self.entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)): int,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
