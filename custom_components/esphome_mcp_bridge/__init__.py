"""ESPHome MCP Bridge integration."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import llm

from .llm import ESPHomeBuilderAPI

_LOGGER = logging.getLogger(__name__)

DOMAIN = "esphome_mcp_bridge"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the ESPHome Builder LLM API."""
    llm.async_register_api(hass, ESPHomeBuilderAPI(hass))
    _LOGGER.info("ESPHome MCP Bridge: registered LLM API 'esphome_builder'")
    return True
