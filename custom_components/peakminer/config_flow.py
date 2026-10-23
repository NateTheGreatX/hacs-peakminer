# custom_components/peakminer/config_flow.py
"""Config flow for PeakMiner integration."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME
import async_timeout

from .const import (
    CONF_API_KEY,
    CONF_PORT,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .coordinator import PeakMinerCoordinator


class PeakMinerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PeakMiner."""

    VERSION = 1

    async def _async_validate(
        self, user_input: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Validate the submitted host/port/api_key against a live miner.

        Returns ``(entry_data, errors)``; ``entry_data`` is ``None`` when the
        miner did not answer cleanly and ``errors`` carries the problem.
        """
        # Empty string API key is treated as "unset" (open API), matching the
        # miner's own docker-compose `${API_KEY:-}` idiom.
        api_key = (user_input.get(CONF_API_KEY) or "").strip() or None
        data = {
            CONF_HOST: user_input[CONF_HOST],
            CONF_PORT: user_input[CONF_PORT],
            CONF_API_KEY: api_key,
        }

        coordinator = PeakMinerCoordinator(
            self.hass,
            user_input[CONF_HOST],
            user_input[CONF_PORT],
            api_key,
        )
        try:
            async with async_timeout.timeout(10):
                await coordinator.async_refresh()
        except Exception:
            return None, {"base": "cannot_connect"}

        if coordinator.data is None:
            return None, {"base": "invalid_host"}

        return data, {}

    async def async_step_user(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="127.0.0.1"): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Optional(CONF_API_KEY): str,
            }
        )

        if user_input is not None:
            data, errors = await self._async_validate(user_input)
            if data is None:
                return self.async_show_form(
                    step_id="user",
                    data_schema=data_schema,
                    errors=errors,
                )

            self.async_set_unique_id(
                f"{user_input['host']}:{user_input['port']}"
            )
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get(
                    CONF_NAME,
                    f"PeakMiner {user_input['host']}:{user_input['port']}",
                ),
                data=data,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: Mapping[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a reconfigure step."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST, default=entry.data.get(CONF_HOST, "127.0.0.1")
                ): str,
                vol.Required(
                    CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)
                ): int,
                vol.Optional(
                    CONF_API_KEY, default=entry.data.get(CONF_API_KEY) or ""
                ): str,
            }
        )

        if user_input is not None:
            data, errors = await self._async_validate(user_input)
            if data is None:
                return self.async_show_form(
                    step_id="reconfigure",
                    data_schema=data_schema,
                    errors=errors,
                )

            # Update this entry in place and reload it, so changing the host /
            # port / API key takes effect without the user having to delete +
            # re-add the integration (which would also drop the entities they
            # already have).
            return self.async_update_reload_and_abort(
                entry,
                data=data,
                unique_id=f"{user_input['host']}:{user_input['port']}",
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors,
        )