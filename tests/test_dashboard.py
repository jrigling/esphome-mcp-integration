"""Tests for the dashboard client REST surface and result model."""
import asyncio

import aiohttp
import pytest
from aiohttp import web

from esphome_mcp_client import DashboardClient, DashboardError, LogFilter
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


async def test_ws_non_upgrade_gives_actionable_error(serve, session):
    """Device Builder serves the SPA (HTTP 200) for /validate instead of a WS
    upgrade. The client should explain that, not surface a raw aiohttp error."""

    async def spa_fallback(_request):
        return web.Response(text="<html>device builder</html>")

    base = await serve(make_app([("GET", "/validate", spa_fallback)]))
    client = DashboardClient(session, base)
    with pytest.raises(DashboardError) as exc:
        await client.validate("kitchen.yaml", max_seconds=5)
    msg = str(exc.value)
    assert "Device Builder" in msg
    assert "compile" in msg


async def test_ws_immediate_close_gives_actionable_error(serve, session):
    """Through the Supervisor ingress proxy a missing endpoint upgrades and is
    then closed immediately with no output. The client must still explain it,
    not return a silent empty result."""

    async def ws_upgrade_then_close(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()  # the client's spawn frame
        await ws.close()  # ...then close with no line/exit frames
        return ws

    base = await serve(make_app([("GET", "/validate", ws_upgrade_then_close)]))
    client = DashboardClient(session, base)
    with pytest.raises(DashboardError) as exc:
        await client.validate("kitchen.yaml", max_seconds=5)
    msg = str(exc.value)
    assert "Device Builder" in msg
    assert "compile" in msg


async def test_ws_immediate_close_ok_for_supported_endpoint(serve, session):
    """A supported endpoint (/compile) that closes with no output must NOT be
    misreported as missing — it returns an empty result, not an error."""

    async def ws_upgrade_then_close(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()
        await ws.close()
        return ws

    base = await serve(make_app([("GET", "/compile", ws_upgrade_then_close)]))
    client = DashboardClient(session, base)
    result = await client.compile("kitchen.yaml", max_seconds=5)
    assert result.exit_code is None
    assert result.lines == []


async def test_compile_streams_lines_via_on_line(serve, session):
    """Build commands forward each output line to the on_line callback as it
    arrives (used to feed a background job live), and still collect them."""

    async def ws_spawn(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()  # the client's spawn frame
        for line in ("Compiling...", "Linking...", "Done"):
            await ws.send_json({"event": "line", "data": line})
        await ws.send_json({"event": "exit", "code": 0})
        await ws.close()
        return ws

    base = await serve(make_app([("GET", "/compile", ws_spawn)]))
    client = DashboardClient(session, base)
    seen: list[str] = []
    result = await client.compile("kitchen.yaml", max_seconds=5, on_line=seen.append)

    assert result.exit_code == 0
    assert result.lines == ["Compiling...", "Linking...", "Done"]
    assert seen == ["Compiling...", "Linking...", "Done"]


async def test_headers_are_sent_on_rest_and_ws(serve, session):
    """The ingress-session cookie must ride on both REST and WebSocket calls,
    or the Supervisor ingress proxy rejects them."""
    seen: dict[str, str | None] = {}

    async def devices(request):
        seen["rest"] = request.headers.get("Cookie")
        return web.json_response({"configured": [], "importable": []})

    async def ws_spawn(request):
        seen["ws"] = request.headers.get("Cookie")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()
        await ws.send_json({"event": "exit", "code": 0})
        await ws.close()
        return ws

    base = await serve(
        make_app([("GET", "/devices", devices), ("GET", "/compile", ws_spawn)])
    )
    client = DashboardClient(
        session, base, headers={"Cookie": "ingress_session=sess-xyz"}
    )
    await client.list_devices()
    await client.compile("kitchen.yaml", max_seconds=5)

    assert seen["rest"] == "ingress_session=sess-xyz"
    assert seen["ws"] == "ingress_session=sess-xyz"


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


# ---- legacy /logs: filtering and early stop -----------------------------------


def _spawn_logs(lines, *, forever=False, spawned=None):
    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        frame = await ws.receive()
        if spawned is not None:
            spawned.append(frame.json())
        try:
            for line in lines:
                await ws.send_json({"event": "line", "data": line})
            i = 0
            while forever:
                await asyncio.sleep(0.01)
                await ws.send_json({"event": "line", "data": f"[D][tick:001]: {i}"})
                i += 1
        except (ConnectionResetError, RuntimeError):
            pass
        return ws

    return handler


async def test_logs_filters_and_cleans_lines(serve, session):
    lines = [
        r"[10:00:00.001]\033[0;35m[C][logger:250]: Logger:\033[0m",
        r"[10:00:01.000]\033[0;36m[D][wifi:300]: scan\033[0m",
        r"[10:00:02.000]\033[0;32m[I][race:045]: Lap 1\033[0m",
    ]
    spawned: list[dict] = []
    base = await serve(make_app([("GET", "/logs", _spawn_logs(lines, spawned=spawned))]))
    result = await DashboardClient(session, base).logs(
        "race.yaml", max_seconds=0.5, line_filter=LogFilter(min_level=3)
    )
    assert result.lines == ["[10:00:02.000][I][race:045]: Lap 1"]
    assert spawned == [{"type": "spawn", "configuration": "race.yaml", "port": "OTA"}]


async def test_logs_accepts_no_states_and_ignores_it(serve, session):
    spawned: list[dict] = []
    base = await serve(make_app([("GET", "/logs", _spawn_logs(["[I][a:001]: x"], spawned=spawned))]))
    await DashboardClient(session, base).logs("race.yaml", max_seconds=0.2, no_states=True)
    assert "no_states" not in spawned[0]


async def test_logs_stop_event_returns_early(serve, session):
    base = await serve(make_app([("GET", "/logs", _spawn_logs(["[I][race:001]: go"], forever=True))]))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    begin = loop.time()
    result = await DashboardClient(session, base).logs(
        "race.yaml", max_seconds=30, stop_event=stop,
        on_line=lambda line: stop.set() if "go" in line else None,
    )
    assert loop.time() - begin < 5
    assert result.lines[0] == "[I][race:001]: go"
    assert result.truncated is True


async def test_logs_missing_endpoint_still_raises(serve, session):
    async def ws_upgrade_then_close(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.receive()
        await ws.close()
        return ws

    base = await serve(make_app([("GET", "/logs", ws_upgrade_then_close)]))
    with pytest.raises(DashboardError, match="Device Builder"):
        await DashboardClient(session, base).logs("race.yaml", max_seconds=5)
