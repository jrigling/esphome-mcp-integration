"""Tests for the Supervisor client: discovery, ranking, URL resolution."""
import aiohttp
import pytest
from aioresponses import aioresponses

from esphome_mcp_client import AddonNotFoundError, SupervisorClient

ADDONS_PAYLOAD = {
    "result": "ok",
    "data": {
        "addons": [
            {"slug": "5c53de3b_esphome-beta", "name": "ESPHome (beta)", "version": "2026.5.0b1", "state": "started"},
            {"slug": "5c53de3b_esphome", "name": "ESPHome", "version": "2026.4.0", "state": "stopped"},
            {"slug": "core_mosquitto", "name": "Mosquitto broker", "version": "6.5", "state": "started"},
        ]
    },
}


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


async def test_list_esphome_addons_filters_non_esphome(session):
    with aioresponses() as m:
        m.get("http://supervisor/addons", payload=ADDONS_PAYLOAD)
        client = SupervisorClient(session, token="tok")
        addons = await client.list_esphome_addons()
    slugs = {a.slug for a in addons}
    assert slugs == {"5c53de3b_esphome", "5c53de3b_esphome-beta"}


async def test_default_slug_prefers_stable_over_beta(session):
    with aioresponses() as m:
        m.get("http://supervisor/addons", payload=ADDONS_PAYLOAD)
        client = SupervisorClient(session, token="tok")
        slug = await client.async_default_slug()
    # Stable wins even though it is stopped and beta is running.
    assert slug == "5c53de3b_esphome"


async def test_default_slug_raises_when_none_installed(session):
    with aioresponses() as m:
        m.get("http://supervisor/addons", payload={"result": "ok", "data": {"addons": []}})
        client = SupervisorClient(session, token="tok")
        with pytest.raises(AddonNotFoundError):
            await client.async_default_slug()


async def test_get_dashboard_base_url_uses_hostname_and_port(session):
    info = {
        "result": "ok",
        "data": {"hostname": "5c53de3b-esphome", "ingress_port": 6052},
    }
    with aioresponses() as m:
        m.get("http://supervisor/addons/5c53de3b_esphome/info", payload=info)
        client = SupervisorClient(session, token="tok")
        url = await client.get_dashboard_base_url("5c53de3b_esphome")
    assert url == "http://5c53de3b-esphome:6052"


async def test_get_dashboard_base_url_falls_back_to_default_port(session):
    info = {"result": "ok", "data": {"hostname": "5c53de3b-esphome", "ingress_port": 0}}
    with aioresponses() as m:
        m.get("http://supervisor/addons/5c53de3b_esphome/info", payload=info)
        client = SupervisorClient(session, token="tok")
        url = await client.get_dashboard_base_url("5c53de3b_esphome")
    assert url == "http://5c53de3b-esphome:6052"
