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
from custom_components.esphome_mcp_bridge.const import (  # noqa: E402
    API_ID,
    CONF_ALLOW_EXTRA_FILES,
    DOMAIN,
)
from custom_components.esphome_mcp_bridge.llm import ESPHomeBuilderAPI  # noqa: E402


class FakeHass:
    """Minimal hass stand-in. A plain class (unlike SimpleNamespace, which
    defines __eq__ and is therefore unhashable) works with HA's lru_cache."""

    def __init__(self) -> None:
        self.data: dict = {}


class FakeEntry:
    """Minimal ConfigEntry stand-in modelling the surface async_setup_entry
    touches: data/options dicts, on-unload registration, and update-listener
    registration (which on a real entry returns an unsubscribe callable)."""

    def __init__(self, data: dict | None = None, options: dict | None = None) -> None:
        self.data: dict = data or {}
        self.options: dict = options or {}
        self.on_unload: list = []
        self.update_listeners: list = []

    def async_on_unload(self, func) -> None:
        self.on_unload.append(func)

    def add_update_listener(self, listener):
        self.update_listeners.append(listener)

        def _remove() -> None:
            self.update_listeners.remove(listener)

        return _remove


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


async def test_setup_caches_extra_files_option() -> None:
    """The extra-file-access option is cached into hass.data for the file tools,
    defaulting off and overridable via options."""
    hass = FakeHass()
    await integration.async_setup_entry(hass, FakeEntry())
    assert hass.data[DOMAIN]["allow_extra_files"] is False

    hass_on = FakeHass()
    await integration.async_setup_entry(
        hass_on, FakeEntry(options={CONF_ALLOW_EXTRA_FILES: True})
    )
    assert hass_on.data[DOMAIN]["allow_extra_files"] is True
