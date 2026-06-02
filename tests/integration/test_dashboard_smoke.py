"""Smoke tests exercising the real ESPHome dashboard protocol.

If any of these fail in CI, the ESPHome dashboard/Device Builder API has likely
drifted from the legacy contract our client targets.
"""
from __future__ import annotations

import aiohttp
import pytest

from esphome_mcp_client import DashboardClient

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(dashboard: str):
    async with aiohttp.ClientSession() as session:
        yield DashboardClient(session, dashboard)


async def test_version(client: DashboardClient) -> None:
    version = await client.version()
    assert version and version[0].isdigit()


async def test_inventory_lists_smoke_device(client: DashboardClient) -> None:
    inventory = await client.inventory()
    configs = {dev["configuration"] for dev in inventory}
    assert "smoke.yaml" in configs


async def test_write_then_read_config(client: DashboardClient) -> None:
    marker = "# edited-by-smoke-test\n"
    await client.write_config(
        "smoke.yaml", "esphome:\n  name: smoke\nesp32:\n  board: esp32dev\n" + marker
    )
    assert marker.strip() in await client.get_config("smoke.yaml")


async def test_json_config(client: DashboardClient) -> None:
    config = await client.get_json_config("smoke.yaml")
    assert "esphome" in config


async def test_validate_ws_spawn_protocol(client: DashboardClient) -> None:
    """The WebSocket spawn protocol: lines stream, then an exit frame."""
    result = await client.validate("smoke.yaml", max_seconds=120)
    assert result.exit_code == 0
    assert result.truncated is False
    assert "valid" in result.output.lower()


@pytest.mark.compile
async def test_compile_ws_spawn_protocol(client: DashboardClient) -> None:
    """Full compile - slow, needs the platform toolchain. Run explicitly."""
    result = await client.compile("smoke.yaml", max_seconds=900)
    assert result.exit_code == 0
