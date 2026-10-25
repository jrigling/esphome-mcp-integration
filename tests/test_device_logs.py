"""Tests for direct-to-device log streaming (aioesphomeapi).

``FakeClient`` mirrors the real ``APIClient.subscribe_logs`` signature and
records what it was asked for: an earlier fake accepted (and ignored) any
level, which let a subscription at ``LOG_LEVEL_NONE`` - an always-empty capture
- go unnoticed (#10). The last tests drive the real aioesphomeapi request
builder to pin the protobuf fields actually sent.
"""
import asyncio

import pytest

from esphome_mcp_client import DeviceLogError, LogFilter, async_stream_device_logs
from esphome_mcp_client.logtext import LEVELS

ESC = "\x1b"


class FakeMsg:
    def __init__(self, message):
        self.message = message


class FakeClient:
    """Stands in for aioesphomeapi.APIClient.

    Canned messages are delivered on the event loop after ``subscribe_logs``
    returns (as a real connection would), ``interval`` seconds apart.
    """

    def __init__(self, logs=(), *, fail_connect=False, interval=0.0):
        self._logs = list(logs)
        self._interval = interval
        self.fail_connect = fail_connect
        self.connected = False
        self.disconnected = False
        self.unsubscribed = False
        self.subscribe_args = None
        self._task = None

    async def connect(self, login=False):
        if self.fail_connect:
            raise RuntimeError("connection refused")
        self.connected = True

    def subscribe_logs(self, on_log, log_level=None, dump_config=None):
        self.subscribe_args = {"log_level": log_level, "dump_config": dump_config}

        async def _deliver():
            for msg in self._logs:
                await asyncio.sleep(self._interval)
                if self.unsubscribed:
                    return
                on_log(msg)

        self._task = asyncio.ensure_future(_deliver())

        def _unsub():
            self.unsubscribed = True

        return _unsub

    async def disconnect(self):
        self.disconnected = True


def dev(level_letter, tag, msg):
    return FakeMsg(f"{ESC}[0;36m[{level_letter}][{tag}:001]: {msg}{ESC}[0m".encode())


async def capture(client, **kwargs):
    kwargs.setdefault("max_seconds", 0.05)
    return await async_stream_device_logs("1.2.3.4", client_factory=lambda: client, **kwargs)


# ---- the #10 regression: what we ask the device for ---------------------------


async def test_subscribes_at_very_verbose_without_config_dump_by_default():
    client = FakeClient()
    await capture(client)
    assert client.subscribe_args == {"log_level": LEVELS["very_verbose"], "dump_config": False}
    # Never the protocol default 0 (LOG_LEVEL_NONE), which yields an empty capture.
    assert client.subscribe_args["log_level"] != 0


async def test_filter_level_is_requested_from_the_device():
    client = FakeClient()
    await capture(client, line_filter=LogFilter(min_level=LEVELS["warn"]))
    assert client.subscribe_args["log_level"] == LEVELS["warn"]


async def test_config_dump_requested_only_when_included():
    client = FakeClient()
    await capture(client, line_filter=LogFilter(include_config_dump=True))
    assert client.subscribe_args["dump_config"] is True


# ---- capture behaviour --------------------------------------------------------


async def test_collects_cleaned_lines_and_disconnects():
    client = FakeClient(logs=[FakeMsg(b"line1\nline2\n"), dev("D", "race", "lap")])
    result = await capture(client, max_lines=10)
    assert result.lines == ["line1", "line2", "[D][race:001]: lap"]
    assert result.success is True
    assert result.truncated is True  # the window elapsed
    assert client.disconnected is True
    assert client.unsubscribed is True


async def test_filter_applies_and_max_lines_counts_kept_lines():
    logs = [dev("D", "wifi", f"noise {i}") for i in range(20)]
    logs += [dev("I", "race", f"lap {i}") for i in range(5)]
    client = FakeClient(logs=logs)
    result = await capture(client, max_seconds=5, max_lines=3, line_filter=LogFilter(tags=["race"]))
    assert result.lines == [f"[I][race:001]: lap {i}" for i in range(3)]
    assert result.truncated is True


async def test_max_lines_returns_before_the_window_ends():
    client = FakeClient(logs=[dev("D", "t", str(i)) for i in range(10)])
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await capture(client, max_seconds=30, max_lines=3)
    assert loop.time() - start < 5
    assert len(result.lines) == 3


async def test_stop_event_ends_capture_early_and_keeps_lines():
    client = FakeClient(logs=[dev("D", "t", str(i)) for i in range(100)], interval=0.01)
    stop = asyncio.Event()
    seen: list[str] = []

    def on_line(line):
        seen.append(line)
        if len(seen) == 3:
            stop.set()

    result = await capture(client, max_seconds=30, stop_event=stop, on_line=on_line)
    assert 3 <= len(result.lines) < 100
    assert result.lines[:3] == seen[:3]
    assert result.truncated is True
    assert client.unsubscribed is True
    assert client.disconnected is True


async def test_on_line_sees_only_kept_lines():
    client = FakeClient(logs=[dev("D", "wifi", "x"), dev("D", "race", "y")])
    seen: list[str] = []
    await capture(client, line_filter=LogFilter(tags=["race"]), on_line=seen.append)
    assert seen == ["[D][race:001]: y"]


async def test_empty_capture_still_succeeds():
    result = await capture(FakeClient())
    assert result.lines == []
    assert result.success is True


async def test_connect_failure_raises_and_still_disconnects():
    client = FakeClient(fail_connect=True)
    with pytest.raises(DeviceLogError, match="connection refused"):
        await capture(client)
    assert client.disconnected is True


async def test_handles_str_messages():
    result = await capture(FakeClient(logs=[FakeMsg("plain string line")]))
    assert "plain string line" in result.lines


# ---- against the real aioesphomeapi request builder ---------------------------


def _real_client_capturing_requests(sent):
    aioesphomeapi = pytest.importorskip("aioesphomeapi")

    class FakeConnection:
        def send_message_callback_response(self, req, on_message, msg_types):
            sent.append((req, on_message, msg_types))
            return lambda: None

    client = aioesphomeapi.APIClient("1.2.3.4", 6053, None)
    client._get_connection = lambda: FakeConnection()

    async def _noop(*args, **kwargs):
        return None

    client.connect = _noop
    client.disconnect = _noop
    return client


async def test_real_subscribe_request_asks_for_logs():
    from aioesphomeapi import LogLevel

    sent = []
    client = _real_client_capturing_requests(sent)
    await capture(client)
    (req, _, _), = sent
    assert req.level == LogLevel.LOG_LEVEL_VERY_VERBOSE
    assert req.dump_config is False


async def test_real_subscribe_request_with_level_and_config_dump():
    from aioesphomeapi import LogLevel

    sent = []
    client = _real_client_capturing_requests(sent)
    await capture(client, line_filter=LogFilter(min_level=LEVELS["info"], include_config_dump=True))
    (req, _, _), = sent
    assert req.level == LogLevel.LOG_LEVEL_INFO
    assert req.dump_config is True


async def test_real_response_messages_are_decoded():
    from aioesphomeapi.api_pb2 import SubscribeLogsResponse

    sent = []
    client = _real_client_capturing_requests(sent)
    task = asyncio.ensure_future(capture(client, max_seconds=0.2))
    while not sent:
        await asyncio.sleep(0.01)
    _, on_message, _ = sent[0]
    on_message(SubscribeLogsResponse(message=f"{ESC}[0;32m[I][race:045]: Lap 3{ESC}[0m".encode()))
    result = await task
    assert result.lines == ["[I][race:045]: Lap 3"]
