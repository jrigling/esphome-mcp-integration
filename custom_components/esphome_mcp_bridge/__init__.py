"""ESPHome MCP Bridge integration.

Registers the ESPHome Builder LLM API so AI agents reaching Home Assistant
through the MCP server can drive a full ESPHome development cycle.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .const import API_ID, DOMAIN
from .llm import ESPHomeBuilderAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESPHome MCP Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # async_register_api returns an unregister callback; tying it to the entry
    # means the LLM API is cleanly removed if the integration is unloaded.
    unregister = llm.async_register_api(hass, ESPHomeBuilderAPI(hass))
    entry.async_on_unload(unregister)

    _LOGGER.info("ESPHome MCP Bridge: registered LLM API '%s'", API_ID)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry (LLM API is unregistered via async_on_unload)."""
    return True
