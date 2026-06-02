"""Home Assistant setup smoke test.

Requires Home Assistant to be installed; auto-skips otherwise, so the normal
unit run is unaffected. Catches the class of bug that plain unit tests miss:
import-time errors against the real HA API and runtime failures in
``async_setup_entry`` (e.g. the local ``llm`` submodule shadowing
``homeassistant.helpers.llm``, which raised AttributeError at setup).
"""
from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from homeassistant.config_entries import ConfigFlow  # noqa: E402
from homeassistant.helpers import llm as ha_llm  # noqa: E402

import custom_components.esphome_mcp_bridge as integration  # noqa: E402
from custom_components.esphome_mcp_bridge.config_flow import (  # noqa: E402
    ESPHomeMCPBridgeConfigFlow,
)
from custom_components.esphome_mcp_bridge.const import API_ID  # noqa: E402
from custom_components.esphome_mcp_bridge.llm import ESPHomeBuilderAPI  # noqa: E402


class FakeHass:
    """Minimal hass stand-in. A plain class (unlike SimpleNamespace, which
    defines __eq__ and is therefore unhashable) works with HA's lru_cache."""

    def __init__(self) -> None:
        self.data: dict = {}


class FakeEntry:
    def __init__(self) -> None:
        self.on_unload: list = []

    def async_on_unload(self, func) -> None:
        self.on_unload.append(func)


def test_api_is_ha_llm_api_subclass() -> None:
    assert issubclass(ESPHomeBuilderAPI, ha_llm.API)


def test_config_flow_is_registered_for_domain() -> None:
    assert issubclass(ESPHomeMCPBridgeConfigFlow, ConfigFlow)
    assert hasattr(ESPHomeMCPBridgeConfigFlow, "async_step_user")


async def test_setup_entry_registers_llm_api() -> None:
    """Run async_setup_entry against the real HA llm helper.

    Catches the submodule-shadowing regression (pre-fix this raised
    AttributeError because ``llm`` resolved to our own module) and the
    async_register_api return-value drift across HA versions.
    """
    hass = FakeHass()
    entry = FakeEntry()

    assert await integration.async_setup_entry(hass, entry) is True

    # The API is registered with the real HA llm registry under our id.
    registered_ids = {api.id for api in ha_llm.async_get_apis(hass)}
    assert API_ID in registered_ids

    assert await integration.async_unload_entry(hass, entry) is True
