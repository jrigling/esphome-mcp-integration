"""Tests for the Supervisor client: discovery, ranking, URL resolution."""
import aiohttp
import pytest

from esphome_mcp_client import AddonNotFoundError, SupervisorClient

from .conftest import json_route, make_app

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


async def test_list_esphome_addons_filters_non_esphome(serve, session):
    base = await serve(make_app([("GET", "/addons", json_route(ADDONS_PAYLOAD))]))
    client = SupervisorClient(session, token="tok", base_url=base)
    addons = await client.list_esphome_addons()
    slugs = {a.slug for a in addons}
    assert slugs == {"5c53de3b_esphome", "5c53de3b_esphome-beta"}


async def test_default_slug_prefers_stable_over_beta(serve, session):
    base = await serve(make_app([("GET", "/addons", json_route(ADDONS_PAYLOAD))]))
    client = SupervisorClient(session, token="tok", base_url=base)
    # Stable wins even though it is stopped and beta is running.
    assert await client.async_default_slug() == "5c53de3b_esphome"


async def test_default_slug_raises_when_none_installed(serve, session):
    empty = {"result": "ok", "data": {"addons": []}}
    base = await serve(make_app([("GET", "/addons", json_route(empty))]))
    client = SupervisorClient(session, token="tok", base_url=base)
    with pytest.raises(AddonNotFoundError):
        await client.async_default_slug()


async def test_get_dashboard_base_url_uses_hostname_and_port(serve, session):
    info = {"result": "ok", "data": {"hostname": "5c53de3b-esphome", "ingress_port": 6052}}
    base = await serve(
        make_app([("GET", "/addons/5c53de3b_esphome/info", json_route(info))])
    )
    client = SupervisorClient(session, token="tok", base_url=base)
    url = await client.get_dashboard_base_url("5c53de3b_esphome")
    assert url == "http://5c53de3b-esphome:6052"


async def test_get_dashboard_base_url_falls_back_to_default_port(serve, session):
    info = {"result": "ok", "data": {"hostname": "5c53de3b-esphome", "ingress_port": 0}}
    base = await serve(
        make_app([("GET", "/addons/5c53de3b_esphome/info", json_route(info))])
    )
    client = SupervisorClient(session, token="tok", base_url=base)
    url = await client.get_dashboard_base_url("5c53de3b_esphome")
    assert url == "http://5c53de3b-esphome:6052"
