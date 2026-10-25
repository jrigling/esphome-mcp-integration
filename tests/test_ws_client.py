"""Tests for the multiplexed /ws client against an in-process fake server.

The fake speaks the real Device Builder framing (ServerInfo greeting on connect,
CommandMessage in, Event/Result/Error out) so the client can't drift from the
protocol it targets.
"""
import asyncio

import aiohttp
import pytest
from aiohttp import web

from esphome_mcp_client import DashboardError, LogFilter, WsClient

from .conftest import make_app


@pytest.fixture
async def session():
    async with aiohttp.ClientSession() as s:
        yield s


def _ws_route(dispatch):
    """Build a /ws handler that greets with ServerInfo then dispatches commands.

    ``dispatch(ws, cmd)`` handles one CommandMessage dict; it may send any
    frames and should send the terminal ResultMessage/ErrorMessage itself.
    """

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # ServerInfo greeting (no message_id) — the client must ignore it.
        await ws.send_json({"server_version": "1.6.5", "esphome_version": "2026.7.0",
                            "port": 6052, "requires_auth": False})
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            cmd = msg.json()
            await dispatch(ws, cmd)
        return ws

    return handler


async def _stream_job(ws, cmd, *, lines, exit_code):
    """Emulate submit -> ResultMessage(job); follow_job -> output+result+done."""
    mid = cmd["message_id"]
    if cmd["command"] in ("firmware/compile", "firmware/upload", "firmware/clean"):
        await ws.send_json({"message_id": mid, "result": {"job_id": "job-1"}})
    elif cmd["command"] == "firmware/follow_job":
        for line in lines:
            await ws.send_json({"message_id": mid, "event": "output", "data": line})
        await ws.send_json(
            {"message_id": mid, "event": "result",
             "data": {"status": "done", "exit_code": exit_code, "error": None}}
        )
        await ws.send_json({"message_id": mid, "result": None})


async def test_compile_submits_then_follows_streaming_lines(serve, session):
    async def dispatch(ws, cmd):
        await _stream_job(ws, cmd, lines=["Compiling...", "Linking...", "Done"], exit_code=0)

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    seen: list[str] = []
    result = await client.compile("kitchen.yaml", max_seconds=5, on_line=seen.append)

    assert result.exit_code == 0
    assert result.success is True
    assert result.lines == ["Compiling...", "Linking...", "Done"]
    assert seen == ["Compiling...", "Linking...", "Done"]


async def test_compile_surfaces_nonzero_exit(serve, session):
    async def dispatch(ws, cmd):
        await _stream_job(ws, cmd, lines=["boom"], exit_code=2)

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    result = await client.compile("kitchen.yaml", max_seconds=5)
    assert result.exit_code == 2
    assert result.success is False


async def test_validate_streams_output_and_captures_code(serve, session):
    async def dispatch(ws, cmd):
        mid = cmd["message_id"]
        assert cmd["command"] == "devices/validate"
        for line in ("INFO Reading configuration...", "Configuration is valid!"):
            await ws.send_json({"message_id": mid, "event": "output", "data": line})
        await ws.send_json({"message_id": mid, "event": "result",
                            "data": {"success": True, "code": 0}})
        await ws.send_json({"message_id": mid, "result": None})

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    result = await client.validate("kitchen.yaml", max_seconds=5)
    assert result.exit_code == 0
    assert "Configuration is valid!" in result.lines


async def test_error_message_raises_dashboard_error(serve, session):
    async def dispatch(ws, cmd):
        mid = cmd["message_id"]
        await ws.send_json({"message_id": mid, "error_code": "not_found",
                            "details": "no such configuration"})

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    with pytest.raises(DashboardError) as exc:
        await client.validate("ghost.yaml", max_seconds=5)
    assert "not_found" in str(exc.value)
    assert "no such configuration" in str(exc.value)


async def test_logs_bounded_by_max_lines_marks_truncated(serve, session):
    async def dispatch(ws, cmd):
        mid = cmd["message_id"]
        # Stream more lines than the cap; never send a terminal result.
        for i in range(50):
            await ws.send_json({"message_id": mid, "event": "output", "data": f"log {i}"})

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    result = await client.logs("kitchen.yaml", max_seconds=5, max_lines=10)
    assert result.truncated is True
    assert len(result.lines) == 10


async def test_ignores_serverinfo_and_foreign_message_ids(serve, session):
    async def dispatch(ws, cmd):
        mid = cmd["message_id"]
        # A frame for a different command id must be ignored by the client.
        await ws.send_json({"message_id": "999", "event": "output", "data": "not mine"})
        await ws.send_json({"message_id": mid, "event": "output", "data": "mine"})
        await ws.send_json({"message_id": mid, "event": "result",
                            "data": {"success": True, "code": 0}})
        await ws.send_json({"message_id": mid, "result": None})

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    result = await client.validate("kitchen.yaml", max_seconds=5)
    assert result.lines == ["mine"]


async def test_run_compiles_then_uploads(serve, session):
    calls: list[str] = []

    async def dispatch(ws, cmd):
        calls.append(cmd["command"])
        if cmd["command"] in ("firmware/compile", "firmware/upload"):
            await ws.send_json({"message_id": cmd["message_id"], "result": {"job_id": "j"}})
        elif cmd["command"] == "firmware/follow_job":
            await ws.send_json({"message_id": cmd["message_id"], "event": "output", "data": "ok"})
            await ws.send_json({"message_id": cmd["message_id"], "event": "result",
                                "data": {"exit_code": 0}})
            await ws.send_json({"message_id": cmd["message_id"], "result": None})

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    result = await client.run("kitchen.yaml", "OTA", max_seconds=5)
    assert result.success is True
    assert calls == ["firmware/compile", "firmware/follow_job",
                     "firmware/upload", "firmware/follow_job"]


async def test_run_skips_upload_when_compile_fails(serve, session):
    calls: list[str] = []

    async def dispatch(ws, cmd):
        calls.append(cmd["command"])
        if cmd["command"] in ("firmware/compile", "firmware/upload"):
            await ws.send_json({"message_id": cmd["message_id"], "result": {"job_id": "j"}})
        elif cmd["command"] == "firmware/follow_job":
            await ws.send_json({"message_id": cmd["message_id"], "event": "result",
                                "data": {"exit_code": 1}})
            await ws.send_json({"message_id": cmd["message_id"], "result": None})

    base = await serve(make_app([("GET", "/ws", _ws_route(dispatch))]))
    client = WsClient(session, base)
    result = await client.run("kitchen.yaml", "OTA", max_seconds=5)
    assert result.success is False
    assert "firmware/upload" not in calls


async def test_non_ws_backend_gives_actionable_error(serve, session):
    async def spa(_request):
        return web.Response(text="<html>classic dashboard</html>")

    base = await serve(make_app([("GET", "/ws", spa)]))
    client = WsClient(session, base)
    with pytest.raises(DashboardError) as exc:
        await client.compile("kitchen.yaml", max_seconds=5)
    assert "classic dashboard" in str(exc.value)


def test_ws_url_appends_ws_path():
    client = WsClient(None, "http://supervisor/ingress/tok123")
    assert client._ws_url == "ws://supervisor/ingress/tok123/ws"


# ---- logs: filtering, no_states, early stop -----------------------------------

# What Device Builder relays from `esphome logs`: timestamp first, ESC printed
# as the literal text "\033", a config dump at connect, then runtime logs.
DASH_LOGS = [
    "INFO Starting log output from 192.168.1.50 using esphome API",
    r"[10:00:00.001]\033[0;35m[C][logger:250]: Logger:\033[0m",
    r"[10:00:00.002]\033[0;35m[C][logger:251]:   Max Level: VERY_VERBOSE\033[0m",
    r"[10:00:01.000]\033[0;36m[D][wifi:300]: scan\033[0m",
    r"[10:00:02.000]\033[0;32m[I][race:045]: Lap 1\033[0m",
    r"[10:00:03.000]\033[0;33m[W][esp32_ble_client:088]\033[1;31m[BTU]\033[0;33m: Disconnected\033[0m",
    r"[10:00:04.000]\033[0;32m[I][race:045]: Lap 2\033[0m",
]


def _log_stream(lines, *, seen_args=None, closed=None, then_forever=False):
    async def dispatch(ws, cmd):
        assert cmd["command"] == "devices/logs"
        if seen_args is not None:
            seen_args.append(cmd["args"])
        mid = cmd["message_id"]
        try:
            for line in lines:
                await ws.send_json({"message_id": mid, "event": "output", "data": line})
            i = 0
            while then_forever:
                await asyncio.sleep(0.01)
                await ws.send_json({"message_id": mid, "event": "output",
                                    "data": f"[D][tick:001]: {i}"})
                i += 1
        except (ConnectionResetError, RuntimeError):
            pass
        finally:
            if closed is not None:
                closed.set()

    return dispatch


async def test_logs_without_filter_cleans_but_keeps_everything(serve, session):
    base = await serve(make_app([("GET", "/ws", _ws_route(_log_stream(DASH_LOGS)))]))
    result = await WsClient(session, base).logs("race.yaml", max_seconds=0.5)
    assert len(result.lines) == len(DASH_LOGS)
    assert result.lines[1] == "[10:00:00.001][C][logger:250]: Logger:"
    assert not any("\\033" in line for line in result.lines)


async def test_logs_filter_by_tag_drops_config_dump_and_cleans(serve, session):
    base = await serve(make_app([("GET", "/ws", _ws_route(_log_stream(DASH_LOGS)))]))
    flt = LogFilter(tags=["race", "esp32_ble*"])
    seen: list[str] = []
    result = await WsClient(session, base).logs(
        "race.yaml", max_seconds=0.5, line_filter=flt, on_line=seen.append
    )
    assert result.lines == [
        "INFO Starting log output from 192.168.1.50 using esphome API",
        "[10:00:02.000][I][race:045]: Lap 1",
        "[10:00:03.000][W][esp32_ble_client:088][BTU]: Disconnected",
        "[10:00:04.000][I][race:045]: Lap 2",
    ]
    assert seen == result.lines
    assert flt.lines_received == len(DASH_LOGS)


async def test_logs_max_lines_counts_kept_lines(serve, session):
    noisy = [f"[D][wifi:001]: noise {i}" for i in range(200)] + ["[I][race:001]: hit"]
    base = await serve(make_app([("GET", "/ws", _ws_route(_log_stream(noisy)))]))
    result = await WsClient(session, base).logs(
        "race.yaml", max_seconds=5, max_lines=1, line_filter=LogFilter(search="hit")
    )
    assert result.lines == ["[I][race:001]: hit"]
    assert result.truncated is True


async def test_logs_passes_no_states_only_when_set(serve, session):
    seen_args: list[dict] = []
    base = await serve(make_app([("GET", "/ws", _ws_route(_log_stream([], seen_args=seen_args)))]))
    client = WsClient(session, base)
    await client.logs("race.yaml", max_seconds=0.1)
    await client.logs("race.yaml", "192.168.1.50", max_seconds=0.1, no_states=True)
    assert seen_args == [
        {"configuration": "race.yaml", "port": "OTA"},
        {"configuration": "race.yaml", "port": "192.168.1.50", "no_states": True},
    ]


async def test_logs_stop_event_returns_early_and_closes_stream(serve, session):
    closed = asyncio.Event()
    base = await serve(make_app([("GET", "/ws", _ws_route(
        _log_stream(["[I][race:001]: start"], closed=closed, then_forever=True)))]))
    stop = asyncio.Event()

    def on_line(line):
        if "start" in line:
            stop.set()

    loop = asyncio.get_running_loop()
    begin = loop.time()
    result = await WsClient(session, base).logs(
        "race.yaml", max_seconds=30, stop_event=stop, on_line=on_line
    )
    assert loop.time() - begin < 5
    assert result.lines[0] == "[I][race:001]: start"
    assert result.truncated is True
    # Closing our socket ends the server-side stream.
    await asyncio.wait_for(closed.wait(), 5)
