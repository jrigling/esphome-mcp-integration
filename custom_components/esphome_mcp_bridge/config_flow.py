"""Config flow for the ESPHome MCP Bridge integration.

The integration mainly just registers an LLM API, so this is a single-instance
flow with one optional setting: whether the file tools may edit files other than
top-level YAML (e.g. C++ in a custom ``components/`` directory). That setting can
be changed later from the integration's options. secrets.yaml is always
protected regardless of this setting.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import API_NAME, CONF_ALLOW_EXTRA_FILES, DEFAULT_ALLOW_EXTRA_FILES, DOMAIN


class ESPHomeMCPBridgeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow for ESPHome MCP Bridge."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: confirm and create a single entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(
                title=API_NAME,
                data={
                    CONF_ALLOW_EXTRA_FILES: user_input.get(
                        CONF_ALLOW_EXTRA_FILES, DEFAULT_ALLOW_EXTRA_FILES
                    )
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_ALLOW_EXTRA_FILES,
                        default=DEFAULT_ALLOW_EXTRA_FILES,
                    ): bool,
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ESPHomeMCPBridgeOptionsFlow:
        """Return the options flow handler."""
        return ESPHomeMCPBridgeOptionsFlow(config_entry)


class ESPHomeMCPBridgeOptionsFlow(OptionsFlow):
    """Let the user toggle extra file access after installation."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        # Stored under a private name to avoid the deprecated practice of
        # assigning self.config_entry (the framework provides it on newer cores).
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(
            CONF_ALLOW_EXTRA_FILES,
            self._entry.data.get(CONF_ALLOW_EXTRA_FILES, DEFAULT_ALLOW_EXTRA_FILES),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_ALLOW_EXTRA_FILES, default=current): bool,
                }
            ),
        )
