"""ESPHome MCP Bridge integration.

Registers the ESPHome Builder LLM API so AI agents reaching Home Assistant
through the MCP server can drive a full ESPHome development cycle.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

# Import the functions directly. A bare `from homeassistant.helpers import llm`
# would be shadowed: importing the local `.llm` submodule below rebinds the
# name `llm` in this package namespace to our own module.
from homeassistant.helpers.llm import async_get_apis, async_register_api

from .const import (
    API_ID,
    CONF_ALLOW_EXTRA_FILES,
    DEFAULT_ALLOW_EXTRA_FILES,
    DOMAIN,
)
from .llm import ESPHomeBuilderAPI

_LOGGER = logging.getLogger(__name__)


def _store_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Cache the entry's effective options into hass.data for the file tools.

    Options take precedence over the value captured at install time (data), so
    changes made through the options flow apply without a restart.
    """
    allow_extra = entry.options.get(
        CONF_ALLOW_EXTRA_FILES,
        entry.data.get(CONF_ALLOW_EXTRA_FILES, DEFAULT_ALLOW_EXTRA_FILES),
    )
    hass.data.setdefault(DOMAIN, {})["allow_extra_files"] = bool(allow_extra)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option changes live (the file tools read this on each call)."""
    _store_options(hass, entry)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESPHome MCP Bridge from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    _store_options(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # Skip if already registered. On Home Assistant 2025.1 and earlier,
    # async_register_api returns None (no unregister), so the API persists
    # across reloads; this guard keeps a reload from raising "already
    # registered".
    if not any(api.id == API_ID for api in async_get_apis(hass)):
        # Newer Home Assistant returns an unregister callback; older versions
        # return None. Only wire up unload when we actually got a callback.
        unregister = async_register_api(hass, ESPHomeBuilderAPI(hass))
        if callable(unregister):
            entry.async_on_unload(unregister)
        _LOGGER.info("ESPHome MCP Bridge: registered LLM API '%s'", API_ID)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    On Home Assistant versions whose async_register_api returns an unregister
    callback, it was wired via async_on_unload. Older versions provide no
    unregister hook, so the API stays registered until restart.
    """
    return True
