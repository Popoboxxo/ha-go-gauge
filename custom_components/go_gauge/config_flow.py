"""Config flow: EIN Workspace pro Integration-Instanz (Name + Token).

Design-Entscheidung (Daniel-Feedback 22.08.): Mehrere Workspaces werden
ueber MEHRERE Instanzen gemanagt - eine pro Workspace, mit sprechendem
Namen. Der Multi-Token-Textarea-Ansatz war verwirrend und funktionierte
in der Praxis nicht sauber.

Ab 2.0.0 gibt es zwei Entry-Arten in derselben Domain: Workspace-Entries
(Nutzung, Token) und genau eine Konto-Entry (Modell-Katalog +
Erreichbarkeit, ohne Token). Die Konto-Entry wird programmatisch ueber den
Import-Flow angelegt; beide Arten haben einen eigenen Options-Flow.
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
    ACCOUNT_ENTRY_UNIQUE_ID,
    ACCOUNT_MODELS_REFRESH_MAX,
    ACCOUNT_MODELS_REFRESH_MIN,
    CONF_AUTO_UPDATE_MODELS,
    CONF_ENTRY_KIND,
    CONF_MODELS_REFRESH_MINUTES,
    DATA_ACCOUNT_KEY,
    DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS,
    DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
    DEFAULT_WARN_PERCENT,
    DOMAIN,
    ENTITY_NAME_ACCOUNT,
    ENTRY_KIND_ACCOUNT,
    ENTRY_KIND_WORKSPACE,
    USER_AGENT,
    conf_entry_kind,
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

    VERSION = 8

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
                        data={
                            "workspace_name": name.strip(),
                            "token": token,
                            # 2.0.0: every entry is explicitly marked, so the
                            # kind is a data fact instead of a guess (the v8
                            # migration backfills this for pre-2.0.0 entries).
                            CONF_ENTRY_KIND: ENTRY_KIND_WORKSPACE,
                        },
                    )

        schema = vol.Schema({
            vol.Required("workspace_name",
                         default=(user_input or {}).get("workspace_name", "")): str,
            vol.Required("token",
                         default=(user_input or {}).get("token", "")): str,
            vol.Required("skip_validation", default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_import(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the singleton account entry (plan §7.1).

        This step is only ever started programmatically via
        ``flow.async_init(DOMAIN, context={"source": "import"})`` from
        :func:`account.async_ensure_account_entry`; it has no ``async_step_user``
        branch and is therefore unreachable from the "add integration" UI.
        Because it runs with ``source=import`` there is no form to show and no
        user interaction to wait for: Home Assistant runs the flow straight
        through to its result (plan §11.3 V1), which is why the caller can await
        ``async_init`` and then assume the entry exists - the returned flow
        result is deliberately not evaluated (``account.py`` only catches
        exceptions).

        Deliberately NO ``_probe_token`` call and NO token: the account entry is
        workspace-independent and auth-free (it never talks to the API by
        itself), so there is nothing to validate and no secret to store - its
        ``data`` stays limited to the ``entry_kind`` marker, which also keeps
        diagnostics/redaction trivial.

        ``unique_id`` is the fixed ``ACCOUNT_ENTRY_UNIQUE_ID`` plus
        ``_abort_if_unique_id_configured()``: Home Assistant enforces
        uniqueness per domain, so a second attempt aborts with
        ``already_configured`` instead of creating a duplicate entry.
        ``user_input`` is accepted for the HA interface but intentionally
        unused - nothing from an import payload may reach the entry.

        The title is the ``ENTITY_NAME_ACCOUNT`` constant (not a translation
        key): config entry titles are stored data, not translatable UI strings,
        and using the same value as the device name keeps both in sync
        regardless of the user's language.
        """
        await self.async_set_unique_id(ACCOUNT_ENTRY_UNIQUE_ID)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=ENTITY_NAME_ACCOUNT,
            data={CONF_ENTRY_KIND: ENTRY_KIND_ACCOUNT},
        )

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
        """Return the options flow matching the entry kind (plan §2.14).

        The account entry owns the model settings (``auto_update_models`` /
        ``models_refresh_minutes``); every workspace entry keeps its usage
        options only. Branching on the persisted ``entry_kind`` marker keeps a
        single integration domain while the two entry types stay independent.
        """
        if conf_entry_kind(config_entry.data) == ENTRY_KIND_ACCOUNT:
            return GoGaugeAccountOptionsFlowHandler(config_entry)
        return GoGaugeOptionsFlowHandler(config_entry)


class GoGaugeAccountOptionsFlowHandler(config_entries.OptionsFlow):
    """Account-wide model settings: auto update + catalog refresh interval.

    The account entry has NO update listener (plan §6.4), so persisting the
    options through the normal options mechanism never reloads the entry. The
    two values are additionally pushed into the live
    :class:`~.account.AccountCoordinator` via its setters *before* they are
    stored, so the change takes effect immediately (timer re-armed, new state
    published) instead of on the next restart.
    """

    def __init__(self, config_entry) -> None:
        self.entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entry point HA calls; renders/persists under ``step_id="account"``.

        HA always calls ``async_step_init`` first for an options flow. Delegating
        to :meth:`async_step_account` keeps the step id - and therefore the
        translation keys under ``options.step.account`` - correct in the UI.
        """
        return await self.async_step_account(user_input)

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show/store exactly the two global account settings."""
        if user_input is not None:
            self._apply_live(user_input)
            # The OptionsFlowManager writes the returned data into
            # ``entry.options`` itself; an extra ``async_update_entry`` here
            # would only duplicate (and potentially suppress) that write.
            return self.async_create_entry(title="", data=user_input)

        opts = self.entry.options
        schema = vol.Schema({
            vol.Required(
                CONF_AUTO_UPDATE_MODELS,
                default=opts.get(
                    CONF_AUTO_UPDATE_MODELS,
                    DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS,
                ),
            ): bool,
            vol.Required(
                CONF_MODELS_REFRESH_MINUTES,
                default=opts.get(
                    CONF_MODELS_REFRESH_MINUTES,
                    DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(
                    min=ACCOUNT_MODELS_REFRESH_MIN,
                    max=ACCOUNT_MODELS_REFRESH_MAX,
                ),
            ),
        })
        return self.async_show_form(step_id="account", data_schema=schema)

    def _account_coordinator(self):
        """Return the live account coordinator, or ``None`` (defensive).

        The runtime is registered under BOTH keys (``DATA_ACCOUNT_KEY`` and
        ``entry_id``, plan §7.3); the entry-id fallback keeps the flow working
        if only one of them ever exists. ``hass.data`` is not guaranteed to be
        a dict (tests, partial startup), so nothing here may raise.
        """
        hass_data = getattr(self.hass, "data", None)
        if not isinstance(hass_data, dict):
            return None
        domain_data = hass_data.get(DOMAIN)
        if not isinstance(domain_data, dict):
            return None
        coordinator = domain_data.get(DATA_ACCOUNT_KEY)
        if coordinator is None:
            coordinator = domain_data.get(getattr(self.entry, "entry_id", None))
        return coordinator

    def _apply_live(self, user_input: dict[str, Any]) -> None:
        """Push the submitted values into the running coordinator (plan §6.4).

        Deliberately no ``async_reload``: the account setters re-arm the fetch
        timer and publish a new snapshot themselves, which is exactly the live
        effect a reload would otherwise force - only without tearing down the
        workspace entities.
        """
        coordinator = self._account_coordinator()
        if coordinator is None:
            return
        coordinator.set_auto_fetch(
            bool(
                user_input.get(
                    CONF_AUTO_UPDATE_MODELS,
                    DEFAULT_ACCOUNT_AUTO_UPDATE_MODELS,
                )
            )
        )
        coordinator.set_fetch_interval(
            int(
                user_input.get(
                    CONF_MODELS_REFRESH_MINUTES,
                    DEFAULT_ACCOUNT_MODELS_REFRESH_MINUTES,
                )
            )
        )


class GoGaugeOptionsFlowHandler(config_entries.OptionsFlow):
    """Workspace options: usage refresh cycles + thresholds + rename.

    The model settings moved to the account entry in 2.0.0 (plan §6.1): a
    workspace has no catalog fetch anymore, so offering those controls here
    would be a setting without effect.
    """

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
            CONF_AUTO_UPDATE_USAGE,
            CONF_PACE_RED_PERCENT,
            CONF_USAGE_REFRESH_MINUTES,
            CONF_WARN_PERCENT,
            DEFAULT_PACE_RED_PERCENT,
            DEFAULT_USAGE_REFRESH_MINUTES,
        )
        usage_minutes_default = opts.get(
            CONF_USAGE_REFRESH_MINUTES, DEFAULT_USAGE_REFRESH_MINUTES
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
        })
        return self.async_show_form(step_id="init", data_schema=schema)
