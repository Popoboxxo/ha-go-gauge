"""Config flow to set up Go Gauge HA via the UI."""
from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_WARN_PERCENT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
)


async def _validate(host: str, port: int) -> tuple[bool, str | None]:
    """Check that a Go monitor answers on /state with the expected shape."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"http://{host}:{port}/state",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        if "usage" not in data or "models" not in data:
            return False, "invalid_payload"
        return True, None
    except Exception:  # noqa: BLE001
        return False, "cannot_connect"


class GoGaugeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            ok, err = await _validate(host, port)
            if ok:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Go Gauge ({host}:{port})",
                    data={CONF_HOST: host, CONF_PORT: port},
                )
            errors["base"] = err or "cannot_connect"

        schema = vol.Schema({
            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
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
            vol.Required(
                CONF_WARN_PERCENT,
                default=self.entry.options.get(CONF_WARN_PERCENT, DEFAULT_WARN_PERCENT),
            ): int,
            vol.Required(
                "scan_interval",
                default=self.entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL),
            ): int,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
