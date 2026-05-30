"""ESPHome MCP Bridge integration.

Registers the ESPHome Builder LLM API so AI agents reaching Home Assistant
through the MCP server can drive a full ESPHome development cycle.
"""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm
from homeassistant.helpers.typing import ConfigType

from .const import API_ID, DOMAIN
from .llm import ESPHomeBuilderAPI

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the ESPHome Builder LLM API."""
    hass.data.setdefault(DOMAIN, {})
    unregister = llm.async_register_api(hass, ESPHomeBuilderAPI(hass))
    hass.data[DOMAIN]["unregister_api"] = unregister
    _LOGGER.info("ESPHome MCP Bridge: registered LLM API '%s'", API_ID)
    return True
