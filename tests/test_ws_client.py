"""Tests for the multiplexed /ws client against an in-process fake server.

The fake speaks the real Device Builder framing (ServerInfo greeting on connect,
CommandMessage in, Event/Result/Error out) so the client can't drift from the
protocol it targets.
"""
import aiohttp
import pytest
from aiohttp import web

from esphome_mcp_client import DashboardError, WsClient

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
