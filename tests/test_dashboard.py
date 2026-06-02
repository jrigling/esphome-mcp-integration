"""Tests for the dashboard client REST surface and result model."""
import aiohttp
import pytest

from esphome_mcp_client import DashboardClient
from esphome_mcp_client.dashboard import CommandResult

from .conftest import json_route, make_app, status_route, text_route


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


async def test_inventory_merges_ping_online_status(serve, session):
    devices = {
        "configured": [
            {
                "name": "kitchen",
                "configuration": "kitchen.yaml",
                "current_version": "2026.4.0",
                "target_platform": "ESP32",
                "loaded_integrations": ["wifi", "api"],
            },
            {"name": "garage", "configuration": "garage.yaml"},
        ],
        "importable": [],
    }
    ping = {"kitchen.yaml": True, "garage.yaml": False}
    base = await serve(
        make_app(
            [
                ("GET", "/devices", json_route(devices)),
                ("GET", "/ping", json_route(ping)),
            ]
        )
    )
    client = DashboardClient(session, base)
    inv = await client.inventory()

    by_name = {d["name"]: d for d in inv}
    assert by_name["kitchen"]["online"] is True
    assert by_name["kitchen"]["current_version"] == "2026.4.0"
    assert by_name["garage"]["online"] is False


async def test_inventory_tolerates_missing_ping_endpoint(serve, session):
    devices = {"configured": [{"name": "kitchen", "configuration": "kitchen.yaml"}]}
    base = await serve(
        make_app(
            [
                ("GET", "/devices", json_route(devices)),
                ("GET", "/ping", status_route(404)),
            ]
        )
    )
    client = DashboardClient(session, base)
    inv = await client.inventory()
    assert inv[0]["online"] is None


async def test_inventory_warns_on_unexpected_shape(serve, session, caplog):
    # Protocol drift: /devices no longer returns a 'configured' key.
    base = await serve(
        make_app(
            [
                ("GET", "/devices", json_route({"items": []})),
                ("GET", "/ping", json_route({})),
            ]
        )
    )
    client = DashboardClient(session, base)
    inv = await client.inventory()
    assert inv == []
    assert "may have changed" in caplog.text


async def test_version_returns_value(serve, session):
    base = await serve(
        make_app([("GET", "/version", json_route({"version": "2026.4.0"}))])
    )
    client = DashboardClient(session, base)
    assert await client.version() == "2026.4.0"


async def test_version_tolerates_missing_endpoint(serve, session):
    base = await serve(make_app([("GET", "/version", status_route(404))]))
    client = DashboardClient(session, base)
    assert await client.version() is None


async def test_get_config_returns_text(serve, session):
    base = await serve(
        make_app([("GET", "/edit", text_route("esphome:\n  name: kitchen\n"))])
    )
    client = DashboardClient(session, base)
    text = await client.get_config("kitchen.yaml")
    assert "name: kitchen" in text


def test_command_result_success_and_output():
    ok = CommandResult(lines=["compiling", "done"], exit_code=0)
    assert ok.success is True
    assert ok.output == "compiling\ndone"

    failed = CommandResult(lines=["boom"], exit_code=1, truncated=True)
    assert failed.success is False
    assert failed.truncated is True


def test_ws_base_derived_from_http_base():
    client = DashboardClient(None, "http://host:6052")
    assert client._ws_base == "ws://host:6052"
