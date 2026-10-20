# custom_components/peakminer/config_flow.py
"""Config flow for PeakMiner integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
import async_timeout

from .const import (
    CONF_PORT,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .coordinator import PeakMinerCoordinator


class PeakMinerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for PeakMiner."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors: dict = {}

        if user_input is not None:
            coordinator = PeakMinerCoordinator(
                self.hass, user_input["host"], user_input["port"]
            )
            try:
                async with async_timeout.timeout(10):
                    await coordinator.async_refresh()
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                if coordinator.data is None:
                    errors["base"] = "invalid_host"
                else:
                    self.async_set_unique_id(
                        f"{user_input['host']}:{user_input['port']}"
                    )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=user_input.get(CONF_NAME, f"PeakMiner {user_input['host']}:{user_input['port']}"),
                        data={"host": user_input["host"], "port": user_input["port"]},
                    )

        data_schema = vol.Schema({
            vol.Required(CONF_HOST, default="127.0.0.1"): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
        })

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
